import { useEffect, useState } from 'react';
import { ArrowLeft, History, LogOut } from 'lucide-react';
import { Link } from 'react-router-dom';
import { api, type DeskAccount, type DeskHistoryData, type User } from '../api';

const formatMoney = (amount: string) =>
  `₹${Number(amount || 0).toLocaleString('en-IN', { maximumFractionDigits: 2 })}`;

const formatDate = (value: string) => new Date(value).toLocaleString();

export default function DeskHistoryPage({
  user,
  onSignOut,
}: {
  user: User;
  onSignOut?: () => void;
}) {
  const [history, setHistory] = useState<DeskHistoryData | null>(null);
  const [desks, setDesks] = useState<DeskAccount[]>([]);
  const [selectedDeskId, setSelectedDeskId] = useState<number | undefined>();
  const [page, setPage] = useState(1);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (user.role !== 'ADMIN') return;
    api.deskAccounts().then(setDesks).catch((reason: unknown) => {
      setError(reason instanceof Error ? reason.message : 'Could not load registration desks.');
    });
  }, [user.role]);

  useEffect(() => {
    let active = true;
    setLoading(true);
    api.deskHistory(page, selectedDeskId)
      .then((data) => {
        if (active) {
          setHistory(data);
          setError('');
        }
      })
      .catch((reason: unknown) => {
        if (active) setError(reason instanceof Error ? reason.message : 'Could not load desk history.');
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [page, selectedDeskId]);

  const totalPages = history ? Math.max(1, Math.ceil(history.count / history.page_size)) : 1;

  return (
    <div className={`mx-auto max-w-7xl ${user.role === 'DESK' ? 'min-h-screen bg-ink px-3 pb-8 pt-4 sm:px-6 sm:pt-5' : ''}`}>
      {user.role === 'DESK' && (
        <header className="mx-auto mb-5 flex max-w-5xl items-center justify-between gap-3 text-white">
          <div className="min-w-0">
            <p className="text-[0.65rem] font-semibold tracking-[0.2em] text-emerald-300">GATEHOUSE · REGISTRATION</p>
            <h1 className="mt-1 text-xl font-semibold">Desk history</h1>
            <p className="mt-1 truncate text-xs text-slate-400">{user.name || user.email}</p>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {user.desk && <span className="rounded-full bg-emerald-400 px-3 py-1.5 text-xs font-bold text-ink">{user.desk.replace('_', ' ')}</span>}
            <button type="button" aria-label="Sign out" onClick={onSignOut} className="scanner-icon"><LogOut size={19} /></button>
          </div>
        </header>
      )}
      <div className={user.role === 'DESK' ? 'mx-auto max-w-5xl rounded-2xl bg-white p-4 sm:rounded-3xl sm:p-7' : ''}>
        <header className="mb-6 flex flex-wrap items-end justify-between gap-3">
          <div>
            <p className="flex items-center gap-2 text-sm font-medium text-accent"><History size={16} />Operations / Desk history</p>
            <h1 className="mt-2 text-3xl font-semibold tracking-tight text-ink">Registration history</h1>
            <p className="mt-2 text-sm text-slate-500">
              {user.role === 'DESK'
                ? 'Track all registrations made by your desk.'
                : 'Review registration orders and ticket details by desk.'}
            </p>
          </div>
          {user.role === 'DESK' ? (
            <Link to="/on-spot" className="flex min-h-11 items-center gap-2 rounded-xl border border-slate-200 px-4 text-sm font-semibold text-slate-700 hover:bg-slate-50"><ArrowLeft size={16} />Back to registration</Link>
          ) : (
            <label className="block min-w-56 text-sm font-medium text-slate-700">
              Filter desk
              <select
                className="field mt-1"
                value={selectedDeskId ?? ''}
                onChange={(event) => {
                  setSelectedDeskId(event.target.value ? Number(event.target.value) : undefined);
                  setPage(1);
                }}
              >
                <option value="">All desks</option>
                {desks.map((desk) => <option key={desk.id} value={desk.id}>{desk.desk.replace('_', ' ')} · {desk.name || desk.email}</option>)}
              </select>
            </label>
          )}
        </header>

        {error && <p role="alert" className="mb-5 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</p>}

        <section className="mb-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
          {[
            ['Orders', history?.count ?? '—'],
            ['Tickets sold', history?.tickets_sold ?? '—'],
            ['Total received', history ? formatMoney(history.total) : '—'],
            ['Cash', history ? formatMoney(history.cash_total) : '—'],
            ['UPI / Card', history ? `${formatMoney(history.upi_total)} / ${formatMoney(history.card_total)}` : '—'],
          ].map(([label, value]) => (
            <article key={label} className="rounded-xl border border-slate-200/80 bg-white p-4 shadow-sm">
              <p className="text-xs font-medium text-slate-500">{label}</p>
              <p className="mt-2 text-xl font-semibold text-ink">{value}</p>
            </article>
          ))}
        </section>

        <section className="space-y-3">
          {history?.results.map((order) => (
            <article key={order.id} className="rounded-2xl border border-slate-200/80 bg-white p-4 shadow-sm sm:p-5">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <p className="text-xs text-slate-500">{formatDate(order.created_at)} · {order.event_name}</p>
                  <h2 className="mt-1 font-semibold text-ink">{order.attendee_name}</h2>
                  <p className="mt-1 text-sm text-slate-500">{order.attendee_email}{order.attendee_phone ? ` · ${order.attendee_phone}` : ''}</p>
                  {user.role === 'ADMIN' && <p className="mt-1 text-xs font-medium text-accent">{order.desk.replace('_', ' ')} · {order.desk_name}</p>}
                </div>
                <div className="text-left sm:text-right">
                  <p className="text-lg font-semibold text-ink">{formatMoney(order.amount)}</p>
                  <p className="mt-1 text-sm text-slate-500">{order.payment_method} · {order.payment_status} · {order.ticket_count} tickets</p>
                  {order.transaction_reference && <p className="mt-1 text-xs text-slate-400">Reference: {order.transaction_reference}</p>}
                </div>
              </div>
              <div className="mt-4 grid gap-2 border-t border-slate-100 pt-3 sm:grid-cols-2">
                {order.tickets.map((ticket) => (
                  <div key={ticket.ticket_number} className="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-slate-50 px-3 py-2 text-sm">
                    <div><span className="font-mono text-xs text-ink">{ticket.ticket_number}</span><span className="ml-2 text-slate-600">{ticket.attendee_name}</span></div>
                    <span className="text-xs text-slate-500">{ticket.status}{ticket.checked_in_at ? ` · Checked in ${formatDate(ticket.checked_in_at)}` : ''}</span>
                  </div>
                ))}
              </div>
            </article>
          ))}
          {!loading && history?.results.length === 0 && <div className="rounded-2xl border border-dashed border-slate-300 bg-white p-8 text-center text-sm text-slate-500">No registration history found for this desk.</div>}
          {loading && <p className="py-6 text-center text-sm text-slate-500">Loading desk history…</p>}
        </section>

        {history && history.count > history.page_size && (
          <nav aria-label="Desk history pages" className="mt-5 flex items-center justify-between">
            <p className="text-sm text-slate-500">Page {page} of {totalPages} · {history.count} orders</p>
            <div className="flex gap-2">
              <button type="button" className="min-h-10 rounded-lg border border-slate-200 px-3 text-sm disabled:opacity-40" disabled={page <= 1 || loading} onClick={() => setPage((current) => current - 1)}>Previous</button>
              <button type="button" className="min-h-10 rounded-lg border border-slate-200 px-3 text-sm disabled:opacity-40" disabled={page >= totalPages || loading} onClick={() => setPage((current) => current + 1)}>Next</button>
            </div>
          </nav>
        )}
      </div>
    </div>
  );
}
