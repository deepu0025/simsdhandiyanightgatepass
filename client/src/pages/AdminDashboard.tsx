import { useEffect, useState } from 'react';
import { Activity, ArrowUpRight, CheckCircle2, Download, Mail, MailWarning, Search, Ticket, Users } from 'lucide-react';
import { api, type DashboardData, type TicketRecord } from '../api';

const metricConfig = [
  { key: 'registrations', label: 'Registrations', icon: Users, color: 'bg-blue-50 text-blue-700' },
  { key: 'tickets_issued', label: 'Tickets issued', icon: Ticket, color: 'bg-violet-50 text-violet-700' },
  { key: 'check_ins', label: 'Successful check-ins', icon: CheckCircle2, color: 'bg-emerald-50 text-emerald-700' },
  { key: 'remaining_eligible', label: 'Ready at the gate', icon: Activity, color: 'bg-amber-50 text-amber-700' },
] as const;

export default function AdminDashboard() {
  const [dashboard, setDashboard] = useState<DashboardData | null>(null);
  const [tickets, setTickets] = useState<TicketRecord[]>([]);
  const [query, setQuery] = useState('');
  const [searchQuery, setSearchQuery] = useState('');
  const [error, setError] = useState('');
  const [sendingTicketId, setSendingTicketId] = useState('');
  const [checkingInTicketId, setCheckingInTicketId] = useState('');
  const [manualCheckInTicket, setManualCheckInTicket] = useState<TicketRecord | null>(null);
  const [manualCheckInReason, setManualCheckInReason] = useState('');

  useEffect(() => {
    let active = true;
    async function refresh() {
      const [dashboardResult, ticketsResult] = await Promise.allSettled([
        api.dashboard(),
        api.tickets(searchQuery),
      ]);
      if (!active) return;
      const errors: string[] = [];
      if (dashboardResult.status === 'fulfilled') {
        setDashboard(dashboardResult.value);
      } else {
        errors.push(dashboardResult.reason instanceof Error ? dashboardResult.reason.message : 'Could not refresh dashboard.');
      }
      if (ticketsResult.status === 'fulfilled') {
        setTickets(ticketsResult.value.results);
      } else {
        errors.push(ticketsResult.reason instanceof Error ? ticketsResult.reason.message : 'Could not refresh tickets.');
      }
      setError(errors.join(' '));
    }

    void refresh();
    const timer = window.setInterval(() => void refresh(), 30_000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [searchQuery]);

  async function search(event: React.FormEvent) {
    event.preventDefault();
    try {
      setTickets((await api.tickets(query)).results);
      setSearchQuery(query);
      setError('');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not search tickets.');
    }
  }

  async function sendTicketEmail(ticketId: string) {
    setSendingTicketId(ticketId);
    setError('');
    try {
      await api.sendTicketEmail(ticketId);
      setTickets((current) => current.map((ticket) =>
        ticket.id === ticketId ? { ...ticket, email_status: 'SENT' } : ticket,
      ));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Ticket email could not be sent.');
    } finally {
      setSendingTicketId('');
    }
  }

  async function manuallyCheckIn(event: React.FormEvent) {
    event.preventDefault();
    if (!manualCheckInTicket || !manualCheckInReason.trim()) {
      setError('A reason is required for manual check-in.');
      return;
    }

    const ticket = manualCheckInTicket;
    setCheckingInTicketId(ticket.id);
    setError('');
    try {
      const result = await api.manualCheckIn(ticket.id, manualCheckInReason.trim());
      setTickets((current) => current.map((item) =>
        item.id === ticket.id ? result.ticket : item,
      ));
      setManualCheckInTicket(null);
      setManualCheckInReason('');
      try {
        setDashboard(await api.dashboard());
      } catch (refreshError) {
        const detail = refreshError instanceof Error ? `: ${refreshError.message}` : '';
        setError(`Manual check-in was recorded, but dashboard metrics could not be refreshed${detail}`);
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Manual check-in failed.');
    } finally {
      setCheckingInTicketId('');
    }
  }

  return (
    <div className="mx-auto max-w-7xl">
      <header className="mb-8 flex flex-wrap items-end justify-between gap-4">
        <div><p className="text-sm font-medium text-accent">Operations / Overview</p><h1 className="mt-2 text-3xl font-semibold tracking-tight text-ink">Good to see you.</h1><p className="mt-2 text-sm text-slate-500">Live ticket and gate activity from your event database.</p></div>
        <div className="rounded-full border border-slate-200 bg-white px-4 py-2 text-sm text-slate-600"><span className={`mr-2 inline-block h-2 w-2 rounded-full ${error ? 'bg-red-500' : dashboard ? 'bg-emerald-500' : 'bg-amber-400'}`} />{error ? 'Backend unavailable' : dashboard ? 'Backend connected' : 'Checking connection'}</div>
      </header>
      {error && <div role="alert" className="mb-5 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>}
      <section className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {metricConfig.map(({ key, label, icon: Icon, color }) => (
          <article key={key} className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-sm">
            <div className="flex items-center justify-between"><span className={`grid h-10 w-10 place-items-center rounded-xl ${color}`}><Icon size={19} /></span><ArrowUpRight size={17} className="text-slate-300" /></div>
            <p className="mt-6 text-3xl font-semibold tracking-tight text-ink">{dashboard?.[key] ?? '—'}</p>
            <p className="mt-1 text-sm text-slate-500">{label}</p>
          </article>
        ))}
      </section>
      <section className="mt-5 grid gap-4 lg:grid-cols-2">
        <div className="rounded-2xl border border-slate-200/80 bg-white p-5"><p className="flex items-center gap-2 text-sm font-medium text-slate-500"><MailWarning size={16} />Emails to review</p><p className="mt-4 text-2xl font-semibold text-ink">{dashboard?.emails_pending_or_failed ?? '—'}</p></div>
        <div className="rounded-2xl border border-slate-200/80 bg-white p-5"><p className="flex items-center gap-2 text-sm font-medium text-slate-500"><Users size={16} />Online / on-spot</p><p className="mt-4 text-2xl font-semibold text-ink">{dashboard?.online_tickets ?? '—'} <span className="text-base font-normal text-slate-400">/</span> {dashboard?.on_spot_tickets ?? '—'}</p></div>
      </section>
      <section className="mt-5 rounded-2xl border border-slate-200/80 bg-white p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div><h2 className="font-semibold text-ink">Automatic spreadsheet import</h2><p className="mt-1 text-sm text-slate-500">Both Google Sheets tabs are checked every 30 seconds. Eligible tickets are issued automatically; emails are not sent automatically.</p></div>
          <span className="text-xs text-slate-400">Dashboard refreshes every 30 seconds</span>
        </div>
        <div className="mt-4 grid gap-3 sm:grid-cols-2">
          {(['Online_Tickets', 'OnSpot_Tickets'] as const).map((worksheet) => {
            const sync = dashboard?.google_sheet_imports?.find((item) => item.worksheet === worksheet);
            return <article key={worksheet} className="rounded-xl border border-slate-100 bg-slate-50 p-4">
              <p className="font-medium text-ink">{worksheet.replace('_', ' ')}</p>
              {!sync ? <p className="mt-2 text-sm text-slate-500">{dashboard && !dashboard.google_sheet_imports ? 'Import status is unavailable. Restart the backend to load worker status.' : 'Waiting for the automatic import worker’s first check.'}</p> : <>
                <p className="mt-2 text-xs text-slate-500">{sync.last_synced_at ? `Last checked ${new Date(sync.last_synced_at).toLocaleString()}` : 'Waiting for the first successful check.'}</p>
                {sync.last_error && <p role="status" className="mt-2 text-sm text-red-700">{sync.last_error}</p>}
                {sync.last_result && <p className="mt-2 text-xs text-slate-600">{sync.last_result.created ?? 0} created · {sync.last_result.updated ?? 0} updated · {sync.last_result.issued ?? 0} issued · {sync.last_result.rejected ?? 0} rejected</p>}
              </>}
            </article>;
          })}
        </div>
      </section>
      <section className="mt-8 overflow-hidden rounded-2xl border border-slate-200/80 bg-white">
        <div className="flex flex-wrap items-center justify-between gap-4 border-b border-slate-100 p-5">
          <div><h2 className="font-semibold text-ink">Ticket register</h2><p className="mt-1 text-sm text-slate-500">Search by ticket number, attendee, email or registration.</p></div>
          <form onSubmit={search} className="flex w-full gap-2 sm:w-auto">
            <label className="relative flex-1 sm:w-72"><Search size={16} className="absolute left-3 top-3 text-slate-400" /><input value={query} onChange={(event) => setQuery(event.target.value)} className="field py-2 pl-9" placeholder="Search tickets" /></label>
            <button className="rounded-lg bg-ink px-4 text-sm font-medium text-white">Search</button>
          </form>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[680px] text-left text-sm">
            <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500"><tr><th className="px-5 py-3 font-medium">Ticket / Attendee</th><th className="px-5 py-3 font-medium">Type</th><th className="px-5 py-3 font-medium">Sheet payment status</th><th className="px-5 py-3 font-medium">Ticket status</th><th className="px-5 py-3 font-medium">Email</th><th className="px-5 py-3 font-medium">Check-in</th></tr></thead>
            <tbody className="divide-y divide-slate-100">
              {tickets.map((ticket) => <tr key={ticket.id} className="hover:bg-slate-50/80"><td className="px-5 py-4"><p className="font-medium text-ink">{ticket.ticket_number}</p><p className="mt-1 text-xs text-slate-500">{ticket.attendee_name} · {ticket.attendee_email}</p></td><td className="px-5 py-4 text-slate-600">{ticket.ticket_type}</td><td className="px-5 py-4"><span className="status-pill">{ticket.payment_status}</span>{ticket.eligibility_basis === 'IMPORTED_PAYMENT_STATUS' && <p className="mt-1 text-xs text-emerald-700">From imported spreadsheet</p>}{ticket.eligibility_basis === 'IMPORTED_EMAIL_SENT' && <p className="mt-1 text-xs text-teal-700">Email evidence imported from sheet</p>}</td><td className="px-5 py-4 text-slate-600"><div>{ticket.status}</div>{ticket.status === 'ISSUED' && <a href={`/api/tickets/${ticket.id}/pdf/`} target="_blank" rel="noreferrer" className="mt-2 inline-flex items-center gap-1 text-xs font-medium text-accent hover:underline"><Download size={14} />Download QR pass</a>}</td><td className="px-5 py-4"><div className="flex items-center gap-2"><span className="status-pill">{ticket.email_status}</span><button disabled={sendingTicketId === ticket.id || ticket.status !== 'ISSUED' || ticket.email_status === 'SENT' || ticket.email_status === 'PROCESSING'} onClick={() => void sendTicketEmail(ticket.id)} aria-label={`${ticket.email_status === 'FAILED' ? 'Retry' : 'Send'} ticket email to ${ticket.attendee_email}`} title={ticket.email_status === 'FAILED' ? 'Retry failed email' : 'Send ticket email'} className="rounded-lg border border-slate-200 p-2 text-slate-600 hover:border-accent hover:text-accent disabled:opacity-40"><Mail size={15} /></button></div></td><td className="px-5 py-4 text-slate-600"><div>{ticket.checked_in_at ? (ticket.checkin_method === 'MANUAL' ? 'Manual check-in' : 'Checked in') : 'Not checked in'}</div>{!ticket.checked_in_at && !['CANCELLED', 'REVOKED'].includes(ticket.status) && <button type="button" disabled={checkingInTicketId === ticket.id} onClick={() => { setError(''); setManualCheckInReason(''); setManualCheckInTicket(ticket); }} aria-label={`Manual check-in ${ticket.ticket_number}`} className="mt-2 whitespace-nowrap rounded-lg border border-amber-300 px-2 py-1 text-xs font-medium text-amber-800 hover:bg-amber-50 disabled:opacity-40">{checkingInTicketId === ticket.id ? 'Checking in...' : 'Manual check-in'}</button>}</td></tr>)}
              {tickets.length === 0 && <tr><td colSpan={6} className="px-5 py-10 text-center text-slate-500">No matching tickets.</td></tr>}
            </tbody>
          </table>
        </div>
      </section>
      {manualCheckInTicket && <div className="fixed inset-0 z-50 grid place-items-center bg-ink/50 p-4" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !checkingInTicketId) setManualCheckInTicket(null); }}>
        <form role="dialog" aria-modal="true" aria-labelledby="manual-check-in-title" onSubmit={(event) => void manuallyCheckIn(event)} className="w-full max-w-lg rounded-2xl bg-white p-6 shadow-xl">
          <h2 id="manual-check-in-title" className="text-lg font-semibold text-ink">Confirm manual check-in</h2>
          <p className="mt-2 text-sm text-slate-600">This records a manual attendance override for {manualCheckInTicket.attendee_name} ({manualCheckInTicket.ticket_number}) without requiring an issued QR pass. It does not update the spreadsheet payment status. Cancelled or revoked tickets remain blocked; the action is recorded in the audit log.</p>
          <label htmlFor="manual-check-in-reason" className="mt-5 block text-sm font-medium text-ink">Reason</label>
          <textarea id="manual-check-in-reason" autoFocus required minLength={1} value={manualCheckInReason} onChange={(event) => setManualCheckInReason(event.target.value)} className="field mt-2 min-h-24 resize-y" placeholder="Why is this manual check-in authorized?" />
          <div className="mt-5 flex justify-end gap-3">
            <button type="button" disabled={Boolean(checkingInTicketId)} onClick={() => setManualCheckInTicket(null)} className="rounded-lg border border-slate-200 px-4 py-2 text-sm font-medium text-slate-700 disabled:opacity-50">Cancel</button>
            <button type="submit" disabled={Boolean(checkingInTicketId) || !manualCheckInReason.trim()} className="rounded-lg bg-amber-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50">{checkingInTicketId ? 'Checking in...' : 'Confirm check-in'}</button>
          </div>
        </form>
      </div>}
    </div>
  );
}
