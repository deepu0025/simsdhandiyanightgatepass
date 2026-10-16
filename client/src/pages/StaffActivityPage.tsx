import { useEffect, useState } from 'react';
import { Banknote, CreditCard, RefreshCw, ScanLine, Ticket, Wallet } from 'lucide-react';
import { api, type StaffActivityData } from '../api';

const money = (amount: string | null) =>
  `₹${Number(amount || 0).toLocaleString('en-IN', { maximumFractionDigits: 2 })}`;

const time = (value: string | null) => value ? new Date(value).toLocaleString() : '—';

export default function StaffActivityPage() {
  const [activity, setActivity] = useState<StaffActivityData | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    async function refresh() {
      try {
        const data = await api.staffActivity();
        if (active) {
          setActivity(data);
          setError('');
        }
      } catch (reason) {
        if (active) setError(reason instanceof Error ? reason.message : 'Could not load staff activity.');
      } finally {
        if (active) setLoading(false);
      }
    }

    void refresh();
    const timer = window.setInterval(() => void refresh(), 30_000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, []);

  return (
    <div className="mx-auto max-w-7xl">
      <header className="mb-7 flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-sm font-medium text-accent">Operations / Staff activity</p>
          <h1 className="mt-2 text-3xl font-semibold tracking-tight text-ink">Desk & scanner dashboard</h1>
          <p className="mt-2 text-sm text-slate-500">Sales and payment totals are grouped by registration desk; scan results are grouped by scanner account.</p>
        </div>
        <span className="text-xs text-slate-400">Refreshes every 30 seconds · All-time totals</span>
      </header>

      {error && <p role="alert" className="mb-5 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</p>}
      {loading && !activity && <p className="mb-5 text-sm text-slate-500">Loading staff activity…</p>}

      <section>
        <div className="mb-3 flex items-center gap-2"><ScanLine size={18} className="text-accent" /><h2 className="text-lg font-semibold text-ink">Gate scanners</h2></div>
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {activity?.scanners.map((scanner) => (
            <article key={scanner.id} className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-sm">
              <div className="flex items-start justify-between gap-2">
                <div><p className="text-xs font-semibold uppercase tracking-wider text-accent">{scanner.gate.replace('_', ' ') || 'Unassigned'}</p><h3 className="mt-1 font-semibold text-ink">{scanner.name || 'Scanner'}</h3><p className="mt-1 break-all text-xs text-slate-500">{scanner.email}</p></div>
                <span className={`rounded-full px-2 py-1 text-xs font-semibold ${scanner.is_active ? 'bg-emerald-50 text-emerald-700' : 'bg-slate-100 text-slate-500'}`}>{scanner.is_active ? 'Active' : 'Disabled'}</span>
              </div>
              <div className="mt-5 grid grid-cols-3 gap-2 text-center">
                <div className="rounded-xl bg-slate-50 p-3"><p className="text-xl font-semibold text-ink">{scanner.scans}</p><p className="mt-1 text-xs text-slate-500">Scans</p></div>
                <div className="rounded-xl bg-emerald-50 p-3"><p className="text-xl font-semibold text-emerald-800">{scanner.accepted}</p><p className="mt-1 text-xs text-emerald-700">Accepted</p></div>
                <div className="rounded-xl bg-amber-50 p-3"><p className="text-xl font-semibold text-amber-800">{scanner.failed}</p><p className="mt-1 text-xs text-amber-700">Other results</p></div>
              </div>
              <p className="mt-3 text-xs text-slate-400">Last scan: {time(scanner.last_scan_at)}</p>
            </article>
          ))}
          {activity?.scanners.length === 0 && <p className="rounded-xl border border-dashed border-slate-300 p-5 text-sm text-slate-500">No scanner accounts have been created.</p>}
        </div>
      </section>

      <section className="mt-8">
        <div className="mb-3 flex items-center gap-2"><Ticket size={18} className="text-accent" /><h2 className="text-lg font-semibold text-ink">On-spot registration desks</h2></div>
        <div className="grid gap-4 lg:grid-cols-2">
          {activity?.desks.map((desk) => (
            <article key={desk.id} className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-sm">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div><p className="text-xs font-semibold uppercase tracking-wider text-accent">{desk.desk.replace('_', ' ') || 'Unassigned'}</p><h3 className="mt-1 text-xl font-semibold text-ink">{desk.name || 'Registration desk'}</h3><p className="mt-1 break-all text-sm text-slate-500">{desk.email}</p></div>
                <span className={`rounded-full px-2.5 py-1 text-xs font-semibold ${desk.is_active ? 'bg-emerald-50 text-emerald-700' : 'bg-slate-100 text-slate-500'}`}>{desk.is_active ? 'Active' : 'Disabled'}</span>
              </div>
              <div className="mt-5 grid gap-3 sm:grid-cols-2">
                <div className="rounded-xl bg-slate-50 p-4"><p className="text-sm text-slate-500">Tickets sold</p><p className="mt-1 text-2xl font-semibold text-ink">{desk.tickets_sold}</p><p className="mt-1 text-xs text-slate-400">{desk.orders} orders · Total {money(desk.total)}</p></div>
                <div className="rounded-xl bg-emerald-50 p-4"><p className="flex items-center gap-2 text-sm text-emerald-800"><Banknote size={16} />Cash received</p><p className="mt-1 text-2xl font-semibold text-emerald-900">{money(desk.cash_total)}</p><p className="mt-1 text-xs text-emerald-700">{desk.cash_orders} orders</p></div>
                <div className="rounded-xl bg-violet-50 p-4"><p className="flex items-center gap-2 text-sm text-violet-800"><Wallet size={16} />UPI received</p><p className="mt-1 text-2xl font-semibold text-violet-900">{money(desk.upi_total)}</p><p className="mt-1 text-xs text-violet-700">{desk.upi_orders} orders</p></div>
                <div className="rounded-xl bg-blue-50 p-4"><p className="flex items-center gap-2 text-sm text-blue-800"><CreditCard size={16} />Card received</p><p className="mt-1 text-2xl font-semibold text-blue-900">{money(desk.card_total)}</p><p className="mt-1 text-xs text-blue-700">{desk.card_orders} orders</p></div>
              </div>
            </article>
          ))}
          {activity?.desks.length === 0 && <p className="rounded-xl border border-dashed border-slate-300 p-5 text-sm text-slate-500">No registration desk accounts have been created.</p>}
        </div>
      </section>

      <section className="mt-8 grid gap-5 xl:grid-cols-2">
        <div className="min-w-0 rounded-2xl border border-slate-200/80 bg-white p-5 shadow-sm">
          <div className="mb-4 flex items-center justify-between"><div><h2 className="font-semibold text-ink">Recent scanner activity</h2><p className="mt-1 text-xs text-slate-500">Latest 100 recorded scan attempts</p></div><RefreshCw size={17} className="text-slate-400" /></div>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px] text-left text-sm">
              <thead className="text-xs uppercase tracking-wide text-slate-400"><tr><th className="pb-2 pr-3 font-medium">Time</th><th className="pb-2 pr-3 font-medium">Scanner / gate</th><th className="pb-2 pr-3 font-medium">Ticket</th><th className="pb-2 pr-3 font-medium">Method</th><th className="pb-2 font-medium">Result</th></tr></thead>
              <tbody className="divide-y divide-slate-100">
                {activity?.recent_scans.map((scan) => <tr key={scan.id}>
                  <td className="py-3 pr-3 text-xs text-slate-500">{time(scan.scanned_at)}</td>
                  <td className="py-3 pr-3"><span className="font-medium text-ink">{scan.scanner_name || 'Scanner'}</span><span className="block text-xs text-slate-500">{scan.scanner_gate.replace('_', ' ') || scan.gate || 'Unassigned'}</span></td>
                  <td className="py-3 pr-3"><span className="font-medium text-ink">{scan.ticket_number || 'Unknown ticket'}</span>{scan.attendee_name && <span className="block text-xs text-slate-500">{scan.attendee_name}</span>}</td>
                  <td className="py-3 pr-3 text-slate-600">{scan.method}</td>
                  <td className="py-3"><span className={`rounded-full px-2 py-1 text-xs font-semibold ${scan.result === 'ACCEPTED' ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-50 text-amber-800'}`}>{scan.result}</span>{scan.reason && <span className="mt-1 block max-w-36 truncate text-xs text-slate-400" title={scan.reason}>{scan.reason}</span>}</td>
                </tr>)}
                {activity?.recent_scans.length === 0 && <tr><td colSpan={5} className="py-6 text-center text-slate-500">No scan attempts recorded yet.</td></tr>}
              </tbody>
            </table>
          </div>
        </div>

        <div className="min-w-0 rounded-2xl border border-slate-200/80 bg-white p-5 shadow-sm">
          <div className="mb-4 flex items-center justify-between"><div><h2 className="font-semibold text-ink">Recent desk sales</h2><p className="mt-1 text-xs text-slate-500">Latest 100 on-spot registration orders</p></div><RefreshCw size={17} className="text-slate-400" /></div>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[560px] text-left text-sm">
              <thead className="text-xs uppercase tracking-wide text-slate-400"><tr><th className="pb-2 pr-3 font-medium">Time</th><th className="pb-2 pr-3 font-medium">Desk</th><th className="pb-2 pr-3 font-medium">Attendee</th><th className="pb-2 pr-3 font-medium">Tickets</th><th className="pb-2 pr-3 font-medium">Payment</th><th className="pb-2 font-medium">Amount</th></tr></thead>
              <tbody className="divide-y divide-slate-100">
                {activity?.recent_sales.map((sale) => <tr key={sale.id}>
                  <td className="py-3 pr-3 text-xs text-slate-500">{time(sale.created_at)}</td>
                  <td className="py-3 pr-3 font-medium text-ink">{sale.desk.replace('_', ' ')}</td>
                  <td className="py-3 pr-3 text-slate-700">{sale.attendee_name}</td>
                  <td className="py-3 pr-3 text-slate-600">{sale.ticket_count}</td>
                  <td className="py-3 pr-3 text-slate-600">{sale.payment_method}</td>
                  <td className="py-3 font-medium text-ink">{money(sale.amount)}</td>
                </tr>)}
                {activity?.recent_sales.length === 0 && <tr><td colSpan={6} className="py-6 text-center text-slate-500">No desk sales recorded yet.</td></tr>}
              </tbody>
            </table>
          </div>
        </div>
      </section>
    </div>
  );
}
