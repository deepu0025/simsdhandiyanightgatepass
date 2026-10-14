import { useEffect, useRef, useState, type FormEvent } from 'react';
import { CheckCircle2, Download, LogOut, Mail, UserPlus } from 'lucide-react';
import { Link } from 'react-router-dom';
import { api, type EventRecord, type TicketRecord, type User } from '../api';

type CreatedResult = {
  registration_id: string;
  ticket: TicketRecord;
  issued: boolean;
  pdf_url: string | null;
  replayed: boolean;
};

export default function OnSpotPage({ user, onSignOut }: { user?: User; onSignOut?: () => void } = {}) {
  const [events, setEvents] = useState<EventRecord[]>([]);
  const [eventId, setEventId] = useState('');
  const [paymentMethod, setPaymentMethod] = useState<'CASH' | 'CARD' | 'UPI'>('CASH');
  const [result, setResult] = useState<CreatedResult | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [emailBusy, setEmailBusy] = useState(false);
  const [emailStatus, setEmailStatus] = useState('');
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

  async function sendEmail() {
    if (!result?.issued) return;
    setEmailBusy(true);
    setError('');
    try {
      const response = await api.sendTicketEmail(result.ticket.id);
      setEmailStatus(response.email_status);
      setResult({ ...result, ticket: { ...result.ticket, email_status: response.email_status } });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Ticket email could not be sent.');
      setEmailStatus('FAILED');
      setResult({ ...result, ticket: { ...result.ticket, email_status: 'FAILED' } });
    } finally {
      setEmailBusy(false);
    }
  }

  function startNext() {
    setResult(null);
    setError('');
    setEmailStatus('');
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
      <p className="mt-2 text-sm leading-relaxed text-slate-500">Record a walk-up attendee and payment details. Payment status is managed in the spreadsheet; import its verified status to issue a scannable ticket.</p>
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
        <p className="mt-6 rounded-xl bg-amber-50 p-4 text-sm leading-relaxed text-amber-900">This app records the registration only and does not approve or reject payment. Update the corresponding row in the spreadsheet, then preview and commit the import to issue the ticket.</p>
        {error && <p role="alert" className="mt-4 rounded-xl bg-red-50 p-4 text-sm text-red-700">{error}</p>}
        <button disabled={busy || !eventId} className="mt-6 flex w-full items-center justify-center gap-2 rounded-xl bg-accent px-5 py-3.5 font-semibold text-white hover:bg-ink disabled:opacity-50"><UserPlus size={18} />{busy ? 'Saving registration…' : 'Save registration'}</button>
      </form>}

      {result && <section className="mt-8 rounded-2xl border border-slate-200/80 bg-white p-6 sm:p-8">
        <div className={`flex items-start gap-3 rounded-xl p-4 ${result.issued ? 'bg-emerald-50 text-emerald-900' : 'bg-amber-50 text-amber-900'}`}>
          <CheckCircle2 size={21} className="mt-0.5 shrink-0" />
          <div><h2 className="font-semibold">{result.issued ? 'Ticket issued and ready to scan' : 'Registration saved — awaiting spreadsheet import'}</h2><p className="mt-1 text-sm">{result.issued ? 'The QR credential and PDF are ready. Email delivery is optional.' : 'Add or update this ticket in the OnSpot_Tickets sheet with the authoritative payment status, then preview and commit the import.'}</p></div>
        </div>
        <dl className="mt-6 grid gap-x-8 gap-y-4 sm:grid-cols-2"><div><dt className="text-xs uppercase tracking-wide text-slate-500">Ticket</dt><dd className="mt-1 font-semibold text-ink">{result.ticket.ticket_number}</dd></div><div><dt className="text-xs uppercase tracking-wide text-slate-500">Ticket ID · use as sheet ticket_id</dt><dd className="mt-1 break-all font-mono text-xs text-ink">{result.ticket.id}</dd></div><div><dt className="text-xs uppercase tracking-wide text-slate-500">Attendee</dt><dd className="mt-1 font-semibold text-ink">{result.ticket.attendee_name}</dd></div><div><dt className="text-xs uppercase tracking-wide text-slate-500">Email</dt><dd className="mt-1 text-ink">{result.ticket.attendee_email}</dd></div><div><dt className="text-xs uppercase tracking-wide text-slate-500">Ticket state · sheet payment status</dt><dd className="mt-1 text-ink">{result.ticket.status} · {result.ticket.payment_status}</dd></div></dl>
        {error && <p role="alert" className="mt-4 rounded-xl bg-red-50 p-4 text-sm text-red-700">{error}</p>}
        <div className="mt-6 flex flex-wrap gap-3">
          {user?.role !== 'DESK' && result.issued && result.pdf_url && <a href={result.pdf_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-2 rounded-xl bg-ink px-4 py-3 text-sm font-semibold text-white"><Download size={17} />Download ticket PDF</a>}
          {user?.role !== 'DESK' && result.issued && result.ticket.email_status !== 'SENT' && result.ticket.email_status !== 'PROCESSING' && <button onClick={() => void sendEmail()} disabled={emailBusy} className="inline-flex items-center gap-2 rounded-xl border border-slate-200 px-4 py-3 text-sm font-semibold text-ink disabled:opacity-50"><Mail size={17} />{emailBusy ? 'Sending…' : result.ticket.email_status === 'FAILED' || emailStatus === 'FAILED' ? 'Retry email' : 'Email ticket'}</button>}
          {emailStatus && <span className="self-center text-sm text-slate-600">Email: {emailStatus}</span>}
          <button onClick={startNext} className="rounded-xl border border-slate-200 px-4 py-3 text-sm font-semibold text-slate-700">New registration</button>
        </div>
      </section>}
      </div>
    </div>
  );
}
