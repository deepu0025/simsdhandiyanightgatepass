import { useEffect, useRef, useState, type FormEvent } from 'react';
import { CheckCircle2, History, LogOut, UserPlus } from 'lucide-react';
import { Link } from 'react-router-dom';
import { api, type EventRecord, type SheetSyncStatus, type TicketRecord, type User } from '../api';

const SINGLE_TICKET_PRICE = 149;
const MAX_TICKET_QUANTITY = 20;
type TicketOption = 'Single ticket' | 'Combo 5+1';

type CreatedResult = {
  registration_id: string;
  ticket: TicketRecord;
  tickets: TicketRecord[];
  quantity: number;
  ticket_count: number;
  amount: string;
  issued: boolean;
  pdf_url: string | null;
  replayed: boolean;
  sheet_sync_status: SheetSyncStatus;
};

export default function OnSpotPage({ user, onSignOut }: { user?: User; onSignOut?: () => void } = {}) {
  const [events, setEvents] = useState<EventRecord[]>([]);
  const [eventId, setEventId] = useState('');
  const [ticketOption, setTicketOption] = useState<TicketOption>('Single ticket');
  const [quantity, setQuantity] = useState(1);
  const [attendeeNames, setAttendeeNames] = useState(['']);
  const [attendeePhone, setAttendeePhone] = useState('');
  const [paymentMethod, setPaymentMethod] = useState<'CASH' | 'CARD' | 'UPI'>('CASH');
  const [result, setResult] = useState<CreatedResult | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const idempotencyKey = useRef('');
  const ticketCount = quantity * (ticketOption === 'Combo 5+1' ? 6 : 1);
  const totalAmount = quantity * (ticketOption === 'Combo 5+1' ? 5 : 1) * SINGLE_TICKET_PRICE;

  function resizeAttendeeNames(names: string[], count: number) {
    return Array.from({ length: count }, (_, index) => names[index] ?? '');
  }

  function updateQuantity(nextQuantity: number) {
    setQuantity(nextQuantity);
    setAttendeeNames((names) => resizeAttendeeNames(
      names,
      nextQuantity * (ticketOption === 'Combo 5+1' ? 6 : 1),
    ));
  }

  function updateTicketOption(nextOption: TicketOption) {
    setTicketOption(nextOption);
    setAttendeeNames((names) => resizeAttendeeNames(
      names,
      quantity * (nextOption === 'Combo 5+1' ? 6 : 1),
    ));
  }

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
        attendee_name: attendeeNames[0]?.trim() || '',
        attendee_names: attendeeNames.map((name) => name.trim()),
        attendee_email: String(form.get('attendee_email') || '').trim(),
        attendee_phone: String(form.get('attendee_phone') || '').trim(),
        ticket_type: ticketOption,
        quantity,
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
    setAttendeePhone('');
    setAttendeeNames(['']);
    setTicketOption('Single ticket');
    setQuantity(1);
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
          <Link to="/desk-history" aria-label="View desk history" title="Desk history" className="scanner-icon"><History size={19} /></Link>
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
          <div className="sm:col-span-2">
            <p className="text-sm font-medium text-slate-700">Attendee name for each ticket</p>
            <p className="mt-1 text-xs text-slate-500">Enter a separate name for every ticket in this order.</p>
            <div className="mt-2 grid gap-3 sm:grid-cols-2">
              {attendeeNames.map((name, index) => (
                <label key={index} className="text-sm font-medium text-slate-700">
                  Ticket {index + 1} attendee
                  <input
                    required
                    name={`attendee_name_${index + 1}`}
                    maxLength={160}
                    className="field mt-1"
                    autoComplete="name"
                    value={name}
                    onChange={(event) => setAttendeeNames((current) =>
                      current.map((currentName, currentIndex) =>
                        currentIndex === index ? event.target.value : currentName,
                      ),
                    )}
                  />
                </label>
              ))}
            </div>
          </div>
          <label className="text-sm font-medium text-slate-700">Contact email (shared by all tickets)<input required type="email" name="attendee_email" maxLength={254} pattern="[^ @]+@[^ @]+[.][^ @]{2,}" title="Enter a valid email address, such as name@example.com." className="field mt-2" autoComplete="email" /></label>
          <label className="text-sm font-medium text-slate-700">Contact phone (shared by all tickets)<input name="attendee_phone" type="tel" inputMode="numeric" maxLength={10} minLength={10} pattern="[6-9][0-9]{9}" title="Enter a 10-digit Indian mobile number starting with 6, 7, 8, or 9." value={attendeePhone} onChange={(event) => setAttendeePhone(event.target.value.replace(/\D/g, '').slice(0, 10))} className="field mt-2" autoComplete="tel" /></label>
          <label className="text-sm font-medium text-slate-700">Ticket option
            <select name="ticket_type" value={ticketOption} onChange={(event) => updateTicketOption(event.target.value as TicketOption)} className="field mt-2">
              <option value="Single ticket">Single ticket · ₹149</option>
              <option value="Combo 5+1">Combo 5+1 · 6 tickets for ₹745</option>
            </select>
          </label>
          <div>
            <span className="block text-sm font-medium text-slate-700">Quantity</span>
            <div className="mt-2 flex h-[46px] items-center justify-between rounded-xl border border-slate-200 px-2">
              <button type="button" aria-label="Decrease quantity" disabled={quantity <= 1} onClick={() => updateQuantity(Math.max(1, quantity - 1))} className="grid h-9 w-9 place-items-center rounded-lg bg-slate-100 text-xl font-semibold text-ink disabled:opacity-40">−</button>
              <span aria-live="polite" className="font-semibold text-ink">{quantity}</span>
              <button type="button" aria-label="Increase quantity" disabled={quantity >= MAX_TICKET_QUANTITY} onClick={() => updateQuantity(Math.min(MAX_TICKET_QUANTITY, quantity + 1))} className="grid h-9 w-9 place-items-center rounded-lg bg-slate-100 text-xl font-semibold text-ink disabled:opacity-40">+</button>
            </div>
          </div>
          <div className="rounded-xl bg-emerald-50 p-3 text-sm text-emerald-900">
            <p className="font-semibold">Total: ₹{totalAmount.toLocaleString('en-IN')}</p>
            <p className="mt-1">{ticketCount} {ticketCount === 1 ? 'ticket' : 'tickets'}{ticketOption === 'Combo 5+1' ? ' · Buy 5, get 1 free' : ''} · ₹{SINGLE_TICKET_PRICE} per paid ticket</p>
          </div>
          <label className="text-sm font-medium text-slate-700">Payment method
            <select value={paymentMethod} onChange={(event) => setPaymentMethod(event.target.value as 'CASH' | 'CARD' | 'UPI')} className="field mt-2">
              <option value="CASH">Cash</option><option value="CARD">Card</option><option value="UPI">UPI</option>
            </select>
          </label>
          {(paymentMethod === 'UPI' || paymentMethod === 'CARD') && <label className="text-sm font-medium text-slate-700 sm:col-span-2">Transaction reference {paymentMethod === 'UPI' && <span className="text-red-600">*</span>}<input required={paymentMethod === 'UPI'} name="transaction_reference" maxLength={120} className="field mt-2" /></label>}
        </div>
        <p className="mt-6 rounded-xl bg-amber-50 p-4 text-sm leading-relaxed text-amber-900">Each ticket will be issued in its own attendee name. The contact email and phone are shared across the order. Confirm the total payment of ₹{totalAmount.toLocaleString('en-IN')} before saving. Saving verifies payment, issues {ticketCount} {ticketCount === 1 ? 'ticket' : 'tickets'}, and checks them in at this desk.</p>
        {error && <p role="alert" className="mt-4 rounded-xl bg-red-50 p-4 text-sm text-red-700">{error}</p>}
        <button disabled={busy || !eventId} className="mt-6 flex w-full items-center justify-center gap-2 rounded-xl bg-accent px-5 py-3.5 font-semibold text-white hover:bg-ink disabled:opacity-50"><UserPlus size={18} />{busy ? 'Saving registration…' : 'Save registration'}</button>
      </form>}

      {result && <section className="mt-8 rounded-2xl border border-slate-200/80 bg-white p-6 sm:p-8">
        <div className={`flex items-start gap-3 rounded-xl p-4 ${result.issued ? 'bg-emerald-50 text-emerald-900' : 'bg-amber-50 text-amber-900'}`}>
          <CheckCircle2 size={21} className="mt-0.5 shrink-0" />
          <div><h2 className="font-semibold">{result.ticket.checked_in_at ? 'Payment verified — attendee checked in' : result.issued ? 'Ticket issued' : 'Registration saved'}</h2><p className="mt-1 text-sm">{result.ticket_count} {result.ticket_count === 1 ? 'ticket' : 'tickets'} · {result.quantity} {result.quantity === 1 ? 'package' : 'packages'} · ₹{Number(result.amount).toLocaleString('en-IN')}. {result.ticket.checked_in_at ? `Checked in at ${new Date(result.ticket.checked_in_at).toLocaleString()}${result.ticket.registration_desk ? ` · ${result.ticket.registration_desk.replace('_', ' ')}` : ''}.` : 'The ticket is ready.'} {result.sheet_sync_status === 'SUCCEEDED' ? 'All registration and check-in details were saved to the OnSpot_Tickets sheet.' : result.sheet_sync_status === 'NOT_CONFIGURED' ? 'Google Sheets is not configured; this registration is saved in Gatehouse only.' : result.sheet_sync_status === 'FAILED' ? 'Sheet sync failed; this registration is saved in Gatehouse. Check the Sheets connection and sync status.' : 'Registration details are queued for the OnSpot_Tickets sheet.'}</p></div>
        </div>
        <dl className="mt-6 grid gap-x-8 gap-y-4 sm:grid-cols-2"><div><dt className="text-xs uppercase tracking-wide text-slate-500">Order</dt><dd className="mt-1 font-semibold text-ink">{result.ticket_count} tickets · ₹{Number(result.amount).toLocaleString('en-IN')}</dd></div><div className="sm:row-span-3"><dt className="text-xs uppercase tracking-wide text-slate-500">Tickets and attendees</dt><dd className="mt-1 space-y-1">{result.tickets.map((item) => <p key={item.id} className="break-words text-sm text-ink"><span className="font-mono text-xs">{item.ticket_number}</span><span className="text-slate-400"> · </span>{item.attendee_name}</p>)}</dd></div><div><dt className="text-xs uppercase tracking-wide text-slate-500">Contact email</dt><dd className="mt-1 text-ink">{result.ticket.attendee_email}</dd></div><div><dt className="text-xs uppercase tracking-wide text-slate-500">Ticket state · payment</dt><dd className="mt-1 text-ink">{result.ticket.status} · {result.ticket.payment_status}</dd></div><div><dt className="text-xs uppercase tracking-wide text-slate-500">Check-in time · desk</dt><dd className="mt-1 text-ink">{result.ticket.checked_in_at ? new Date(result.ticket.checked_in_at).toLocaleString() : '—'} · {result.ticket.registration_desk?.replace('_', ' ') || '—'}</dd></div></dl>
        {error && <p role="alert" className="mt-4 rounded-xl bg-red-50 p-4 text-sm text-red-700">{error}</p>}
        <div className="mt-6 flex flex-wrap gap-3">
          <button onClick={startNext} className="rounded-xl border border-slate-200 px-4 py-3 text-sm font-semibold text-slate-700">New registration</button>
        </div>
      </section>}
      </div>
    </div>
  );
}
