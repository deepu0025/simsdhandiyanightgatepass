import { useEffect, useState, type FormEvent } from 'react';
import { CalendarPlus, CheckCircle2, CircleOff, MapPin } from 'lucide-react';
import { Link } from 'react-router-dom';
import { api, type EventRecord } from '../api';

export default function EventsPage() {
  const [events, setEvents] = useState<EventRecord[]>([]);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);

  async function refresh() {
    setEvents(await api.events());
  }

  useEffect(() => {
    void refresh().catch((reason: unknown) => {
      setError(reason instanceof Error ? reason.message : 'Could not load events.');
    });
  }, []);

  async function createEvent(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    const startsAt = String(form.get('starts_at') || '');
    const endsAt = String(form.get('ends_at') || '');
    setBusy(true);
    setError('');
    setNotice('');
    try {
      await api.createEvent({
        name: String(form.get('name') || '').trim(),
        venue: String(form.get('venue') || '').trim(),
        starts_at: new Date(startsAt).toISOString(),
        ends_at: new Date(endsAt).toISOString(),
        instructions: String(form.get('instructions') || '').trim(),
      });
      await refresh();
      formElement.reset();
      setNotice('Event created and activated. It is now available on the import and on-spot pages.');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not create the event.');
    } finally {
      setBusy(false);
    }
  }

  async function toggleEvent(item: EventRecord) {
    setError('');
    setNotice('');
    try {
      await api.updateEvent(item.id, { is_active: !item.is_active });
      await refresh();
      setNotice(`${item.name} is now ${item.is_active ? 'inactive' : 'active'}.`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not update the event.');
    }
  }

  return (
    <div className="mx-auto max-w-5xl">
      <header className="mb-6">
        <p className="text-sm font-medium text-accent">Operations / Events</p>
        <h1 className="mt-2 text-3xl font-semibold tracking-tight text-ink">Manage events</h1>
        <p className="mt-2 text-sm text-slate-500">Create an event here to enable imports and on-spot registrations. No server shell or Django admin access is needed.</p>
      </header>
      {error && <p role="alert" className="mb-4 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</p>}
      {notice && <p role="status" className="mb-4 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800">{notice}</p>}

      <section className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-sm sm:p-7">
        <h2 className="flex items-center gap-2 text-lg font-semibold text-ink"><CalendarPlus size={20} className="text-accent" />Create an active event</h2>
        <form onSubmit={(event) => void createEvent(event)} className="mt-5 grid gap-4 sm:grid-cols-2">
          <label className="text-sm font-medium text-slate-700">Event name
            <input name="name" required maxLength={200} className="field mt-1" placeholder="Event name" />
          </label>
          <label className="text-sm font-medium text-slate-700">Venue
            <input name="venue" required maxLength={240} className="field mt-1" placeholder="Venue" />
          </label>
          <label className="text-sm font-medium text-slate-700">Starts at
            <input name="starts_at" type="datetime-local" required className="field mt-1" />
          </label>
          <label className="text-sm font-medium text-slate-700">Ends at
            <input name="ends_at" type="datetime-local" required className="field mt-1" />
          </label>
          <label className="text-sm font-medium text-slate-700 sm:col-span-2">Entry instructions (optional)
            <textarea name="instructions" maxLength={5000} rows={3} className="field mt-1" placeholder="Instructions shown on issued tickets" />
          </label>
          <button disabled={busy} className="flex min-h-12 items-center justify-center gap-2 rounded-xl bg-accent px-4 font-medium text-white disabled:opacity-50 sm:col-span-2">
            <CalendarPlus size={18} />{busy ? 'Creating…' : 'Create event'}
          </button>
        </form>
      </section>

      <section className="mt-6">
        <h2 className="text-lg font-semibold text-ink">Events</h2>
        {events.length === 0 ? (
          <p className="mt-3 rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">No events yet. Create one above to unlock ticket import and on-spot registration.</p>
        ) : (
          <div className="mt-3 grid gap-3 sm:grid-cols-2">
            {events.map((item) => (
              <article key={item.id} className="rounded-2xl border border-slate-200/80 bg-white p-4 sm:p-5">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="break-words font-semibold text-ink">{item.name}</p>
                    <p className="mt-1 flex items-center gap-1.5 text-sm text-slate-500"><MapPin size={15} />{item.venue}</p>
                  </div>
                  <span className={`shrink-0 rounded-full px-2.5 py-1 text-xs font-semibold ${item.is_active ? 'bg-emerald-50 text-emerald-700' : 'bg-slate-100 text-slate-500'}`}>{item.is_active ? 'Active' : 'Inactive'}</span>
                </div>
                <p className="mt-3 text-sm text-slate-600">{new Date(item.starts_at).toLocaleString()} – {new Date(item.ends_at).toLocaleString()}</p>
                <button type="button" onClick={() => void toggleEvent(item)} className="mt-4 flex min-h-10 w-full items-center justify-center gap-2 rounded-xl border border-slate-200 px-3 text-sm font-medium text-slate-700 hover:bg-slate-50">
                  {item.is_active ? <><CircleOff size={16} />Deactivate</> : <><CheckCircle2 size={16} />Activate</>}
                </button>
              </article>
            ))}
          </div>
        )}
      </section>
      <p className="mt-6 text-sm text-slate-500">After creating an event, go to <Link className="font-medium text-accent underline" to="/import">Import data</Link> or <Link className="font-medium text-accent underline" to="/on-spot">On-spot registration</Link>.</p>
    </div>
  );
}
