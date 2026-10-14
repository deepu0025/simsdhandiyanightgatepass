import { useState, type FormEvent } from 'react';
import { ArrowRight, TicketCheck } from 'lucide-react';

export default function LoginPage({ onLogin }: { onLogin: (email: string, password: string) => Promise<void> }) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      await onLogin(email, password);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Sign in failed.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="grid min-h-screen bg-ink lg:grid-cols-[1.1fr_0.9fr]">
      <section className="relative hidden overflow-hidden p-14 text-white lg:flex lg:flex-col lg:justify-between">
        <div className="absolute -left-32 -top-32 h-[34rem] w-[34rem] rounded-full border border-white/10" />
        <div className="absolute -left-16 -top-16 h-[26rem] w-[26rem] rounded-full border border-white/10" />
        <div className="relative flex items-center gap-3"><TicketCheck className="text-emerald-300" /><span className="font-semibold tracking-widest">GATEHOUSE</span></div>
        <div className="relative max-w-xl">
          <p className="mb-5 text-sm uppercase tracking-[0.25em] text-emerald-300">One calm control room</p>
          <h1 className="text-5xl font-semibold leading-tight">Every ticket.<br />Every entrance.<br /><span className="text-emerald-300">In sync.</span></h1>
          <p className="mt-6 max-w-md text-lg leading-relaxed text-slate-300">The secure operations workspace for event teams, admins, and gate staff.</p>
        </div>
        <p className="relative text-xs text-slate-500">Private event operations · Authorized staff only</p>
      </section>
      <section className="grid place-items-center bg-white px-6 py-12 sm:px-12">
        <form onSubmit={submit} className="w-full max-w-sm">
          <div className="mb-10 lg:hidden"><TicketCheck size={30} className="text-accent" /><p className="mt-3 font-semibold tracking-widest text-ink">GATEHOUSE</p></div>
          <p className="text-sm font-medium uppercase tracking-[0.18em] text-accent">Welcome back</p>
          <h2 className="mt-3 text-3xl font-semibold text-ink">Sign in to continue</h2>
          <p className="mt-2 text-sm text-slate-500">Use your event operations account.</p>
          <label className="mt-8 block text-sm font-medium text-slate-700">Work email
            <input required type="email" autoComplete="username" value={email} onChange={(event) => setEmail(event.target.value)} className="field mt-2" placeholder="you@event.org" />
          </label>
          <label className="mt-5 block text-sm font-medium text-slate-700">Password
            <input required type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} className="field mt-2" placeholder="Enter your password" />
          </label>
          {error && <p role="alert" className="mt-4 rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700">{error}</p>}
          <button disabled={busy} className="mt-7 flex w-full items-center justify-between rounded-xl bg-ink px-5 py-3.5 font-medium text-white transition hover:bg-accent disabled:opacity-60">
            {busy ? 'Signing in…' : 'Sign in'}<ArrowRight size={18} />
          </button>
          <p className="mt-5 text-xs leading-relaxed text-slate-500">Scanner and admin access are assigned by an administrator.</p>
        </form>
      </section>
    </main>
  );
}
