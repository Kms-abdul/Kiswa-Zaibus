/* Reports (release 2.4): the report library — sales, stock, purchasing, cash, MSEA & online, controls and the financial statements.
   Every report has Print, PDF, Excel and CSV (as the role allows; every print and export is logged), drill-down from summary rows,
   and the same filters: company, period, location and the report's own filters. Scheduled e-mails are unchanged. */
import { useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api } from '../api';
import { useAuth } from '../auth';
import { monthStart, today, when } from '../format';
import { Btn, DataTable, ErrorNote, Field, Modal, Page, Panel, Pill, Skeleton, useAction, useLoad } from '../ui/kit';
import { type Fmt, type Report, ReportActions, ReportSheet, ReportTable } from '../ui/report';
import InvoiceView from './InvoiceView';

type Entry = { key: string; group: string; title: string; period: boolean };
const GROUP_ORDER = ['Sales', 'Stock', 'Purchasing', 'Cash', 'MSEA & online', 'Controls', 'Financial'];
const AS_OF = new Set(['balance-sheet', 'receivables-ageing', 'payables-ageing']);
// company-wide registers: the location filter does not apply
const NO_BRANCH = new Set(['audit-trail', 'signin-activity', 'price-history', 'owner-instructions', 'msea-vouchers', 'online-orders', 'payment-links',
  'purchase-returns', 'supplier-purchases', 'approvals-register']);
const ONLINE_STATUS = ['Order Placed', 'Payment Confirmed', 'Picking', 'Quality Check', 'Packed', 'Dispatched to Branch', 'Received at Branch', 'Ready for Collection',
  'Collected', 'Partly returned', 'Returned', 'Cancelled', 'Payment failed'];

export default function Reports() {
  const { can, org, myBranches } = useAuth();
  const [sp, setSp] = useSearchParams();
  const lib = useLoad<{ reports: Entry[]; formats: Fmt[] }>(() => api('/reports/library'), []);
  const groups = useMemo(() => {
    const g: Record<string, Entry[]> = {};
    for (const r of lib.data?.reports || []) (g[r.group] = g[r.group] || []).push(r);
    return GROUP_ORDER.filter(k => g[k]).map(k => [k, g[k]] as [string, Entry[]]);
  }, [lib.data]);
  const key = sp.get('r') || lib.data?.reports[0]?.key || '';
  const entry = lib.data?.reports.find(r => r.key === key);
  const [f, setF] = useState<Record<string, string>>(() => ({ from: monthStart(), to: today(),
    ...Object.fromEntries(['company', 'from', 'to', 'branch', 'status', 'category', 'item', 'days', 'account'].filter(k => sp.get(k)).map(k => [k, sp.get(k)!])) }));
  const [stack, setStack] = useState<{ key: string; args: Record<string, string>; title?: string }[]>([]);
  const cur = stack.length ? stack[stack.length - 1] : null;
  const curKey = cur?.key || key;
  const curEntry = lib.data?.reports.find(r => r.key === curKey);
  const params = Object.fromEntries(Object.entries({ ...f, ...(cur?.args || {}) }).filter(([, v]) => v !== '' && v != null));
  if (curEntry && !curEntry.period && !AS_OF.has(curKey)) delete params.from;
  const qs = new URLSearchParams(params).toString();
  const url = `/reports/library/${curKey}?${qs}`;
  const rep = useLoad<{ k: string; d: Report }>(() => (curKey ? api<Report>(url).then(d => ({ k: url, d })) : Promise.resolve(null as any)), [url]);
  const r = rep.data?.k === url ? rep.data.d : null;
  const [inv, setInv] = useState<number | null>(null);
  const [sched, setSched] = useState(false);
  const canSched = can('edit', 'set') || can('cfgrep', 'rep');
  const pick = (k: string) => { setStack([]); sp.set('r', k); setSp(sp, { replace: true }); };
  const drill = (d: any) => {
    if (d?.report) setStack(s => [...s, { key: d.report, args: Object.fromEntries(Object.entries(d.args || {}).map(([k, v]) => [k, String(v)])) }]);
    else if (d?.invoice) setInv(d.invoice);
  };
  const set = (k: string) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  const title = (k: string) => lib.data?.reports.find(x => x.key === k)?.title || k;
  if (lib.err) return <Page title="Reports"><ErrorNote err={lib.err} /></Page>;
  return (
    <Page title="Reports" sub="Print, PDF, Excel or CSV from any report; click a summary row to open its detail." actions={canSched && <Btn onClick={() => setSched(true)}>Scheduled e-mails</Btn>}>
      {sched && <Schedules report={curKey} company={f.company || ''} by="day" account={f.account || '1100'} onClose={() => setSched(false)} />}
      {!lib.data ? <Skeleton /> : <div className="rep-layout">
        <aside className="rep-menu no-print" aria-label="Reports">{groups.map(([g, xs]) => <details key={g} open={xs.some(x => x.key === key) || g === 'Sales'}>
          <summary>{g} ({xs.length})</summary>
          {xs.map(x => <button key={x.key} className={x.key === key && !stack.length ? 'on' : ''} onClick={() => pick(x.key)}>{x.title}</button>)}</details>)}</aside>
        <div className="rep-main">
          <div className="filters no-print">
            <select value={f.company || ''} onChange={set('company')} aria-label="Company"><option value="">{entry?.group === 'Financial' ? 'Company (first of yours)' : 'All companies'}</option>
              {(org?.companies || []).map(c => <option key={c.code} value={c.code}>{c.name}</option>)}</select>
            {curEntry?.period && <><input type="date" value={f.from} onChange={set('from')} aria-label="From" /><input type="date" value={f.to} onChange={set('to')} aria-label="To" /></>}
            {AS_OF.has(curKey) && <input type="date" value={f.to} onChange={set('to')} aria-label="As of" />}
            {!NO_BRANCH.has(curKey)
              && <select value={f.branch || ''} onChange={set('branch')} aria-label="Location"><option value="">All my locations</option>
                {myBranches.filter(b => !f.company || b.company === f.company).map(b => <option key={b.id} value={b.id}>{b.company} · {b.name}</option>)}</select>}
            {curKey === 'item-ledger' && <input value={cur?.args.item ?? f.item ?? ''} onChange={set('item')} placeholder="Item code" aria-label="Item code" />}
            {curKey === 'item-sales' && <input value={cur?.args.category ?? f.category ?? ''} onChange={set('category')} placeholder="Category (all)" aria-label="Category" />}
            {curKey === 'slow-moving' && <select value={f.days || '90'} onChange={set('days')} aria-label="No sale for">{['30', '60', '90', '180', '365'].map(d => <option key={d} value={d}>No sale for {d}+ days</option>)}</select>}
            {curKey === 'online-orders' && <select value={f.status || ''} onChange={set('status')} aria-label="Status"><option value="">All statuses</option>{ONLINE_STATUS.map(s => <option key={s}>{s}</option>)}</select>}
            {curKey === 'ledger' && <input value={cur?.args.account ?? f.account ?? ''} onChange={set('account')} placeholder="Account code, e.g. 1100" aria-label="Account" />}
            {curKey === 'audit-trail' && <><input value={f.q || ''} onChange={set('q')} placeholder="Search action, reference, reason" aria-label="Search" />
              <input value={f.user || ''} onChange={set('user')} placeholder="User" aria-label="User" /></>}
          </div>
          {stack.length > 0 && <div className="crumbs no-print"><Btn kind="link" onClick={() => setStack([])}>{title(key)}</Btn>
            {stack.map((s, i) => <span key={i}> › <Btn kind="link" onClick={() => setStack(st => st.slice(0, i + 1))}>{title(s.key)}{s.args.item ? ` ${s.args.item}` : ''}{s.args.branch ? ` · ${s.args.branch}` : ''}{s.args.category ? ` · ${s.args.category}` : ''}</Btn></span>)}</div>}
          <ErrorNote err={rep.err} />
          <Panel>
            <div className="rep-head no-print"><h2>{r?.title || curEntry?.title || 'Report'}</h2>
              {r && <span className="muted rep-count">{r.rows.length} {r.rows.length === 1 ? "row" : "rows"}</span>}
              <ReportActions url={url} name={`${curKey}-${f.company || 'all'}-${f.to}`} formats={lib.data.formats} r={r} /></div>
            {!r ? (!rep.err && <Skeleton />) : <ReportSheet r={r} subtitle={f.branch ? `Location ${f.branch}` : undefined}>
              <ReportTable r={r} onDrill={drill} />
              {r.drill?.some(Boolean) && <p className="muted small no-print">Click a row to open its detail.</p>}
            </ReportSheet>}
          </Panel>
        </div>
      </div>}
      {inv && <InvoiceModal id={inv} onClose={() => setInv(null)} />}
    </Page>
  );
}

function InvoiceModal({ id, onClose }: { id: number; onClose: () => void }) {
  const { data, err } = useLoad<any>(() => api(`/pos/invoices/${id}`), [id]);
  return <Modal wide title={data ? `Bill ${data.no}` : 'Bill'} onClose={onClose}><ErrorNote err={err} />{data ? <InvoiceView inv={data} /> : !err && <Skeleton />}</Modal>;
}

// ------------------------------------------------------------ scheduled report e-mails (CSV attached)
const FREQ: Record<string, string> = { daily: 'Every day', weekly: 'Every week', monthly: 'Every month' };
const WD = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
function Schedules({ report, company, by, account, onClose }: { report: string; company: string; by: string; account: string; onClose: () => void }) {
  const l = useLoad<any[]>(() => api('/setup/report-schedules'), []);
  const cat = useLoad<any>(() => api('/setup/catalog'), []);
  const keys: [string, string][] = cat.data?.report_keys || [];
  const blank = { name: '', report: keys.some(k => k[0] === report) ? report : 'sales-register', frequency: 'daily', run_at: '21:30', weekday: 0, month_day: 1, recipients: '', period: 'today' };
  const [f, setF] = useState<any>(null);
  const [run, busy] = useAction();
  const save = async () => {
    const body = { name: f.name, report: f.report, frequency: f.frequency, run_at: f.run_at, weekday: Number(f.weekday), month_day: Number(f.month_day),
      recipients: String(f.recipients).split(/[,;\s]+/).filter(Boolean), params: { period: f.period, ...(company && { company }), ...(f.report === 'sales-summary' && { by }), ...(f.report === 'ledger' && { account }) } };
    if (await run(() => (f.id ? api(`/setup/report-schedules/${f.id}`, { method: 'PUT', body }) : api('/setup/report-schedules', { body })), () => 'Schedule saved.')) { setF(null); l.reload(); }
  };
  return <Modal wide title="Scheduled report e-mails" onClose={onClose} footer={!f && <Btn kind="pri" onClick={() => setF(blank)}>New schedule</Btn>}>
    {!f ? <DataTable rows={l.data} onRow={r => setF({ ...r, ...r.params, recipients: (r.recipients || []).join(', ') })} empty="No schedules yet." cols={[
      { key: 'name', label: 'Schedule', primary: true }, { key: 'report', label: 'Report' }, { key: 'frequency', label: 'When', render: (r: any) => `${FREQ[r.frequency]} ${r.run_at}` },
      { key: 'recipients', label: 'To', hideSmall: true, render: (r: any) => (r.recipients || []).join(', ') }, { key: 'last_run_at', label: 'Last run', render: (r: any) => (r.last_run_at ? `${when(r.last_run_at)} · ${r.last_result || ''}` : '—') },
      { key: 'active', label: 'Status', render: (r: any) => (r.active ? <Pill s="Active" /> : <Pill s="Inactive" />) }]} />
      : <div className="form grid2">
        <Field label="Name"><input value={f.name} onChange={e => setF({ ...f, name: e.target.value })} placeholder="e.g. Daily sales to owner" /></Field>
        <Field label="Report"><select value={f.report} onChange={e => setF({ ...f, report: e.target.value })}>{keys.map(([k, n]) => <option key={k} value={k}>{n}</option>)}</select></Field>
        <Field label="Period covered"><select value={f.period} onChange={e => setF({ ...f, period: e.target.value })}>
          {[['today', 'Today'], ['yesterday', 'Yesterday'], ['last_7_days', 'Last 7 days'], ['mtd', 'Month to date'], ['last_month', 'Last month']].map(([k, n]) => <option key={k} value={k}>{n}</option>)}</select></Field>
        <Field label="Frequency"><select value={f.frequency} onChange={e => setF({ ...f, frequency: e.target.value })}>{Object.entries(FREQ).map(([k, n]) => <option key={k} value={k}>{n}</option>)}</select></Field>
        {f.frequency === 'weekly' && <Field label="Day"><select value={f.weekday} onChange={e => setF({ ...f, weekday: e.target.value })}>{WD.map((d, i) => <option key={d} value={i}>{d}</option>)}</select></Field>}
        {f.frequency === 'monthly' && <Field label="Day of month (1-28)"><input inputMode="numeric" value={f.month_day} onChange={e => setF({ ...f, month_day: e.target.value })} /></Field>}
        <Field label="Time (24-hour)"><input type="time" value={f.run_at} onChange={e => setF({ ...f, run_at: e.target.value })} /></Field>
        <Field label="E-mail to (comma separated)" wide><input value={f.recipients} onChange={e => setF({ ...f, recipients: e.target.value })} /></Field>
        <p className="muted">Runs with your permissions and the company / grouping selected on the report screen. The CSV is attached to the e-mail.</p>
        <div className="row">
          <Btn onClick={() => setF(null)}>Back</Btn>
          {f.id && <Btn busy={busy} onClick={async () => { await run(() => api(`/setup/report-schedules/${f.id}`, { method: 'PUT', body: { active: !f.active } }), () => (f.active ? 'Paused.' : 'Resumed.')); setF(null); l.reload(); }}>{f.active ? 'Pause' : 'Resume'}</Btn>}
          {f.id && <Btn busy={busy} onClick={async () => { await run(() => api(`/setup/report-schedules/${f.id}/run`, { body: {} }), (x: any) => x.result); l.reload(); }}>Send now</Btn>}
          <Btn kind="pri" busy={busy} onClick={save}>Save</Btn></div>
      </div>}
  </Modal>;
}
