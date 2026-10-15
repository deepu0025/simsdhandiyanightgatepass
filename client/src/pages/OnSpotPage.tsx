import { useEffect, useRef, useState, type FormEvent } from 'react';
import { CheckCircle2, LogOut, UserPlus } from 'lucide-react';
import { Link } from 'react-router-dom';
import { api, type EventRecord, type SheetSyncStatus, type TicketRecord, type User } from '../api';

type CreatedResult = {
  registration_id: string;
  ticket: TicketRecord;
  issued: boolean;
  pdf_url: string | null;
  replayed: boolean;
  sheet_sync_status: SheetSyncStatus;
};

export default function OnSpotPage({ user, onSignOut }: { user?: User; onSignOut?: () => void } = {}) {
  const [events, setEvents] = useState<EventRecord[]>([]);
  const [eventId, setEventId] = useState('');
  const [paymentMethod, setPaymentMethod] = useState<'CASH' | 'CARD' | 'UPI'>('CASH');
  const [result, setResult] = useState<CreatedResult | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const idempotencyKey = useRef('');

  useEffect(() => {
    api.events().then((items) => {
      const active = items.filter((event) => event.is_active);
      setEvents(active);
      if (active.length === 1) setEventId(active[0].id);
    }).catch((reason: unknown) => setError(reason instanceof Error ? reason.message : 'Could not load events.'));
  }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError('');
    const form = new FormData(event.currentTarget);
    if (!idempotencyKey.current) idempotencyKey.current = crypto.randomUUID();
    try {
      const created = await api.createOnSpotRegistration({
        event_id: eventId,
        attendee_name: String(form.get('attendee_name') || '').trim(),
        attendee_email: String(form.get('attendee_email') || '').trim(),
        attendee_phone: String(form.get('attendee_phone') || '').trim(),
        ticket_type: String(form.get('ticket_type') || '').trim(),
        amount: String(form.get('amount') || ''),
        payment_method: paymentMethod,
        transaction_reference: String(form.get('transaction_reference') || '').trim(),
      }, idempotencyKey.current);
      setResult(created);
      if (!created.replayed) idempotencyKey.current = '';
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not create the registration.');
    } finally {
      setBusy(false);
    }
  }

  function startNext() {
    setResult(null);
    setError('');
    idempotencyKey.current = '';
    (document.getElementById('onspot-form') as HTMLFormElement | null)?.reset();
  }

  return (
    <div className={user?.role === 'DESK' ? 'min-h-screen bg-ink px-3 pb-8 pt-4 sm:px-6 sm:pt-5' : ''}>
      {user?.role === 'DESK' && <header className="mx-auto flex max-w-4xl items-center justify-between gap-3 text-white">
        <div className="min-w-0">
          <p className="text-[0.65rem] font-semibold tracking-[0.2em] text-emerald-300">GATEHOUSE · REGISTRATION</p>
          <h1 className="mt-1 text-xl font-semibold">On-spot desk</h1>
          <p className="mt-1 truncate text-xs text-slate-400">{user.name || user.email}</p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {user.desk && <span className="rounded-full bg-emerald-400 px-3 py-1.5 text-xs font-bold text-ink">{user.desk.replace('_', ' ')}</span>}
          <button type="button" aria-label="Sign out" onClick={onSignOut} className="scanner-icon"><LogOut size={19} /></button>
        </div>
      </header>}
      <div className={`mx-auto max-w-4xl ${user?.role === 'DESK' ? 'mt-4 rounded-2xl bg-white p-3 sm:mt-6 sm:rounded-3xl sm:p-6' : ''}`}>
      <p className="text-sm font-medium text-accent">Ticket operations</p>
      <h1 className="mt-2 text-3xl font-semibold tracking-tight text-ink">On-spot registration</h1>
      <p className="mt-2 text-sm leading-relaxed text-slate-500">Record the walk-up attendee and confirm payment at the desk. Saving issues the ticket and checks the attendee in immediately; the registration and check-in details are sent to the OnSpot_Tickets sheet.</p>
      {events.length === 0 && <p className="mt-5 rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">{user?.role === 'DESK' ? 'There are no active events yet. Please contact the administrator.' : <>There are no active events. <Link to="/events" className="font-semibold underline">Create an event in Gatehouse</Link> first.</>}</p>}
      {!result && <form id="onspot-form" onSubmit={submit} className="mt-6 rounded-2xl border border-slate-200/80 bg-white p-4 sm:mt-8 sm:p-8">
        <label className="block text-sm font-medium text-slate-700">Event
          <select required value={eventId} onChange={(event) => setEventId(event.target.value)} className="field mt-2">
            <option value="">Select active event</option>{events.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.venue}</option>)}
          </select>
        </label>
        <div className="mt-5 grid gap-5 sm:grid-cols-2">
          <label className="text-sm font-medium text-slate-700">Attendee name<input required name="attendee_name" maxLength={160} className="field mt-2" autoComplete="name" /></label>
          <label className="text-sm font-medium text-slate-700">Email<input required type="email" name="attendee_email" className="field mt-2" autoComplete="email" /></label>
          <label className="text-sm font-medium text-slate-700">Phone<input name="attendee_phone" maxLength={32} className="field mt-2" autoComplete="tel" /></label>
          <label className="text-sm font-medium text-slate-700">Ticket type<input required name="ticket_type" maxLength={120} className="field mt-2" placeholder="General admission" /></label>
          <label className="text-sm font-medium text-slate-700">Amount<input required name="amount" type="number" min="0" step="0.01" className="field mt-2" placeholder="0.00" /></label>
          <label className="text-sm font-medium text-slate-700">Payment method
            <select value={paymentMethod} onChange={(event) => setPaymentMethod(event.target.value as 'CASH' | 'CARD' | 'UPI')} className="field mt-2">
              <option value="CASH">Cash</option><option value="CARD">Card</option><option value="UPI">UPI</option>
            </select>
          </label>
          {(paymentMethod === 'UPI' || paymentMethod === 'CARD') && <label className="text-sm font-medium text-slate-700 sm:col-span-2">Transaction reference {paymentMethod === 'UPI' && <span className="text-red-600">*</span>}<input required={paymentMethod === 'UPI'} name="transaction_reference" maxLength={120} className="field mt-2" /></label>}
        </div>
        <p className="mt-6 rounded-xl bg-amber-50 p-4 text-sm leading-relaxed text-amber-900">Confirm that payment has been received before saving. This marks payment verified, issues the ticket, and checks the attendee in at this desk. The payment method, transaction reference, desk, and timestamps are recorded for the spreadsheet.</p>
        {error && <p role="alert" className="mt-4 rounded-xl bg-red-50 p-4 text-sm text-red-700">{error}</p>}
        <button disabled={busy || !eventId} className="mt-6 flex w-full items-center justify-center gap-2 rounded-xl bg-accent px-5 py-3.5 font-semibold text-white hover:bg-ink disabled:opacity-50"><UserPlus size={18} />{busy ? 'Saving registration…' : 'Save registration'}</button>
      </form>}

      {result && <section className="mt-8 rounded-2xl border border-slate-200/80 bg-white p-6 sm:p-8">
        <div className={`flex items-start gap-3 rounded-xl p-4 ${result.issued ? 'bg-emerald-50 text-emerald-900' : 'bg-amber-50 text-amber-900'}`}>
          <CheckCircle2 size={21} className="mt-0.5 shrink-0" />
          <div><h2 className="font-semibold">{result.ticket.checked_in_at ? 'Payment verified — attendee checked in' : result.issued ? 'Ticket issued' : 'Registration saved'}</h2><p className="mt-1 text-sm">{result.ticket.checked_in_at ? `Checked in at ${new Date(result.ticket.checked_in_at).toLocaleString()}${result.ticket.registration_desk ? ` · ${result.ticket.registration_desk.replace('_', ' ')}` : ''}.` : 'The ticket is ready.'} {result.sheet_sync_status === 'SUCCEEDED' ? 'All registration and check-in details were saved to the OnSpot_Tickets sheet.' : result.sheet_sync_status === 'NOT_CONFIGURED' ? 'Google Sheets is not configured; this registration is saved in Gatehouse only.' : result.sheet_sync_status === 'FAILED' ? 'Sheet sync failed; this registration is saved in Gatehouse. Check the Sheets connection and sync status.' : 'Registration details are queued for the OnSpot_Tickets sheet.'}</p></div>
        </div>
        <dl className="mt-6 grid gap-x-8 gap-y-4 sm:grid-cols-2"><div><dt className="text-xs uppercase tracking-wide text-slate-500">Ticket</dt><dd className="mt-1 font-semibold text-ink">{result.ticket.ticket_number}</dd></div><div><dt className="text-xs uppercase tracking-wide text-slate-500">Ticket ID · use as sheet ticket_id</dt><dd className="mt-1 break-all font-mono text-xs text-ink">{result.ticket.id}</dd></div><div><dt className="text-xs uppercase tracking-wide text-slate-500">Attendee</dt><dd className="mt-1 font-semibold text-ink">{result.ticket.attendee_name}</dd></div><div><dt className="text-xs uppercase tracking-wide text-slate-500">Email</dt><dd className="mt-1 text-ink">{result.ticket.attendee_email}</dd></div><div><dt className="text-xs uppercase tracking-wide text-slate-500">Ticket state · payment</dt><dd className="mt-1 text-ink">{result.ticket.status} · {result.ticket.payment_status}</dd></div><div><dt className="text-xs uppercase tracking-wide text-slate-500">Check-in time · desk</dt><dd className="mt-1 text-ink">{result.ticket.checked_in_at ? new Date(result.ticket.checked_in_at).toLocaleString() : '—'} · {result.ticket.registration_desk?.replace('_', ' ') || '—'}</dd></div></dl>
        {error && <p role="alert" className="mt-4 rounded-xl bg-red-50 p-4 text-sm text-red-700">{error}</p>}
        <div className="mt-6 flex flex-wrap gap-3">
          <button onClick={startNext} className="rounded-xl border border-slate-200 px-4 py-3 text-sm font-semibold text-slate-700">New registration</button>
        </div>
      </section>}
      </div>
    </div>
  );
}
