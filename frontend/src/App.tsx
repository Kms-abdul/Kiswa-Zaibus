import { lazy, Suspense } from 'react';
import { Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { useAuth } from './auth';
import Shell from './Shell';
import SignIn from './pages/SignIn';
import { ErrorBoundary, Skeleton } from './ui/kit';

const Dashboard = lazy(() => import('./pages/Dashboard'));
const Approvals = lazy(() => import('./pages/Approvals'));
const Pos = lazy(() => import('./pages/Pos'));
const Invoices = lazy(() => import('./pages/Invoices'));
const Msea = lazy(() => import('./pages/Msea'));
const Online = lazy(() => import('./pages/Online'));
const Stock = lazy(() => import('./pages/Stock'));
const Items = lazy(() => import('./pages/Items'));
const Transfers = lazy(() => import('./pages/Transfers'));
const Adjustments = lazy(() => import('./pages/Adjustments'));
const Purchasing = lazy(() => import('./pages/Purchasing'));
const Cash = lazy(() => import('./pages/Cash'));
const Accounts = lazy(() => import('./pages/Accounts'));
const Reports = lazy(() => import('./pages/Reports'));
const Audit = lazy(() => import('./pages/Audit'));
const Users = lazy(() => import('./pages/Users'));
const Settings = lazy(() => import('./pages/Settings'));
const Profile = lazy(() => import('./pages/Profile'));
const Shop = lazy(() => import('./shop/Shop'));
const Setup = lazy(() => import('./pages/Setup'));
const StockAudits = lazy(() => import('./pages/StockAudits'));
const Finance = lazy(() => import('./pages/Finance'));
const Labels = lazy(() => import('./pages/Labels'));
const PayLinks = lazy(() => import('./pages/PayLinks'));
const PayPublic = lazy(() => import('./pages/PayPublic'));

export default function App() {
  const { me, loading, can } = useAuth();
  const loc = useLocation();
  if (loc.pathname.startsWith('/pay/')) return <ErrorBoundary><Suspense fallback={<div className="boot">Loading…</div>}><Routes><Route path="/pay/:id" element={<PayPublic />} /></Routes></Suspense></ErrorBoundary>;
  if (loc.pathname.startsWith('/shop')) return <ErrorBoundary><Suspense fallback={<div className="boot">Loading…</div>}><Shop /></Suspense></ErrorBoundary>;
  if (loading) return <div className="boot">Loading…</div>;
  if (!me) return <SignIn />;
  const g = (ok: boolean, el: JSX.Element) => (ok ? el : <Navigate to="/" replace />);
  return (
    <Suspense fallback={<div className="page"><Skeleton /></div>}>
      <Routes>
        <Route element={<Shell />}>
          <Route index element={<Dashboard />} />
          <Route path="approvals/*" element={g(can('view', 'appr'), <Approvals />)} />
          <Route path="pos" element={g(can('add', 'pos'), <Pos />)} />
          <Route path="invoices/*" element={g(can('view', 'pos'), <Invoices />)} />
          <Route path="msea/*" element={g(can('view', 'msea'), <Msea />)} />
          <Route path="online/*" element={g(can('view', 'onl'), <Online />)} />
          <Route path="stock" element={g(can('view', 'inv'), <Stock />)} />
          <Route path="items/*" element={g(can('view', 'inv'), <Items />)} />
          <Route path="transfers/*" element={g(can('view', 'inv'), <Transfers />)} />
          <Route path="adjustments/*" element={g(can('view', 'inv'), <Adjustments />)} />
          <Route path="purchasing/*" element={g(can('view', 'pur'), <Purchasing />)} />
          <Route path="cash/*" element={g(can('view', 'cash'), <Cash />)} />
          <Route path="accounts/*" element={g(can('view', 'acc'), <Accounts />)} />
          <Route path="reports/*" element={g(can('view', 'rep'), <Reports />)} />
          <Route path="audit" element={g(can('view', 'audit') || can('view', 'ctl.sec'), <Audit />)} />
          <Route path="users" element={g(can('view', 'ctl.users') || can('users', 'ctl.users'), <Users />)} />
          <Route path="settings" element={g(can('view', 'set') || can('view', 'ctl.int') || can('view', 'ctl.pay'), <Settings />)} />
          <Route path="setup" element={g(can('view', 'set') || can('view', 'inv') || can('view', 'acc') || can('view', 'ctl'), <Setup />)} />
          <Route path="labels" element={g(can('view', 'inv'), <Labels />)} />
          <Route path="stock-audits/*" element={g(can('view', 'saudit'), <StockAudits />)} />
          <Route path="finance/*" element={g(can('view', 'fin'), <Finance />)} />
          <Route path="paylinks" element={g(can('view', 'ctl.pay'), <PayLinks />)} />
          <Route path="profile" element={<Profile />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </Suspense>
  );
}
