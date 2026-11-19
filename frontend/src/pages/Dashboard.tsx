/* Dashboard (2.5): what needs me now (with links), quick actions for my role, today's figures, the last 30 days, recent alerts. */
import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';
import { useAuth } from '../auth';
import { ago, co, day, inr, plural, today } from '../format';
import { NAV } from '../Shell';
import { Btn, Page, Panel, Pill, Skeleton, useLoad, useUrlState } from '../ui/kit';
import { Icon } from '../ui/icons';

type Work = { key: string; label: string; count: number; to: string; tone: string };

/** Readable alert text: browser identification strings shortened to "Chrome on Windows" etc. */
function tidy(msg: string) {
  return msg.replace(/\(?Mozilla\/5\.0 \(([^)]*)\).*$/, (m, sys: string) => {
    const os = /Android/.test(sys) ? 'Android' : /iPhone|iPad/.test(sys) ? 'iPhone' : /Windows/.test(sys) ? 'Windows' : /Mac/.test(sys) ? 'Mac' : /Linux|X11/.test(sys) ? 'Linux' : 'a computer';
    const br = /Edg\//.test(m) ? 'Edge' : /Firefox/.test(m) ? 'Firefox' : /Chrome|AppleWebKit\/537/.test(m) ? 'Chrome' : /Safari/.test(m) ? 'Safari' : 'a browser';
    return `(${br} on ${os}).`;
  });
}
const ALERT_LINK: [RegExp, string][] = [[/^Financial year|^Period/, '/finance?tab=ye'], [/^Stock audit/, '/stock-audits'], [/^Approval/, '/approvals'], [/sign-in|Locked|password/i, '/audit?tab=security'],
  [/^Transfer/, '/transfers'], [/^Online|order/i, '/online'], [/^Cash|handover/i, '/cash'], [/^Low stock|reorder/i, '/stock?low=1']];

const TILES: { label: string; sub: string; to: string; icon: string; show: (c: (a: string, k: string) => boolean) => boolean; work?: string }[] = [
  { label: 'POS billing', sub: 'Bill uniforms and books', to: '/pos', icon: 'bill', show: c => c('add', 'pos') },
  { label: 'Sponsored issue', sub: 'MSEA voucher students', to: '/msea', icon: 'cap', show: c => c('view', 'msea') },
  { label: 'Stock receipt', sub: 'Post a GRN', to: '/purchasing', icon: 'truck', show: c => c('view', 'pur') && c('edit', 'inv'), work: 'grn' },
  { label: 'Stock transfer', sub: 'Move stock between locations', to: '/transfers?new=1', icon: 'swap', show: c => c('add', 'inv'), work: 'receive_transfer' },
  { label: 'Purchase request', sub: 'Raise a requisition', to: '/purchasing', icon: 'cart', show: c => c('add', 'pur') },
  { label: 'Online orders', sub: 'Pick, pack, hand over', to: '/online', icon: 'box', show: c => c('view', 'onl') },
  { label: 'Cash closing', sub: 'Count the drawer, hand over', to: '/cash', icon: 'bank', show: c => c('view', 'cash'), work: 'handover' },
  { label: 'Stock audits', sub: 'Count and verify', to: '/stock-audits', icon: 'clipboard', show: c => c('view', 'saudit'), work: 'count' },
  { label: 'Finance & tax', sub: 'Vouchers, GST, TDS, year-end', to: '/finance', icon: 'ledger', show: c => c('view', 'fin') },
  { label: 'Reports', sub: 'Sales, stock, MSEA, finance', to: '/reports', icon: 'chart', show: c => c('view', 'rep') },
  { label: 'Approvals', sub: '', to: '/approvals', icon: 'check', show: c => c('view', 'appr'), work: 'approvals' },
];
const sum = (rows: any[] | undefined, k: string) => (rows || []).reduce((s, r) => s + (Number(r[k]) || 0), 0);

export default function Dashboard() {
  const { me, can, myBranches, location } = useAuth();
  const { data: d } = useLoad<any>(() => api('/dashboard'), []);
  const { data: work } = useLoad<Work[]>(() => api('/my-work'), []);
  const { data: alerts } = useLoad<any[]>(() => api('/alerts'), []);
  const [tab, setTab] = useUrlState<'overview' | 'branches' | 'alerts'>('tab', 'overview');
  const h = new Date().getHours();
  const here = myBranches.find(b => b.location === location);
  const cos = [...new Set(myBranches.filter(b => b.location === location).map(b => b.company))].map(c => (c === 'KG' ? 'Kiswa Garments' : c === 'ZE' ? 'Zaibus Enterprises' : c));
  const wk = (k?: string) => (k ? work?.find(w => w.key === k)?.count || 0 : 0);
  const tiles = TILES.filter(t => t.show(can));
  const rep = can('view', 'rep');
  const tabs: [string, string][] = [['overview', 'Overview'], ...((d?.by_branch?.length || 0) > 1 ? [['branches', 'Branch comparison'] as [string, string]] : []), ['alerts', `Alerts${alerts?.length ? ` (${alerts.length})` : ''}`]];
  return (
    <Page title={`Good ${h < 12 ? 'morning' : h < 17 ? 'afternoon' : 'evening'}, ${me!.name.split(' ')[0]}`}
      sub={`${[cos.join(' & '), here?.name].filter(Boolean).join(' · ') || me!.designation || me!.role} · ${day(today())}`}>
      <div className="dtabs" role="tablist">{tabs.map(([k, l]) => <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? 'on' : ''} onClick={() => setTab(k as any)}>{l}</button>)}</div>
      {tab === 'overview' && <>
        {tiles.length > 0 && <div className="qtiles" aria-label="Quick actions">{tiles.map(t => {
          const n = wk(t.work);
          const sub = t.to === '/approvals' ? (n ? `${n} waiting for you` : 'Nothing waiting') : t.sub;
          return <Link key={t.label} to={t.to} className="qtile"><span className="qi"><Icon n={t.icon} /></span><span className="qt"><b>{t.label}</b><small>{sub}</small></span>{n > 0 && <em className="badge">{n}</em>}</Link>;
        })}</div>}
        {work && work.length > 0 && <Panel title="Your work">
          <ul className="work">{work.map(w => <li key={w.key} className={w.tone}><Link to={w.to}><b>{w.count}</b><span>{w.label}</span><i aria-hidden>›</i></Link></li>)}</ul>
        </Panel>}
        {!d ? <Skeleton /> : <Kpis d={d} rep={rep} />}
        <div className="dgrid">
          {d && <Trend trend={d.trend || []} />}
          <Panel title="Recent alerts" actions={(alerts?.length || 0) > 5 && <Btn kind="link" onClick={() => setTab('alerts')}>Open all</Btn>}>
            <AlertList alerts={(alerts || []).slice(0, 6)} can={can} />
          </Panel>
        </div>
        {d?.online?.length > 0 && <Panel title="Online orders, last 30 days"><div className="chips">{d.online.map((o: any) =>
          <Link key={o.status} to={rep ? `/reports?r=online-orders&status=${encodeURIComponent(o.status)}` : `/online?tab=${encodeURIComponent(o.status)}`} className="chip"><Pill s={o.status} /> {o.n}</Link>)}</div></Panel>}
      </>}
      {tab === 'branches' && d && <Panel title="Sales by location" actions={<span className="muted small">this month, including GST</span>}>
        <div className="table-wrap"><table className="rtable"><thead><tr><th>Location</th><th>Company</th><th className="r">Today</th><th className="r">Bills today</th><th className="r">Month to date</th><th>Share</th></tr></thead>
          <tbody>{(d.by_branch || []).map((b: any) => {
            const tot = sum(d.by_branch, 'mtd') || 1;
            return <tr key={b.branch_id}><td className="primary" data-label="">{b.name}</td><td data-label="Company"><span className={'co ' + b.company}>{b.company}</span></td>
              <td className="r" data-label="Today">{inr(b.today, 0)}</td><td className="r" data-label="Bills today">{b.bills}</td><td className="r" data-label="Month to date">{inr(b.mtd, 0)}</td>
              <td data-label="Share"><span className="share"><i style={{ width: `${(b.mtd / tot) * 100}%` }} /></span> {Math.round((b.mtd / tot) * 100)}%</td></tr>;
          })}</tbody></table></div>
      </Panel>}
      {tab === 'alerts' && <Panel title="Alerts, last 14 days">{alerts && !alerts.length ? <p className="empty">No alerts in the last 14 days.</p> : <AlertList alerts={(alerts || []).slice(0, 100)} can={can} />}</Panel>}
    </Page>
  );
}

function AlertList({ alerts, can }: { alerts: any[]; can: (a: string, k: string) => boolean }) {
  if (!alerts.length) return <p className="empty">No alerts in the last 14 days.</p>;
  return <ul className="alerts">{alerts.map(a => {
    const t = ALERT_LINK.find(([re]) => re.test(a.alert_type))?.[1];
    const to = t && NAV.find(n => n.to === t.split('?')[0])?.show(can) ? t : undefined;
    const body = <><b>{a.alert_type}</b> <span>{tidy(a.message)}</span><small title={a.created_at}>{ago(a.created_at)}</small></>;
    return <li key={a.id} className={a.severity}>{to ? <Link to={to}>{body}</Link> : body}</li>;
  })}</ul>;
}

/** Headline figures. Each card only appears when the user may see that figure. */
function Kpis({ d, rep }: { d: any; rep: boolean }) {
  const bal = (acc: string) => (d.balances || []).filter((b: any) => b.account === acc).reduce((s: number, b: any) => s + Number(b.bal || 0), 0);
  const has = (acc: string) => (d.balances || []).some((b: any) => b.account === acc);
  const today = sum(d.sales, 'today'), mtd = sum(d.sales, 'mtd'), prev = sum(d.prev_mtd, 'amt'), bills = sum(d.sales, 'bills');
  const delta = prev ? ((mtd - prev) / prev) * 100 : null;
  const spark = sparkFrom(d.trend || []);
  const cards: { k: string; label: string; icon: string; v: string; s: ReactNode; to?: string; spark?: number[] }[] = [];
  if (d.sales) {
    cards.push({ k: 'today', label: 'Sales today', icon: 'receipt', v: inr(today, 0), s: `${plural(bills, 'bill')}, including GST`, to: rep ? '/reports?r=daily-sales' : '/invoices', spark });
    cards.push({ k: 'mtd', label: 'Sales this month', icon: 'trend', v: inr(mtd, 0), s: delta == null ? 'No sales in the same days last month' : <><span className={delta >= 0 ? 'up' : 'down'}>{delta >= 0 ? '▲' : '▼'} {Math.abs(delta).toFixed(1)}%</span> vs same days last month</>, to: rep ? '/reports?r=daily-sales' : undefined });
  }
  if (d.gp_mtd) {
    const tx = Number(d.gp_mtd.taxable) || 0, gp = Number(d.gp_mtd.gp) || 0;
    cards.push({ k: 'gp', label: 'Gross profit MTD', icon: 'chart', v: inr(gp, 0), s: tx ? `${((gp / tx) * 100).toFixed(1)}% margin, before GST` : 'No sales yet this month' });
  }
  if (d.collections_today != null) cards.push({ k: 'coll', label: 'Collections today', icon: 'cash', v: inr(d.collections_today, 0), s: 'Cash, UPI, card, bank, receipts' });
  if (has('1200') || has('1210')) cards.push({ k: 'rec', label: 'Receivables', icon: 'person', v: inr(bal('1200') + bal('1210'), 0), s: 'Customers and MSEA outstanding', to: rep ? '/reports' : undefined });
  if (has('2000')) cards.push({ k: 'pay', label: 'Payables', icon: 'truck', v: inr(-bal('2000'), 0), s: 'Due to suppliers' });
  if (has('1100')) cards.push({ k: 'cash', label: 'Cash in hand', icon: 'cash', v: inr(bal('1100'), 0), s: 'Counters and Head Office' });
  if (has('1120') || has('1115')) cards.push({ k: 'bank', label: 'Bank', icon: 'bank', v: inr(bal('1120'), 0), s: has('1115') ? `${inr(bal('1115'), 0)} UPI / card in settlement` : 'All bank accounts' });
  for (const s of d.stock_value || []) cards.push({ k: 'sv' + s.company, label: `${co(s.company)} stock at cost`, icon: 'box', v: inr(s.value, 0), s: 'All your locations', to: rep ? '/reports?r=stock-summary' : '/stock' });
  if (!cards.length) return null;
  return <div className="kpis">{cards.map(c => {
    const inner = <><div className="kpi-h"><span className="eyebrow">{c.label}</span><span className="kpi-i"><Icon n={c.icon} size={16} /></span></div>
      <b className="v">{c.v}</b><small className="s">{c.s}</small>{c.spark && <Spark v={c.spark} />}</>;
    return c.to ? <Link key={c.k} to={c.to} className="kpi">{inner}</Link> : <div key={c.k} className="kpi">{inner}</div>;
  })}</div>;
}
function sparkFrom(trend: { d: string; t: number }[]) {
  const days: number[] = [];
  const t0 = new Date(); t0.setHours(12);
  for (let k = 13; k >= 0; k--) {
    const x = new Date(t0); x.setDate(t0.getDate() - k);
    const key = `${x.getFullYear()}-${String(x.getMonth() + 1).padStart(2, '0')}-${String(x.getDate()).padStart(2, '0')}`;
    days.push(trend.filter(t => t.d === key).reduce((s, t) => s + Number(t.t || 0), 0));
  }
  return days;
}
function Spark({ v }: { v: number[] }) {
  const max = Math.max(1, ...v), w = 140, hh = 28;
  const pts = v.map((y, i) => `${(i / (v.length - 1)) * w},${hh - 2 - (y / max) * (hh - 4)}`).join(' ');
  return <svg className="spark" viewBox={`0 0 ${w} ${hh}`} preserveAspectRatio="none" aria-hidden><polygon points={`0,${hh} ${pts} ${w},${hh}`} /><polyline points={pts} /></svg>;
}

/** Sales for the last 30 days, one column per day (days without sales show as gaps), Kiswa and Zaibus stacked. */
function Trend({ trend }: { trend: { d: string; company: string; t: number }[] }) {
  if (!trend.length) return null;
  const days: string[] = [];
  const t0 = new Date(); t0.setHours(12);
  for (let k = 29; k >= 0; k--) { const x = new Date(t0); x.setDate(t0.getDate() - k); days.push(`${x.getFullYear()}-${String(x.getMonth() + 1).padStart(2, '0')}-${String(x.getDate()).padStart(2, '0')}`); }
  const v = (dd: string, c: string) => trend.find(t => t.d === dd && t.company === c)?.t || 0;
  const max = Math.max(1, ...days.map(dd => v(dd, 'KG') + v(dd, 'ZE')));
  const total = trend.reduce((s, t) => s + t.t, 0);
  return (
    <Panel title="Sales, last 30 days" actions={<span className="muted small">{inr(total, 0)} in all</span>}>
      <div className="bars" role="img" aria-label={`Daily sales for the last 30 days, ${inr(total, 0)} in all`}>
        {days.map(dd => {
          const kg = v(dd, 'KG'), ze = v(dd, 'ZE');
          return <div key={dd} className="bar" title={`${day(dd)}: Kiswa ${inr(kg, 0)}, Zaibus ${inr(ze, 0)}`}>
            <i className="ze" style={{ height: `${(ze / max) * 100}%` }} /><i className="kg" style={{ height: `${(kg / max) * 100}%` }} /></div>;
        })}
      </div>
      <p className="legend"><span className="sw kg" /> Kiswa <span className="sw ze" /> Zaibus · {day(days[0], false)} to {day(days[29], false)} · hover a day for its figures</p>
    </Panel>
  );
}
