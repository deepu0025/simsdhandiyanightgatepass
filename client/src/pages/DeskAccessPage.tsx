import { useEffect, useState } from 'react';
import { KeyRound, UserRoundPlus } from 'lucide-react';
import { api, type DeskAccount, type DeskId } from '../api';

const desks: DeskId[] = ['DESK_1', 'DESK_2'];
type Draft = { name: string; email: string; password: string };
const emptyDraft = (): Draft => ({ name: '', email: '', password: '' });

export default function DeskAccessPage() {
  const [accounts, setAccounts] = useState<DeskAccount[]>([]);
  const [drafts, setDrafts] = useState<Record<DeskId, Draft>>({
    DESK_1: emptyDraft(),
    DESK_2: emptyDraft(),
  });
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busyDesk, setBusyDesk] = useState<DeskId | ''>('');

  async function refresh() {
    setAccounts(await api.deskAccounts());
  }

  useEffect(() => {
    void refresh().catch((reason: unknown) => {
      setError(reason instanceof Error ? reason.message : 'Could not load registration desk accounts.');
    });
  }, []);

  function updateDraft(desk: DeskId, field: keyof Draft, value: string) {
    setDrafts((current) => ({ ...current, [desk]: { ...current[desk], [field]: value } }));
  }

  async function createAccount(event: React.FormEvent, desk: DeskId) {
    event.preventDefault();
    setBusyDesk(desk);
    setError('');
    setNotice('');
    try {
      await api.createDeskAccount({ ...drafts[desk], desk });
      await refresh();
      setDrafts((current) => ({ ...current, [desk]: emptyDraft() }));
      setNotice(`${desk.replace('_', ' ')} login created.`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not create the desk login.');
    } finally {
      setBusyDesk('');
    }
  }

  async function updateAccount(account: DeskAccount, changes: { password?: string; is_active?: boolean }) {
    setBusyDesk(account.desk || '');
    setError('');
    setNotice('');
    try {
      await api.updateDeskAccount(account.id, changes);
      await refresh();
      const desk = account.desk;
      if (desk) {
        setDrafts((current) => ({ ...current, [desk]: { ...current[desk], password: '' } }));
      }
      setNotice(`${account.desk?.replace('_', ' ') || 'Desk'} account updated.`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not update the desk login.');
    } finally {
      setBusyDesk('');
    }
  }

  return (
    <div className="mx-auto max-w-5xl">
      <header className="mb-6">
        <p className="text-sm font-medium text-accent">Operations / Registration desks</p>
        <h1 className="mt-2 text-3xl font-semibold tracking-tight text-ink">Two on-spot desks</h1>
        <p className="mt-2 max-w-2xl text-sm text-slate-500">Create a unique login for each desk. Desk users can register attendees and see active events, but cannot access admin or scanner operations. Passwords are never shown after saving.</p>
      </header>
      {error && <p role="alert" className="mb-4 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</p>}
      {notice && <p role="status" className="mb-4 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800">{notice}</p>}
      <section className="grid gap-4 sm:grid-cols-2">
        {desks.map((desk, index) => {
          const account = accounts.find((item) => item.desk === desk);
          const draft = drafts[desk];
          const busy = busyDesk === desk;
          return (
            <article key={desk} className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-sm">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <p className="text-xs font-semibold uppercase tracking-wider text-accent">Registration panel {index + 1}</p>
                  <h2 className="mt-1 text-xl font-semibold text-ink">Desk {index + 1}</h2>
                </div>
                {account ? <span className={`rounded-full px-2.5 py-1 text-xs font-semibold ${account.is_active ? 'bg-emerald-50 text-emerald-700' : 'bg-slate-100 text-slate-500'}`}>{account.is_active ? 'Active' : 'Disabled'}</span> : <span className="rounded-full bg-amber-50 px-2.5 py-1 text-xs font-semibold text-amber-700">Not set up</span>}
              </div>
              {account ? (
                <div className="mt-5">
                  <div className="rounded-xl bg-slate-50 p-3">
                    <p className="font-medium text-ink">{account.name || 'Registration attendant'}</p>
                    <p className="mt-1 break-all text-sm text-slate-500">{account.email}</p>
                  </div>
                  <label className="mt-4 block text-sm font-medium text-slate-700" htmlFor={`${desk}-password`}>Reset password</label>
                  <div className="mt-1 flex gap-2">
                    <input
                      id={`${desk}-password`}
                      className="field min-w-0"
                      type="password"
                      autoComplete="new-password"
                      minLength={12}
                      value={draft.password}
                      onChange={(event) => updateDraft(desk, 'password', event.target.value)}
                      placeholder="At least 12 characters"
                    />
                    <button type="button" disabled={busy || draft.password.length < 12} onClick={() => void updateAccount(account, { password: draft.password })} className="grid min-h-12 shrink-0 place-items-center rounded-xl bg-ink px-4 text-white disabled:opacity-40" aria-label={`Reset ${desk.replace('_', ' ')} password`}><KeyRound size={18} /></button>
                  </div>
                  <button type="button" disabled={busy} onClick={() => void updateAccount(account, { is_active: !account.is_active })} className="mt-3 min-h-11 w-full rounded-xl border border-slate-200 px-4 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50">{account.is_active ? 'Disable this login' : 'Enable this login'}</button>
                </div>
              ) : (
                <form className="mt-5 space-y-3" onSubmit={(event) => void createAccount(event, desk)}>
                  <label className="block text-sm font-medium text-slate-700">Attendant name
                    <input required className="field mt-1" value={draft.name} onChange={(event) => updateDraft(desk, 'name', event.target.value)} placeholder={`Desk ${index + 1} attendant`} />
                  </label>
                  <label className="block text-sm font-medium text-slate-700">Unique login ID (email)
                    <input required type="email" className="field mt-1" value={draft.email} onChange={(event) => updateDraft(desk, 'email', event.target.value)} placeholder={`desk${index + 1}@example.com`} />
                  </label>
                  <label className="block text-sm font-medium text-slate-700">Initial password
                    <input required type="password" autoComplete="new-password" minLength={12} className="field mt-1" value={draft.password} onChange={(event) => updateDraft(desk, 'password', event.target.value)} placeholder="At least 12 characters" />
                  </label>
                  <button disabled={busy} className="flex min-h-12 w-full items-center justify-center gap-2 rounded-xl bg-accent px-4 font-medium text-white disabled:opacity-50">
                    <UserRoundPlus size={18} />{busy ? 'Saving…' : `Create Desk ${index + 1} login`}
                  </button>
                </form>
              )}
            </article>
          );
        })}
      </section>
    </div>
  );
}
