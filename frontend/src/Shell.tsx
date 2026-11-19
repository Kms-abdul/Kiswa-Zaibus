import { useEffect, useState } from 'react';
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { useRegisterSW } from 'virtual:pwa-register/react';
import { useAuth } from './auth';
import ReauthDialog from './ReauthDialog';
import { Btn, ErrorBoundary, Modal } from './ui/kit';
import { Palette, SHORTCUTS } from './ui/palette';
import { Icon, NAV_ICON } from './ui/icons';
import { api } from './api';

const CO_NAME: Record<string, string> = { KG: 'Kiswa Garments', ZE: 'Zaibus Enterprises' };

type Nav = { to: string; label: string; group: string; show: (can: (a: string, k: string) => boolean) => boolean; icon: string };
export const NAV: Nav[] = [
  { to: '/', label: 'Dashboard', group: 'Overview', show: () => true, icon: '⌂' },
  { to: '/approvals', label: 'Approvals', group: 'Overview', show: c => c('view', 'appr'), icon: '✓' },
  { to: '/pos', label: 'New bill', group: 'Sell', show: c => c('add', 'pos'), icon: '₹' },
  { to: '/invoices', label: 'Invoices & returns', group: 'Sell', show: c => c('view', 'pos'), icon: '≡' },
  { to: '/msea', label: 'MSEA vouchers', group: 'Sell', show: c => c('view', 'msea'), icon: '◎' },
  { to: '/online', label: 'Online orders', group: 'Sell', show: c => c('view', 'onl'), icon: '⇄' },
  { to: '/stock', label: 'Stock', group: 'Stock', show: c => c('view', 'inv'), icon: '▦' },
  { to: '/items', label: 'Items', group: 'Stock', show: c => c('view', 'inv'), icon: '◫' },
  { to: '/transfers', label: 'Transfers', group: 'Stock', show: c => c('view', 'inv'), icon: '↔' },
  { to: '/adjustments', label: 'Adjustments', group: 'Stock', show: c => c('view', 'inv'), icon: '±' },
  { to: '/purchasing', label: 'Purchasing', group: 'Stock', show: c => c('view', 'pur'), icon: '⊕' },
  { to: '/stock-audits', label: 'Stock audits', group: 'Stock', show: c => c('view', 'saudit'), icon: '☑' },
  { to: '/labels', label: 'Barcode labels', group: 'Stock', show: c => c('view', 'inv'), icon: '▥' },
  { to: '/cash', label: 'Cash custody', group: 'Money', show: c => c('view', 'cash'), icon: '◍' },
  { to: '/accounts', label: 'Accounts', group: 'Money', show: c => c('view', 'acc'), icon: '⊞' },
  { to: '/finance', label: 'Finance & tax', group: 'Money', show: c => c('view', 'fin'), icon: '§' },
  { to: '/paylinks', label: 'Payment links & QR', group: 'Money', show: c => c('view', 'ctl.pay'), icon: '⌗' },
  { to: '/reports', label: 'Reports', group: 'Money', show: c => c('view', 'rep'), icon: '◔' },
  { to: '/audit', label: 'Audit trail', group: 'Control', show: c => c('view', 'audit') || c('view', 'ctl.sec'), icon: '⚑' },
  { to: '/users', label: 'Users & access', group: 'Control', show: c => c('view', 'ctl.users') || c('users', 'ctl.users'), icon: '☺' },
  { to: '/setup', label: 'Setup & masters', group: 'Control', show: c => c('view', 'set') || c('view', 'inv') || c('view', 'acc') || c('view', 'ctl'), icon: '⚒' },
  { to: '/settings', label: 'Settings', group: 'Control', show: c => c('view', 'set') || c('view', 'ctl.int') || c('view', 'ctl.pay'), icon: '⚙' },
];
const MOBILE_PRIORITY = ['/', '/pos', '/online', '/approvals', '/stock', '/invoices', '/cash', '/reports'];

export default function Shell() {
  const { me, can, signOut, myBranches, location, setLocation } = useAuth();
  const [more, setMore] = useState(false);
  const [find, setFind] = useState(false);
  const [keys, setKeys] = useState(false);
  const [online, setOnline] = useState(navigator.onLine);
  const [installEvt, setInstallEvt] = useState<any>(null);
  const [alertCount, setAlertCount] = useState(0);
  useEffect(() => { api<any[]>('/alerts').then(a => setAlertCount(a.length)).catch(() => {}); }, []);
  const nav = useNavigate();
  const loc = useLocation();
  const { needRefresh: [needRefresh], updateServiceWorker } = useRegisterSW();
  useEffect(() => {
    const on = () => setOnline(true), off = () => setOnline(false);
    const inst = (e: Event) => { e.preventDefault(); setInstallEvt(e); };
    window.addEventListener('online', on); window.addEventListener('offline', off); window.addEventListener('beforeinstallprompt', inst);
    return () => { window.removeEventListener('online', on); window.removeEventListener('offline', off); window.removeEventListener('beforeinstallprompt', inst); };
  }, []);
  useEffect(() => { setMore(false); }, [loc.pathname]);
  useEffect(() => {
    const typing = (t: EventTarget | null) => t instanceof HTMLElement && (t.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(t.tagName));
    const k = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); setFind(true); return; }
      if (typing(e.target) || document.body.classList.contains('modal-open')) return;
      if (e.key === '/') { e.preventDefault(); setFind(true); }
      else if (e.key === '?') { e.preventDefault(); setKeys(true); }
    };
    document.addEventListener('keydown', k);
    return () => document.removeEventListener('keydown', k);
  }, []);
  const items = NAV.filter(n => n.show(can));
  const groups = [...new Set(items.map(i => i.group))];
  const bottom = MOBILE_PRIORITY.map(p => items.find(i => i.to === p)).filter(Boolean).slice(0, 4) as Nav[];
  const locs = [...new Map(myBranches.map(b => [b.location, b])).values()];
  const here = locs.find(b => b.location === location) || locs[0];
  const coNames = [...new Set(myBranches.filter(b => b.location === here?.location).map(b => b.company))].map(c => CO_NAME[c] || c);
  const page = [...NAV].sort((a, b) => b.to.length - a.to.length).find(n => n.to === '/' ? loc.pathname === '/' : loc.pathname.startsWith(n.to));
  const pageLabel = page?.label || (loc.pathname.startsWith('/profile') ? 'My account' : '');
  return (
    <div className="app">
      <aside className="rail" aria-label="Main menu">
        <div className="brand"><span className="mark">KZ</span><div><b>Kiswa · Zaibus</b><small>Hyderabad Head Office</small></div></div>
        <label className="rail-ctx" title="Working location">
          <span className="dotc" aria-hidden />
          <span className="rc-t"><b>{coNames.join(' & ') || 'Head Office'}</b><small>{here?.name || 'All locations'}</small></span>
          {locs.length > 1 && <select value={location} onChange={e => setLocation(e.target.value)} aria-label="Working location">
            {locs.map(b => <option key={b.location} value={b.location}>{b.name}</option>)}
          </select>}
          {locs.length > 1 && <Icon n="down" size={15} />}
        </label>
        <nav>
          {groups.map(g => (
            <div key={g} className="grp"><div className="gl">{g}</div>
              {items.filter(i => i.group === g).map(i => <NavLink key={i.to} to={i.to} end={i.to === '/'}><Icon n={NAV_ICON[i.to]} />{i.label}</NavLink>)}
            </div>
          ))}
        </nav>
        <div className="who"><span className="av" aria-hidden>{me!.name.split(' ').map(w => w[0]).slice(0, 2).join('')}</span><div className="who-t"><b>{me!.name}</b><small>{me!.role}{me!.read_only ? ' · read-only' : ''}</small>
          <div className="row"><button className="link" onClick={() => nav('/profile')}>My account</button><button className="link" onClick={signOut}>Sign out</button></div></div></div>
      </aside>
      <main>
        <header className="top">
          <span className="mark sm only-sm">KZ</span>
          <nav className="hcrumbs hide-sm" aria-label="You are here">
            <span>Head Office</span>{coNames.length > 0 && <><Icon n="chev" size={13} /><span>{coNames.join(' & ')}</span></>}
            {here && <><Icon n="chev" size={13} /><span>{here.name}</span></>}{pageLabel && <><Icon n="chev" size={13} /><b>{pageLabel}</b></>}
          </nav>
          {locs.length > 1 ? (
            <select className="ctx only-sm" value={location} onChange={e => setLocation(e.target.value)} aria-label="Working location">
              {locs.map(b => <option key={b.location} value={b.location}>{b.name}</option>)}
            </select>
          ) : <span className="ctx only-sm">{locs[0]?.name || 'Head Office'}</span>}
          <span className="grow" />
          <button className="searchbox" onClick={() => setFind(true)} aria-label="Quick search (Ctrl+K)">
            <Icon n="search" size={16} /><span className="sb-text">Search students, invoices, items…</span><kbd className="hide-sm">/</kbd></button>
          <span className={'sync hide-sm' + (online ? '' : ' off')} title={online ? 'Connected to the server' : 'No connection: saving is paused'}><i aria-hidden />{online ? 'Online' : 'Offline'}</span>
          <button className="iconbtn" onClick={() => nav('/?tab=alerts')} aria-label={`Alerts${alertCount ? `, ${alertCount} in the last 14 days` : ''}`} title="Alerts">
            <Icon n="bell" />{alertCount > 0 && <em className="badge">{alertCount > 99 ? '99+' : alertCount}</em>}</button>
          <button className="iconbtn hide-sm" onClick={() => setKeys(true)} aria-label="Keyboard shortcuts and help" title="Keyboard shortcuts (?)"><Icon n="help" /></button>
          {installEvt && <Btn onClick={async () => { installEvt.prompt(); await installEvt.userChoice; setInstallEvt(null); }}>Install app</Btn>}
          <button className="iconbtn only-sm" onClick={() => nav('/profile')} aria-label="My account"><Icon n="person" /></button>
        </header>
        {!online && <div className="banner bad">You are offline. Screens you opened stay visible; saving needs a connection.</div>}
        {needRefresh && <div className="banner info">A new version is ready. <button className="link" onClick={() => updateServiceWorker(true)}>Reload now</button></div>}
        <div className="page"><ErrorBoundary resetKey={loc.pathname}><Outlet /></ErrorBoundary></div>
      </main>
      <nav className="bottom" aria-label="Quick menu">
        {bottom.map(i => <NavLink key={i.to} to={i.to} end={i.to === '/'}><Icon n={NAV_ICON[i.to]} size={21} />{i.label.split(' ')[0]}</NavLink>)}
        <button onClick={() => setMore(true)} className={more ? 'active' : ''}><Icon n="menu" size={21} />More</button>
      </nav>
      {more && (
        <div className="modal-bg" onMouseDown={e => { if (e.target === e.currentTarget) setMore(false); }}>
          <div className="modal sheet-menu" role="dialog" aria-label="All screens">
            <div className="modal-head"><h3>{me!.name}</h3><button className="x" onClick={() => setMore(false)} aria-label="Close">×</button></div>
            <div className="modal-body">
              {groups.map(g => (
                <div key={g} className="menu-grp"><div className="gl">{g}</div>
                  <div className="menu-grid">{items.filter(i => i.group === g).map(i => <NavLink key={i.to} to={i.to} end={i.to === '/'}><Icon n={NAV_ICON[i.to]} />{i.label}</NavLink>)}</div>
                </div>
              ))}
              <div className="row end"><Btn onClick={() => nav('/profile')}>My account</Btn><Btn onClick={signOut}>Sign out</Btn></div>
            </div>
          </div>
        </div>
      )}
      {find && <Palette onClose={() => setFind(false)} />}
      {keys && <Modal title="Keyboard shortcuts" onClose={() => setKeys(false)}>
        <table className="grid keys"><tbody>{SHORTCUTS.map(([k, v]) => <tr key={k}><td><kbd>{k}</kbd></td><td>{v}</td></tr>)}</tbody></table>
        <p className="muted small">Every screen explains itself under its title. The user guide is in docs/USER_WORKFLOW.md; for problems ask IT with the message shown on screen.</p>
      </Modal>}
      <ReauthDialog />
    </div>
  );
}
