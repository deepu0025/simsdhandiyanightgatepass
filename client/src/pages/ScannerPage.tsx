import { useCallback, useEffect, useRef, useState } from 'react';
import { Html5Qrcode, type Html5QrcodeCameraScanConfig } from 'html5-qrcode';
import { Camera, CameraOff, CheckCircle2, LogOut, RotateCcw, Volume2, VolumeX, XCircle } from 'lucide-react';
import type { User } from '../api';
import { api } from '../api';

type ScanResult = { result: string; attendee_name?: string | null; ticket_number?: string | null; ticket_type?: string | null; checked_in_at?: string | null };

export default function ScannerPage({ user, onSignOut }: { user: User; onSignOut: () => void }) {
  const scannerRef = useRef<Html5Qrcode | null>(null);
  const busyRef = useRef(false);
  const lastSubmittedCredentialRef = useRef('');
  const [running, setRunning] = useState(false);
  const [cameras, setCameras] = useState<Array<{ id: string; label: string }>>([]);
  const [camera, setCamera] = useState('');
  const [muted, setMuted] = useState(false);
  const [result, setResult] = useState<ScanResult | null>(null);
  const [pendingCredential, setPendingCredential] = useState('');
  const [checkingIn, setCheckingIn] = useState(false);
  const [history, setHistory] = useState<Array<Record<string, string>>>([]);
  const [error, setError] = useState('');

  useEffect(() => {
    api.scanHistory().then(setHistory).catch((reason: unknown) => {
      setError(reason instanceof Error ? reason.message : 'Unable to load scan history.');
    });
    Html5Qrcode.getCameras()
      .then((list) => {
        setCameras(list);
        setCamera((current) => current || list[0]?.id || '');
      })
      .catch(() => setError('Camera permission is needed to scan. You can still use a compatible scanner in manual mode.'));
    return () => {
      if (scannerRef.current?.isScanning) void scannerRef.current.stop();
    };
  }, []);

  const submitCredential = useCallback(async (credential: string) => {
    const normalized = String(credential ?? '').trim();
    if (!normalized || normalized.length > 256) {
      setError('Invalid QR code. Please scan a valid Gatehouse ticket.');
      return;
    }
    if (busyRef.current || lastSubmittedCredentialRef.current === normalized) return;
    busyRef.current = true;
    lastSubmittedCredentialRef.current = normalized;
    setError('');
    try {
      const data = await api.validateScan(normalized);
      setResult(data);
      setPendingCredential(data.result === 'VALID' ? normalized : '');
      if (scannerRef.current?.isScanning) await scannerRef.current.stop();
      setRunning(false);
    } catch (reason) {
      lastSubmittedCredentialRef.current = '';
      setResult({ result: 'SERVER_UNAVAILABLE' });
      setError(reason instanceof Error ? reason.message : 'Scan not confirmed by server.');
    } finally {
      busyRef.current = false;
    }
  }, []);

  async function start() {
    if (!camera) {
      setError('No camera is available. Check the browser camera permission and reload.');
      return;
    }
    setError('');
    const scanner = scannerRef.current ?? new Html5Qrcode('ticket-reader');
    scannerRef.current = scanner;
    try {
      const config: Html5QrcodeCameraScanConfig = { fps: 10, qrbox: { width: 250, height: 250 } };
      await scanner.start(camera, config, (decoded) => void submitCredential(decoded), () => undefined);
      setRunning(true);
    } catch {
      setError('Could not start the camera. Allow camera access and try again.');
    }
  }

  async function stop() {
    if (scannerRef.current?.isScanning) await scannerRef.current.stop();
    setRunning(false);
  }

  async function confirmCheckIn() {
    if (!pendingCredential || checkingIn) return;
    setCheckingIn(true);
    setError('');
    try {
      const data = await api.checkInScan(pendingCredential);
      setResult(data);
      setPendingCredential('');
      if (!muted && data.result === 'ACCEPTED') {
        const AudioContextClass = window.AudioContext;
        if (AudioContextClass) {
          const context = new AudioContextClass();
          const oscillator = context.createOscillator();
          const gain = context.createGain();
          oscillator.connect(gain);
          gain.connect(context.destination);
          oscillator.frequency.value = 740;
          gain.gain.value = 0.08;
          oscillator.start();
          oscillator.stop(context.currentTime + 0.12);
          void context.close();
        }
      }
      try {
        setHistory(await api.scanHistory());
      } catch (reason) {
        setError(reason instanceof Error ? `Check-in was recorded, but history could not be refreshed: ${reason.message}` : 'Check-in was recorded, but history could not be refreshed.');
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Check-in was not confirmed by the server.');
    } finally {
      setCheckingIn(false);
    }
  }

  async function scanNextTicket() {
    lastSubmittedCredentialRef.current = '';
    setPendingCredential('');
    setResult(null);
    setError('');
    await start();
  }

  const success = result?.result === 'ACCEPTED';
  const valid = result?.result === 'VALID';
  const duplicate = result?.result === 'ALREADY_USED';
  const unknown = result?.result === 'INVALID';
  const ineligible = result?.result === 'INELIGIBLE' || result?.result === 'PAYMENT_NOT_VERIFIED';
  const cancelled = result?.result === 'CANCELLED';

  return (
    <main className="min-h-screen overflow-x-hidden bg-ink px-3 pb-8 pt-4 text-white sm:px-6 sm:pt-5">
      <header className="mx-auto flex max-w-5xl items-center justify-between gap-3">
        <div className="min-w-0"><p className="text-[0.65rem] font-semibold tracking-[0.2em] text-emerald-300">GATEHOUSE · ENTRY</p><h1 className="mt-1 text-xl font-semibold">Scanner station</h1><p className="mt-1 truncate text-xs text-slate-400">{user.name || user.email}</p></div>
        <div className="flex gap-2"><button aria-label={muted ? 'Enable sound' : 'Mute sound'} onClick={() => setMuted(!muted)} className="scanner-icon">{muted ? <VolumeX size={19} /> : <Volume2 size={19} />}</button><button aria-label="Sign out" onClick={onSignOut} className="scanner-icon"><LogOut size={19} /></button></div>
      </header>
      <div className="mx-auto mt-4 grid min-w-0 max-w-5xl gap-4 lg:mt-6 lg:grid-cols-[minmax(0,1fr)_300px] lg:gap-5">
        <section className="min-w-0 rounded-2xl bg-white p-3 text-ink sm:rounded-3xl sm:p-6">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div><h2 className="font-semibold">Scan a ticket</h2><p className="mt-1 text-sm text-slate-500">Hold the QR code inside the frame.</p></div>
            {user.gate && <span className="rounded-full bg-emerald-50 px-3 py-1 text-xs font-bold tracking-wide text-emerald-800">{user.gate.replace('_', ' ')}</span>}
            <select aria-label="Select camera" disabled={running} value={camera} onChange={(event) => setCamera(event.target.value)} className="max-w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700">{cameras.map((item) => <option key={item.id} value={item.id}>{item.label || 'Camera'}</option>)}</select>
          </div>
          <div className="relative mt-4 overflow-hidden rounded-xl bg-black sm:rounded-2xl">
            <div id="ticket-reader" className="min-h-[260px] sm:min-h-[380px]" />
            {!running && <div className="pointer-events-none absolute inset-0 grid place-items-center bg-black/60 text-center"><div><CameraOff size={32} className="mx-auto text-white/70" /><p className="mt-3 text-sm text-white/80">Camera paused</p></div></div>}
          </div>
          <div className="mt-3 grid grid-cols-2 gap-2 sm:mt-4 sm:gap-3">
            <button onClick={() => void start()} disabled={running || Boolean(pendingCredential) || checkingIn} className="flex min-h-12 items-center justify-center gap-2 rounded-xl bg-accent px-4 font-medium text-white disabled:opacity-50"><Camera size={18} />Start camera</button>
            <button onClick={() => void stop()} disabled={!running} className="flex min-h-12 items-center justify-center gap-2 rounded-xl bg-slate-100 px-4 font-medium text-slate-700 disabled:opacity-50"><RotateCcw size={18} />Stop / restart</button>
          </div>
          {error && <p role="alert" className="mt-3 rounded-xl bg-red-50 p-3 text-sm text-red-700">{error}</p>}
        </section>
        <aside className="space-y-5">
          <section className={`rounded-3xl p-5 ${result ? success || valid ? 'bg-emerald-400 text-ink' : 'bg-red-400 text-ink' : 'bg-white/10 text-white'}`}>
            <div className="flex items-center gap-3">
              {result ? success || valid ? <CheckCircle2 size={25} /> : <XCircle size={25} /> : <Camera size={25} />}
              <div><p className="text-xs font-semibold uppercase tracking-wide">{result ? success ? 'Check-in confirmed' : result.result === 'VALID' ? 'Ticket valid · awaiting check-in' : duplicate ? 'Already checked in' : ineligible ? 'Payment not verified' : cancelled ? 'Cancelled / revoked' : unknown ? 'Invalid ticket' : 'Scan not confirmed' : 'Ready at the gate'}</p><p className="mt-1 text-sm opacity-80">{result?.ticket_number || (result ? 'Ask attendee to contact event staff.' : 'Start the camera to begin.')}</p></div>
            </div>
            {result?.attendee_name && <div className="mt-5 border-t border-current/15 pt-4"><p className="text-lg font-semibold">{result.attendee_name}</p><p className="mt-1 text-sm opacity-80">{result.ticket_type}</p>{duplicate && result.checked_in_at && <p className="mt-2 text-xs">First check-in: {new Date(result.checked_in_at).toLocaleString()}</p>}</div>}
            {pendingCredential && <button type="button" disabled={checkingIn} onClick={() => void confirmCheckIn()} className="mt-5 min-h-14 w-full rounded-xl bg-ink px-4 text-base font-bold text-white shadow-lg disabled:opacity-60">{checkingIn ? 'Checking in…' : 'Check in'}</button>}
            {result && !pendingCredential && !running && <button type="button" onClick={() => void scanNextTicket()} className="mt-5 min-h-12 w-full rounded-xl bg-white px-4 font-semibold text-ink shadow-lg">Scan next ticket</button>}
          </section>
          <section className="rounded-3xl bg-white/10 p-5">
            <div className="flex items-center justify-between"><h2 className="font-semibold">Recent scans</h2><span className="text-xs text-slate-400">This account</span></div>
            <div className="mt-4 space-y-3">
              {history.slice(0, 8).map((item, index) => <div key={`${item.id || index}`} className="flex justify-between gap-3 border-t border-white/10 pt-3 text-sm"><span className="truncate">{item.ticket_number || 'Unrecognized code'}</span><span className={item.result === 'ACCEPTED' ? 'text-emerald-300' : 'text-amber-300'}>{item.result}</span></div>)}
              {history.length === 0 && <p className="text-sm text-slate-400">No scans recorded yet.</p>}
            </div>
          </section>
        </aside>
      </div>
    </main>
  );
}
