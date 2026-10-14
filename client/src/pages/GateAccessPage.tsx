import { useEffect, useState } from 'react';
import { KeyRound, ShieldCheck, UserRoundPlus } from 'lucide-react';
import { api, type GateId, type ScannerAccount } from '../api';

const gates: GateId[] = ['GATE_1', 'GATE_2', 'GATE_3', 'GATE_4'];

type Draft = { name: string; email: string; password: string };

const emptyDraft = (): Draft => ({ name: '', email: '', password: '' });

export default function GateAccessPage() {
  const [accounts, setAccounts] = useState<ScannerAccount[]>([]);
  const [drafts, setDrafts] = useState<Record<string, Draft>>(() =>
    Object.fromEntries(gates.map((gate) => [gate, emptyDraft()])),
  );
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busyGate, setBusyGate] = useState('');
  const [assignments, setAssignments] = useState<Record<number, GateId>>({});

  async function refresh() {
    setAccounts(await api.scannerAccounts());
  }

  useEffect(() => {
    void refresh().catch((reason: unknown) => {
      setError(reason instanceof Error ? reason.message : 'Could not load gate accounts.');
    });
  }, []);

  function updateDraft(gate: GateId, field: keyof Draft, value: string) {
    setDrafts((current) => ({
      ...current,
      [gate]: { ...current[gate], [field]: value },
    }));
  }

  async function createAccount(event: React.FormEvent, gate: GateId) {
    event.preventDefault();
    const draft = drafts[gate];
    setBusyGate(gate);
    setError('');
    setNotice('');
    try {
      await api.createScannerAccount({ ...draft, gate });
      await refresh();
      setDrafts((current) => ({ ...current, [gate]: emptyDraft() }));
      setNotice(`${gate.replace('_', ' ')} scanner account created.`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not create the scanner account.');
    } finally {
      setBusyGate('');
    }
  }

  async function updateAccount(account: ScannerAccount, changes: { password?: string; is_active?: boolean }) {
    setBusyGate(account.gate);
    setError('');
    setNotice('');
    try {
      await api.updateScannerAccount(account.id, changes);
      await refresh();
      setDrafts((current) => ({ ...current, [account.gate]: { ...current[account.gate], password: '' } }));
      setNotice(`${account.gate.replace('_', ' ')} account updated.`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not update the scanner account.');
    } finally {
      setBusyGate('');
    }
  }

  async function assignExisting(account: ScannerAccount) {
    const gate = assignments[account.id];
    if (!gate) return;
    setBusyGate(gate);
    setError('');
    setNotice('');
    try {
      await api.updateScannerAccount(account.id, { gate });
      await refresh();
      setNotice(`${account.email} assigned to ${gate.replace('_', ' ')}.`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not assign the existing scanner account.');
    } finally {
      setBusyGate('');
    }
  }

  const unassignedAccounts = accounts.filter((account) => !account.gate);
  const availableGates = gates.filter((gate) => !accounts.some((account) => account.gate === gate));

  return (
    <div className="mx-auto max-w-5xl">
      <header className="mb-6">
        <p className="text-sm font-medium text-accent">Operations / Gate access</p>
        <h1 className="mt-2 text-3xl font-semibold tracking-tight text-ink">Four gate scanners</h1>
        <p className="mt-2 max-w-2xl text-sm text-slate-500">
          Create a separate login for each gate. Scanner accounts can only record check-ins for their assigned gate.
          Passwords are never shown after saving.
        </p>
      </header>
      {error && <p role="alert" className="mb-4 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</p>}
      {notice && <p role="status" className="mb-4 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800">{notice}</p>}
      <section className="grid gap-4 sm:grid-cols-2">
        {gates.map((gate, index) => {
          const account = accounts.find((item) => item.gate === gate);
          const draft = drafts[gate];
          const busy = busyGate === gate;
          return (
            <article key={gate} className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-sm">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <p className="text-xs font-semibold uppercase tracking-wider text-accent">Scanner panel {index + 1}</p>
                  <h2 className="mt-1 text-xl font-semibold text-ink">Gate {index + 1}</h2>
                </div>
                {account ? <span className={`rounded-full px-2.5 py-1 text-xs font-semibold ${account.is_active ? 'bg-emerald-50 text-emerald-700' : 'bg-slate-100 text-slate-500'}`}>{account.is_active ? 'Active' : 'Disabled'}</span> : <span className="rounded-full bg-amber-50 px-2.5 py-1 text-xs font-semibold text-amber-700">Not set up</span>}
              </div>
              {account ? (
                <div className="mt-5">
                  <div className="rounded-xl bg-slate-50 p-3">
                    <p className="font-medium text-ink">{account.name || 'Gate scanner'}</p>
                    <p className="mt-1 break-all text-sm text-slate-500">{account.email}</p>
                  </div>
                  <label className="mt-4 block text-sm font-medium text-slate-700" htmlFor={`${gate}-password`}>Reset password</label>
                  <div className="mt-1 flex gap-2">
                    <input
                      id={`${gate}-password`}
                      className="field min-w-0"
                      type="password"
                      autoComplete="new-password"
                      minLength={12}
                      value={draft.password}
                      onChange={(event) => updateDraft(gate, 'password', event.target.value)}
                      placeholder="At least 12 characters"
                    />
                    <button
                      type="button"
                      disabled={busy || draft.password.length < 12}
                      onClick={() => void updateAccount(account, { password: draft.password })}
                      className="grid min-h-12 shrink-0 place-items-center rounded-xl bg-ink px-4 text-white disabled:opacity-40"
                      aria-label={`Reset ${gate.replace('_', ' ')} password`}
                      title="Save new password"
                    ><KeyRound size={18} /></button>
                  </div>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => void updateAccount(account, { is_active: !account.is_active })}
                    className="mt-3 min-h-11 w-full rounded-xl border border-slate-200 px-4 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                  >{account.is_active ? 'Disable this login' : 'Enable this login'}</button>
                </div>
              ) : (
                <form className="mt-5 space-y-3" onSubmit={(event) => void createAccount(event, gate)}>
                  <label className="block text-sm font-medium text-slate-700">Scanner name
                    <input required className="field mt-1" value={draft.name} onChange={(event) => updateDraft(gate, 'name', event.target.value)} placeholder={`Gate ${index + 1} attendant`} />
                  </label>
                  <label className="block text-sm font-medium text-slate-700">Login ID (email)
                    <input required type="email" className="field mt-1" value={draft.email} onChange={(event) => updateDraft(gate, 'email', event.target.value)} placeholder="gate1@example.com" />
                  </label>
                  <label className="block text-sm font-medium text-slate-700">Initial password
                    <input required type="password" autoComplete="new-password" minLength={12} className="field mt-1" value={draft.password} onChange={(event) => updateDraft(gate, 'password', event.target.value)} placeholder="At least 12 characters" />
                  </label>
                  <button disabled={busy} className="flex min-h-12 w-full items-center justify-center gap-2 rounded-xl bg-accent px-4 font-medium text-white disabled:opacity-50">
                    {busy ? <ShieldCheck size={18} /> : <UserRoundPlus size={18} />}
                    {busy ? 'Saving…' : `Create Gate ${index + 1} login`}
                  </button>
                </form>
              )}
            </article>
          );
        })}
      </section>
      {unassignedAccounts.length > 0 && (
        <section className="mt-5 rounded-2xl border border-amber-200 bg-amber-50 p-5">
          <h2 className="font-semibold text-amber-950">Existing scanner logins without a gate</h2>
          <p className="mt-1 text-sm text-amber-900">Assign each existing account to an open gate before it can scan tickets.</p>
          <div className="mt-4 space-y-3">
            {unassignedAccounts.map((account) => {
              const selectedGate = assignments[account.id] || availableGates[0];
              return (
                <div key={account.id} className="flex flex-col gap-2 rounded-xl bg-white p-3 sm:flex-row sm:items-center">
                  <div className="min-w-0 flex-1">
                    <p className="font-medium text-ink">{account.name || 'Gate scanner'}</p>
                    <p className="break-all text-sm text-slate-500">{account.email}</p>
                  </div>
                  {availableGates.length ? <>
                    <select aria-label={`Assign ${account.email} to gate`} className="field sm:max-w-40" value={selectedGate} onChange={(event) => setAssignments((current) => ({ ...current, [account.id]: event.target.value as GateId }))}>
                      {availableGates.map((gate) => <option key={gate} value={gate}>{gate.replace('_', ' ')}</option>)}
                    </select>
                    <button type="button" disabled={busyGate === selectedGate} onClick={() => void assignExisting(account)} className="min-h-11 rounded-xl bg-ink px-4 text-sm font-medium text-white disabled:opacity-50">Assign gate</button>
                  </> : <span className="text-sm text-amber-800">All four gates already have an account.</span>}
                </div>
              );
            })}
          </div>
        </section>
      )}
    </div>
  );
}
