import { Activity, CalendarDays, ClipboardPlus, LayoutDashboard, LogOut, ScanLine, Upload, TicketCheck, UsersRound } from 'lucide-react';
import { NavLink } from 'react-router-dom';
import type { ReactNode } from 'react';
import type { User } from '../api';

export default function Shell({
  user,
  onSignOut,
  children,
}: {
  user: User;
  onSignOut: () => void;
  children: ReactNode;
}) {
  return (
    <div className="min-h-screen bg-canvas lg:flex">
      <aside className="flex w-full flex-col bg-ink text-white lg:fixed lg:inset-y-0 lg:w-64">
        <div className="px-6 py-7">
          <div className="flex items-center gap-3">
            <span className="grid h-10 w-10 place-items-center rounded-xl bg-emerald-500/15 text-emerald-300"><TicketCheck size={21} /></span>
            <div><p className="font-semibold tracking-wide">GATEHOUSE</p><p className="text-xs text-slate-400">Event operations</p></div>
          </div>
        </div>
        <nav className="flex flex-wrap gap-2 px-4 pb-4 lg:flex-col">
          <NavLink to="/" end className={({ isActive }) => `nav-item ${isActive ? 'nav-active' : ''}`}><LayoutDashboard size={18} />Overview</NavLink>
          <NavLink to="/events" className={({ isActive }) => `nav-item ${isActive ? 'nav-active' : ''}`}><CalendarDays size={18} />Events</NavLink>
          <NavLink to="/import" className={({ isActive }) => `nav-item ${isActive ? 'nav-active' : ''}`}><Upload size={18} />Import data</NavLink>
          <NavLink to="/on-spot" className={({ isActive }) => `nav-item ${isActive ? 'nav-active' : ''}`}><ClipboardPlus size={18} />On-spot registration</NavLink>
          <NavLink to="/scanner" className="nav-item"><Activity size={18} />Scanner panel</NavLink>
          <NavLink to="/gate-access" className={({ isActive }) => `nav-item ${isActive ? 'nav-active' : ''}`}><ScanLine size={18} />Gate scanners</NavLink>
          <NavLink to="/desk-access" className={({ isActive }) => `nav-item ${isActive ? 'nav-active' : ''}`}><UsersRound size={18} />Registration desks</NavLink>
        </nav>
        <div className="mt-auto hidden border-t border-white/10 p-5 lg:block">
          <p className="truncate text-sm font-medium">{user.name || user.email}</p>
          <p className="mb-4 text-xs text-slate-400">Administrator</p>
          <button onClick={onSignOut} className="flex items-center gap-2 text-sm text-slate-300 hover:text-white"><LogOut size={16} />Sign out</button>
        </div>
      </aside>
      <main className="min-h-screen px-4 py-7 sm:px-8 lg:ml-64 lg:px-10">{children}</main>
    </div>
  );
}
