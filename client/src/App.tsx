import { lazy, Suspense, useEffect, useState } from 'react';
import { Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom';
import { api, type User } from './api';
import LoginPage from './pages/LoginPage';
import Shell from './components/Shell';

const AdminDashboard = lazy(() => import('./pages/AdminDashboard'));
const EventsPage = lazy(() => import('./pages/EventsPage'));
const ImportPage = lazy(() => import('./pages/ImportPage'));
const OnSpotPage = lazy(() => import('./pages/OnSpotPage'));
const ScannerPage = lazy(() => import('./pages/ScannerPage'));
const GateAccessPage = lazy(() => import('./pages/GateAccessPage'));
const DeskAccessPage = lazy(() => import('./pages/DeskAccessPage'));

export default function App() {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const location = useLocation();
  const navigate = useNavigate();

  useEffect(() => {
    api.me()
      .then(setUser)
      .catch(() => setUser(null))
      .finally(() => setLoading(false));
  }, [location.pathname]);

  async function signIn(email: string, password: string) {
    const nextUser = await api.login(email, password);
    setUser(nextUser);
    navigate(nextUser.role === 'SCANNER' ? '/scanner' : nextUser.role === 'DESK' ? '/on-spot' : '/');
  }

  async function signOut() {
    await api.logout();
    setUser(null);
    navigate('/login');
  }

  if (loading) return <div className="grid min-h-screen place-items-center text-sm text-slate-500">Connecting to operations…</div>;
  const pending = <div className="grid min-h-screen place-items-center text-sm text-slate-500">Loading operations panel…</div>;

  return (
    <Routes>
      <Route path="/login" element={user ? <Navigate to={user.role === 'SCANNER' ? '/scanner' : user.role === 'DESK' ? '/on-spot' : '/'} replace /> : <LoginPage onLogin={signIn} />} />
      <Route path="/scanner" element={user?.role === 'SCANNER' || user?.role === 'ADMIN' ? <Suspense fallback={pending}><ScannerPage user={user} onSignOut={signOut} /></Suspense> : user ? <Navigate to="/on-spot" replace /> : <Navigate to="/login" replace />} />
      <Route
        path="/"
        element={
          user?.role === 'ADMIN' ? (
            <Shell user={user} onSignOut={signOut}>
              <Suspense fallback={pending}><AdminDashboard /></Suspense>
            </Shell>
          ) : user ? <Navigate to={user.role === 'SCANNER' ? '/scanner' : '/on-spot'} replace /> : <Navigate to="/login" replace />
        }
      />
      <Route
        path="/import"
        element={
          user?.role === 'ADMIN' ? (
            <Shell user={user} onSignOut={signOut}>
              <Suspense fallback={pending}><ImportPage /></Suspense>
            </Shell>
          ) : user ? <Navigate to={user.role === 'SCANNER' ? '/scanner' : '/on-spot'} replace /> : <Navigate to="/login" replace />
        }
      />
      <Route
        path="/events"
        element={
          user?.role === 'ADMIN' ? (
            <Shell user={user} onSignOut={signOut}>
              <Suspense fallback={pending}><EventsPage /></Suspense>
            </Shell>
          ) : user ? <Navigate to={user.role === 'SCANNER' ? '/scanner' : '/on-spot'} replace /> : <Navigate to="/login" replace />
        }
      />
      <Route
        path="/on-spot"
        element={
          user?.role === 'DESK' ? (
            <Suspense fallback={pending}><OnSpotPage user={user} onSignOut={signOut} /></Suspense>
          ) : user?.role === 'ADMIN' ? (
            <Shell user={user} onSignOut={signOut}>
              <Suspense fallback={pending}><OnSpotPage /></Suspense>
            </Shell>
          ) : user ? <Navigate to={user.role === 'SCANNER' ? '/scanner' : '/login'} replace /> : <Navigate to="/login" replace />
        }
      />
      <Route
        path="/desk-access"
        element={
          user?.role === 'ADMIN' ? (
            <Shell user={user} onSignOut={signOut}>
              <Suspense fallback={pending}><DeskAccessPage /></Suspense>
            </Shell>
          ) : user ? <Navigate to={user.role === 'SCANNER' ? '/scanner' : '/on-spot'} replace /> : <Navigate to="/login" replace />
        }
      />
      <Route
        path="/gate-access"
        element={
          user?.role === 'ADMIN' ? (
            <Shell user={user} onSignOut={signOut}>
              <Suspense fallback={pending}><GateAccessPage /></Suspense>
            </Shell>
          ) : user ? <Navigate to={user.role === 'SCANNER' ? '/scanner' : '/on-spot'} replace /> : <Navigate to="/login" replace />
        }
      />
      <Route path="*" element={<Navigate to={user ? (user.role === 'ADMIN' ? '/' : user.role === 'SCANNER' ? '/scanner' : '/on-spot') : '/login'} replace />} />
    </Routes>
  );
}
