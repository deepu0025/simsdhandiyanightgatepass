import { useEffect, useState, type ChangeEvent } from 'react';
import { AlertTriangle, CheckCircle2, Download, FileSpreadsheet, Sheet, UploadCloud } from 'lucide-react';
import { Link } from 'react-router-dom';
import { api, type EventRecord, type ImportPreviewData } from '../api';

type Preview = ImportPreviewData;
type ImportResult = { created: number; updated: number; skipped: number; rejected: number; issued: number; error_report_url: string };

export default function ImportPage() {
  const [events, setEvents] = useState<EventRecord[]>([]);
  const [eventId, setEventId] = useState('');
  const [worksheet, setWorksheet] = useState<'Online_Tickets' | 'OnSpot_Tickets'>('Online_Tickets');
  const [preview, setPreview] = useState<Preview | null>(null);
  const [result, setResult] = useState<ImportResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    api.events().then((items) => {
      const active = items.filter((item) => item.is_active);
      setEvents(active);
      if (active.length === 1) setEventId(active[0].id);
    }).catch((reason: unknown) => {
      setError(reason instanceof Error ? reason.message : 'Could not load events.');
    });
  }, []);

  async function upload(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file || !eventId) return;
    setBusy(true);
    setError('');
    setPreview(null);
    setResult(null);
    try {
      setPreview(await api.previewImport(file, eventId));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Import preview failed.');
    } finally {
      setBusy(false);
      event.target.value = '';
    }
  }

  async function previewGoogleSheet() {
    if (!eventId) return;
    setBusy(true);
    setError('');
    setPreview(null);
    setResult(null);
    try {
      setPreview(await api.previewGoogleSheetImport(worksheet, eventId));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Google Sheets preview failed.');
    } finally {
      setBusy(false);
    }
  }

  async function commit() {
    if (!preview || !preview.commit_available || result) return;
    if (!window.confirm(`Import valid records for the selected event? ${preview.email_evidence_rows} row(s) contain email_status=SENT with a valid email_sent_at and may authorize a new Gatehouse QR/PDF. No ticket email will be sent automatically. Other payment claims remain unverified. ${preview.rejected} invalid row(s) will be excluded.`)) return;
    setBusy(true);
    setError('');
    try {
      const committed = await api.commitImport(preview.batch_id);
      setResult(committed);
      setPreview((current) => current ? {
        ...current,
        status: 'COMMITTED',
        result: {
          created: committed.created,
          updated: committed.updated,
          skipped: committed.skipped,
          rejected: committed.rejected,
          issued: committed.issued,
        },
        error_report_url: committed.error_report_url,
      } : current);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Import commit failed.');
    } finally {
      setBusy(false);
    }
  }

  const counters = result ?? preview;

  return (
    <div className="mx-auto max-w-5xl">
      <p className="text-sm font-medium text-accent">Data management</p>
      <h1 className="mt-2 text-3xl font-semibold tracking-tight text-ink">Import ticket records</h1>
      <p className="mt-2 text-sm leading-relaxed text-slate-500">Preview Google Sheets, CSV, or Excel rows, inspect issues, then explicitly commit valid rows. Payment authorization comes from the imported sheet status, not from this application.</p>
      {events.length === 0 && <p className="mt-5 rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">There are no active events. <Link to="/events" className="font-semibold underline">Create an event in Gatehouse</Link> first.</p>}
      <section className="mt-8 rounded-2xl border border-slate-200/80 bg-white p-6 sm:p-8">
        <label className="mb-5 block text-sm font-medium text-slate-700">Event for these records
          <select value={eventId} onChange={(event) => { setEventId(event.target.value); setPreview(null); setResult(null); }} className="field mt-2">
            <option value="">Choose an active event</option>
            {events.map((item) => <option key={item.id} value={item.id}>{item.name} — {item.venue}</option>)}
          </select>
        </label>
        <div className="grid min-h-48 place-items-center rounded-2xl border-2 border-dashed border-slate-200 bg-slate-50/70 px-5 py-8 text-center">
          <div>
            <span className="mx-auto grid h-12 w-12 place-items-center rounded-2xl bg-emerald-50 text-accent"><FileSpreadsheet /></span>
            <h2 className="mt-4 font-semibold text-ink">Upload CSV or XLSX</h2>
            <p className="mt-1 text-sm text-slate-500">Maximum 10 MB and 10,000 data rows.</p>
            <label className={`mt-5 inline-flex items-center gap-2 rounded-xl px-4 py-3 text-sm font-medium text-white ${!eventId || busy ? 'cursor-not-allowed bg-slate-400' : 'cursor-pointer bg-ink hover:bg-accent'}`}>
              <UploadCloud size={17} />{busy ? 'Working…' : 'Choose spreadsheet'}
              <input type="file" accept=".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" className="sr-only" onChange={upload} disabled={!eventId || busy} />
            </label>
          </div>
        </div>
        <div className="mt-5 rounded-2xl border border-emerald-100 bg-emerald-50/50 p-5">
          <div className="flex items-center gap-2 font-semibold text-ink"><Sheet size={18} className="text-accent" />Import from Google Sheets</div>
          <p className="mt-1 text-sm text-slate-600">Reads the backend-configured Google Sheet using its service account. Review a validation preview before confirming any database changes.</p>
          <div className="mt-4 flex flex-wrap gap-3">
            <label className="min-w-56 flex-1 text-sm font-medium text-slate-700">Worksheet
              <select value={worksheet} onChange={(event) => setWorksheet(event.target.value as 'Online_Tickets' | 'OnSpot_Tickets')} className="field mt-2">
                <option value="Online_Tickets">Online_Tickets</option>
                <option value="OnSpot_Tickets">OnSpot_Tickets</option>
              </select>
            </label>
            <button type="button" onClick={() => void previewGoogleSheet()} disabled={!eventId || busy} className="mt-auto inline-flex items-center justify-center gap-2 rounded-xl bg-ink px-4 py-3 text-sm font-medium text-white hover:bg-accent disabled:cursor-not-allowed disabled:opacity-50">
              <Sheet size={16} />{busy ? 'Reading sheet…' : 'Preview Google Sheet'}
            </button>
          </div>
          {events.length === 0 && <p className="mt-3 text-sm text-amber-800">Create or activate an event in Django admin before importing rows.</p>}
        </div>
        {error && <p role="alert" className="mt-4 rounded-xl bg-red-50 p-4 text-sm text-red-700">{error}</p>}
      </section>
      {preview && <section className="mt-6 rounded-2xl border border-slate-200/80 bg-white p-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div><h2 className="font-semibold text-ink">{result ? 'Import completed' : 'Review import preview'}</h2><p className="mt-1 text-sm text-slate-500">{preview.source_worksheet ? `${preview.source_worksheet} · ` : ''}{preview.total_rows} data rows · {preview.status.toLowerCase()}</p></div>
          <a href={preview.error_report_url} className="inline-flex items-center gap-2 rounded-lg border border-slate-200 px-3 py-2 text-sm font-medium text-slate-700 hover:border-accent"><Download size={16} />Download error report</a>
        </div>
        <div className="mt-5 grid gap-3 sm:grid-cols-4">
          {[
            ['Create', counters?.created ?? 0, 'bg-emerald-50 text-emerald-800'],
            ['Update', counters?.updated ?? 0, 'bg-blue-50 text-blue-800'],
            ['Skip', counters?.skipped ?? 0, 'bg-slate-100 text-slate-700'],
            ['Reject', counters?.rejected ?? 0, 'bg-amber-50 text-amber-800'],
            ['Verified payment', preview.verified_payment_rows, 'bg-emerald-50 text-emerald-800'],
            ['Email evidence', preview.email_evidence_rows, 'bg-teal-50 text-teal-800'],
            ...(result ? [['Issued', result.issued, 'bg-violet-50 text-violet-800']] : []),
          ].map(([label, count, color]) => <div key={label} className={`rounded-xl p-4 ${color}`}><p className="text-xs">{label} rows</p><p className="mt-1 text-2xl font-semibold">{count}</p></div>)}
        </div>
        {preview.unknown_columns.length > 0 && <p className="mt-4 text-sm text-amber-800">Unrecognized columns (ignored): {preview.unknown_columns.join(', ')}</p>}
        <div className="mt-5 max-h-80 overflow-auto rounded-xl border border-slate-100">
          <table className="w-full min-w-[720px] text-left text-sm">
            <thead className="sticky top-0 bg-slate-50 text-xs uppercase tracking-wide text-slate-500"><tr><th className="px-4 py-3">Row</th><th className="px-4 py-3">Ticket number</th><th className="px-4 py-3">Action</th><th className="px-4 py-3">Sheet payment status</th><th className="px-4 py-3">Email evidence</th><th className="px-4 py-3">Validation</th></tr></thead>
            <tbody className="divide-y divide-slate-100">
              {preview.rows.map((row) => <tr key={row.row}><td className="px-4 py-3 text-slate-500">{row.row}</td><td className="px-4 py-3 font-medium text-ink">{row.ticket_number || '—'}</td><td className="px-4 py-3">{row.action}</td><td className="px-4 py-3">{row.payment_status || '—'}{row.verified_payment && <span className="ml-2 font-medium text-emerald-700">Verified</span>}</td><td className="px-4 py-3">{row.email_delivery_evidence ? <span className="font-medium text-teal-700">SENT + timestamp</span> : '—'}</td><td className="px-4 py-3">{row.errors.length ? <span className="flex gap-2 text-red-700"><AlertTriangle size={15} className="mt-0.5 shrink-0" />{row.errors.join('; ')}</span> : <span className="text-emerald-700">Ready</span>}</td></tr>)}
              {preview.total_rows > preview.rows.length && <tr><td colSpan={6} className="px-4 py-3 text-center text-slate-500">Showing the first 200 rows. The downloadable error report includes all rejected rows.</td></tr>}
            </tbody>
          </table>
        </div>
        <div className="mt-5 rounded-xl bg-amber-50 p-4 text-sm leading-relaxed text-amber-900"><p className="flex items-center gap-2 font-semibold"><AlertTriangle size={17} />Safety rules</p><p className="mt-2">This application does not approve or reject payments. On explicit import commit, PAID, CAPTURED, or VERIFIED from the spreadsheet is recorded as the imported payment status and can authorize a new Gatehouse QR/PDF. A row with email_status=SENT and a valid email_sent_at remains an alternate authorization. Imported QR contents are never trusted, email is not sent automatically, and stale rows do not overwrite newer operational state.</p></div>
        {!result && preview.commit_available && <button onClick={() => void commit()} disabled={busy} className="mt-5 flex w-full items-center justify-center gap-2 rounded-xl bg-accent px-5 py-3.5 font-semibold text-white hover:bg-ink disabled:opacity-50"><CheckCircle2 size={18} />{busy ? 'Importing…' : `Confirm import ${preview.created + preview.updated} valid row(s)`}</button>}
        {!result && !preview.commit_available && <p className="mt-5 rounded-xl bg-slate-50 p-4 text-sm text-slate-600">There are no valid new or newer rows to commit.</p>}
      </section>}
    </div>
  );
}
