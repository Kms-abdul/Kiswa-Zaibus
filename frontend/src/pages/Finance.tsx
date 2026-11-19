/* Finance & tax (release 2.3): dashboard, vouchers (21 types, GST and TDS worked out as you type, maker → approver), GST (set-off,
   GSTR-1 / 3B / 9 / 9C, JSON, challan, filing, e-invoice, e-way bill, 2B / 2A reconciliation), TDS (register, challan, Form 138 / 140 /
   144 / 143 returns, Form 130 / 131 certificates), 73 reports with drill-down and Excel / PDF, banking and BRS, payables and
   receivables, fixed assets, the compliance calendar and the finance masters. Rules are enforced by the API (services/fin_*.py). */
import { useEffect, useMemo, useRef, useState } from 'react';
import { Route, Routes, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { api, download, errText } from '../api';
import { useAuth } from '../auth';
import { day, inr, monthStart, today, when } from '../format';
import { Btn, ConfirmBtn, DataTable, ErrorNote, EvidencePanel, Field, Kv, Modal, Page, Panel, Pill, Skeleton, Tabs, useAction, useLoad } from '../ui/kit';
import { ReportActions, ReportSheet, ReportTable } from '../ui/report';

type Tab = 'dash' | 'vch' | 'gst' | 'tds' | 'rep' | 'bank' | 'pr' | 'assets' | 'cmp' | 'ye' | 'setup';
const TABS: [Tab, string][] = [['dash', 'Dashboard'], ['vch', 'Vouchers'], ['gst', 'GST'], ['tds', 'TDS'], ['rep', 'Reports'], ['bank', 'Bank'], ['pr', 'Payables & receivables'],
  ['assets', 'Fixed assets'], ['cmp', 'Compliance'], ['ye', 'Year-end'], ['setup', 'Setup']];
const money = (v: any) => (v == null || v === '' ? '—' : inr(v));
const ym = (d = today()) => d.slice(0, 7);
const prevYm = () => { const d = new Date(today() + 'T00:00:00Z'); d.setUTCDate(0); return d.toISOString().slice(0, 7); };

export default function Finance() {
  return <Routes><Route index element={<Home />} /><Route path="vouchers/:id" element={<VoucherPage />} /></Routes>;
}

function useCompany() {
  const { org } = useAuth();
  const [sp, setSp] = useSearchParams();
  const company = sp.get('company') || org?.companies?.[0]?.code || 'KG';
  return [company, (c: string) => { sp.set('company', c); setSp(sp, { replace: true }); }] as const;
}
function useMeta(company: string) { return useLoad<any>(() => api(`/fin/meta?company=${company}`), [company]); }

function Home() {
  const { org } = useAuth();
  const [sp, setSp] = useSearchParams();
  const tab = (sp.get('tab') as Tab) || 'dash';
  const [company, setCompany] = useCompany();
  const meta = useMeta(company);
  const go = (t: Tab, extra: Record<string, string> = {}) => { sp.set('tab', t); for (const [k, v] of Object.entries(extra)) sp.set(k, v); setSp(sp); };
  return <Page title="Finance & tax" sub="Double-entry books with GST and TDS. Every voucher posts one balanced journal; posted vouchers are never edited — corrections are reversals."
    actions={<select value={company} onChange={e => setCompany(e.target.value)} aria-label="Company">{(org?.companies || []).map(c => <option key={c.code} value={c.code}>{c.name}</option>)}</select>}>
    <div className="tabs-scroll"><Tabs value={tab} onChange={t => go(t)} tabs={TABS} /></div>
    {meta.err && <ErrorNote err={meta.err} />}
    {meta.data && <>
      {tab === 'dash' && <Dashboard company={company} go={go} />}
      {tab === 'vch' && <Vouchers company={company} meta={meta.data} />}
      {tab === 'gst' && <Gst company={company} meta={meta.data} />}
      {tab === 'tds' && <Tds company={company} meta={meta.data} />}
      {tab === 'rep' && <Reports company={company} meta={meta.data} />}
      {tab === 'bank' && <Bank company={company} meta={meta.data} />}
      {tab === 'pr' && <Payables company={company} meta={meta.data} />}
      {tab === 'assets' && <Assets company={company} meta={meta.data} />}
      {tab === 'cmp' && <Compliance company={company} />}
      {tab === 'ye' && <YearEnd company={company} go={go} reload={meta.reload} />}
      {tab === 'setup' && <Setup company={company} meta={meta.data} reload={meta.reload} />}
    </>}
  </Page>;
}

// ------------------------------------------------------------ year-end closing (2.6)
type Check = { key: string; title: string; status: 'ok' | 'warn' | 'block'; detail: string; go?: { tab?: Tab; page?: string; report?: string } | null };
function YearEnd({ company, go, reload }: { company: string; go: (t: Tab, x?: Record<string, string>) => void; reload: () => void }) {
  const nav = useNavigate();
  const [fy, setFy] = useState('');
  const v = useLoad<any>(() => api(`/fin/year-end?company=${company}${fy ? `&fy=${fy}` : ''}`), [company, fy]);
  const [split, setSplit] = useState<{ account: string; pct: string }[] | null>(null);
  const [reason, setReason] = useState('');
  const [ack, setAck] = useState(false);
  const [run, busy] = useAction();
  const d = v.data;
  useEffect(() => { setSplit(null); setAck(false); setReason(''); }, [company, fy, d?.year?.status]);
  if (v.err) return <ErrorNote err={v.err} />;
  if (!d) return <Skeleton />;
  const y = d.year;
  const sp = split ?? (d.split || []).map((s: any) => ({ account: s.account, pct: String(s.pct) }));
  const total = sp.reduce((t: number, s: any) => t + (Number(s.pct) || 0), 0);
  const checks: Check[] = d.checks || [];
  const blocks = checks.filter(c => c.status === 'block'), warns = checks.filter(c => c.status === 'warn');
  const open = (c: Check) => { if (c.go?.page) nav('/' + c.go.page); else if (c.go?.tab) go(c.go.tab, c.go.report ? { r: c.go.report } : {}); };
  const act = (path: string, body: any, msg: string) => run(() => api(`/fin/year-end/${path}`, { body: { company, fy: y.code, ...body } }), () => msg).then(r => { if (r) { v.setData(r); reload(); } });
  const pack = [['tb', 'Trial balance'], ['pl', 'Profit & loss'], ['bs', 'Balance sheet'], ['cf', 'Cash flow']];
  const share = (pct: string) => Math.round((d.net || 0) * (Number(pct) || 0)) / 100;
  return <>
    <Panel title="Financial year" actions={<select value={y.code} onChange={e => setFy(e.target.value)} aria-label="Financial year">
      {d.years.map((x: any) => <option key={x.code} value={x.code}>FY {x.code} · {x.status}</option>)}</select>}>
      <Kv items={[['Year', `${day(y.from_date)} – ${day(y.to_date)}`], ['Status', <Pill s={y.status} tone={y.status === 'Closed' ? 'ok' : 'warn'} />],
        ...(y.status === 'Closed' ? [['Closed', d.summary ? `${when(d.summary.closed_at)} by ${d.summary.closed_by} (${d.summary.closed_role})` : 'At installation (go-live opening balances)'],
          ['Reason', y.close_reason], ['Closing journal', y.closing_journal]] as [string, any][] : []),
        ...(y.reopen_reason ? [['Last reopened', `${when(y.reopened_at)} · ${y.reopen_reason}`]] as [string, any][] : [])]} />
      <div className="row">{pack.map(([k, l]) => <Btn key={k} kind="link" onClick={() => go('rep', { r: k, from: String(y.from_date).slice(0, 10), to: String(y.to_date).slice(0, 10) })}>{l} {y.code}</Btn>)}</div>
      <p className="muted small">Year-end pack: open each report for the year, then Print, PDF or Excel. After closing, the P&L still shows the year's profit; the balance sheet shows it inside capital.</p>
    </Panel>

    {y.status === 'Closed' ? <>
      {d.summary && <Panel title="How the year was closed">
        <div className="tiles small"><div className="tile"><span>Income</span><b>{inr(d.summary.income)}</b></div><div className="tile"><span>Expenses</span><b>{inr(d.summary.expenses)}</b></div>
          <div className="tile"><span>{d.summary.net >= 0 ? 'Profit' : 'Loss'} transferred</span><b className={d.summary.net < 0 ? 'bad-text' : ''}>{inr(Math.abs(d.summary.net))}</b></div></div>
        <DataTable rows={d.summary.split} keyOf={r => r.account} cols={[{ key: 'name', label: 'Transferred to', primary: true }, { key: 'pct', label: 'Share', align: 'r', render: r => `${r.pct}%` },
          { key: 'amount', label: 'Amount', align: 'r', render: r => inr(r.amount) }]} />
        {d.summary.acknowledged?.length > 0 && <p className="note warn">Closed with these items reviewed and accepted: {d.summary.acknowledged.join(' · ')}.</p>}
      </Panel>}
      {d.can.reopen && <Panel title="Reopen this year">
        <p className="muted">Only for corrections after closing (for example an auditor's adjustment). The closing journal is reversed, the year is unlocked and management is
          alerted. Reopen the latest closed year first. Close it again when the correction is posted.</p>
        <div className="form narrow"><Field label="Reason (recorded in the audit trail)"><input value={reason} onChange={e => setReason(e.target.value)} placeholder="e.g. Auditor adjustment: bonus provision" /></Field>
          <ConfirmBtn label={`Reopen FY ${y.code}`} confirm="Yes, reopen and reverse the closing entry" busy={busy} onConfirm={() => act('reopen', { reason }, `FY ${y.code} reopened.`)} /></div>
      </Panel>}
    </> : <>
      <Panel title="1 · Before closing" actions={<span className="muted small">{blocks.length ? `${blocks.length} to fix` : 'Nothing blocks closing'}{warns.length ? ` · ${warns.length} to review` : ''}</span>}>
        <ul className="checks">{checks.map(c => <li key={c.key} className={c.status}>
          <span className="ck" aria-hidden>{c.status === 'ok' ? '✓' : c.status === 'block' ? '✕' : '!'}</span>
          <div><b>{c.title}</b>{c.status !== 'ok' && <small>{c.detail}</small>}</div>
          {c.go && <Btn kind="link" onClick={() => open(c)}>Open</Btn>}</li>)}</ul>
      </Panel>
      <Panel title="2 · Profit to transfer">
        <div className="tiles small"><div className="tile"><span>Income</span><b>{inr(d.income)}</b></div><div className="tile"><span>Expenses</span><b>{inr(d.expenses)}</b></div>
          <div className="tile"><span>{d.net >= 0 ? 'Net profit' : 'Net loss'}</span><b className={d.net < 0 ? 'bad-text' : ''}>{inr(Math.abs(d.net))}</b></div></div>
        <p className="muted">The {d.net >= 0 ? 'profit' : 'loss'} goes to these capital / reserve accounts (for a firm, each partner's capital by the profit-sharing ratio).</p>
        <table className="ye-split"><thead><tr><th>Account</th><th className="r">Share %</th><th className="r">Amount</th><th /></tr></thead><tbody>
          {sp.map((s: any, i: number) => <tr key={i}>
            <td><select value={s.account} onChange={e => setSplit(sp.map((x: any, k: number) => (k === i ? { ...x, account: e.target.value } : x)))} aria-label="Account">
              {d.equity_accounts.map((a: any) => <option key={a.code} value={a.code}>{a.code} · {a.name}</option>)}</select></td>
            <td className="r"><input inputMode="decimal" value={s.pct} onChange={e => setSplit(sp.map((x: any, k: number) => (k === i ? { ...x, pct: e.target.value } : x)))} aria-label="Share %" className="num" /></td>
            <td className="r">{inr(share(s.pct))}</td>
            <td>{sp.length > 1 && <button className="x" onClick={() => setSplit(sp.filter((_: any, k: number) => k !== i))} aria-label="Remove">×</button>}</td></tr>)}
        </tbody><tfoot><tr><td><Btn kind="link" onClick={() => setSplit([...sp, { account: d.equity_accounts[0]?.code || '', pct: String(Math.max(0, 100 - total)) }])}>+ Add account</Btn></td>
          <td className={'r' + (Math.abs(total - 100) > 0.001 ? ' bad-text' : '')}><b>{total}%</b></td><td className="r"><b>{inr(d.net)}</b></td><td /></tr></tfoot></table>
        <p className="muted small">Need another account (Reserves & Surplus, a partner's capital)? Add it under Setup & masters → Chart of accounts with a code starting 3.</p>
        <details><summary>Closing entry ({d.closing_lines.length} income and expense accounts brought to zero)</summary>
          <DataTable rows={d.closing_lines} keyOf={r => r.account} cols={[{ key: 'name', label: 'Account', primary: true, render: r => `${r.account} · ${r.name}` },
            { key: 'debit', label: 'Debit', align: 'r', render: r => (r.debit ? inr(r.debit) : '') }, { key: 'credit', label: 'Credit', align: 'r', render: r => (r.credit ? inr(r.credit) : '') }]} />
        </details>
        <p className="muted small">Next year opens with assets {inr(d.opening_next.assets)} and liabilities {inr(d.opening_next.liabilities)}; income and expense accounts start at zero.</p>
      </Panel>
      <Panel title="3 · Close the year">
        {!d.can.close ? <p className="muted">Only the CFO or Director closes a financial year.</p> : blocks.length ? <p className="note bad">Fix the items marked ✕ above first.</p> : <div className="form narrow">
          {warns.length > 0 && <label className="check wrap"><input type="checkbox" checked={ack} onChange={e => setAck(e.target.checked)} />I have reviewed the {warns.length} item(s) marked ! and want to close anyway</label>}
          <Field label="Reason or reference" hint="e.g. Accounts finalised; audited by M/s … on 15-09-2026"><input value={reason} onChange={e => setReason(e.target.value)} /></Field>
          <ConfirmBtn kind="pri" label={`Close FY ${y.code}`} confirm={`Yes, close FY ${y.code} and lock it`} busy={busy}
            onConfirm={() => act('close', { reason, acknowledge: ack, split: sp.map((s: any) => ({ account: s.account, pct: Number(s.pct) })) }, `FY ${y.code} closed. Profit moved to capital; the year is locked.`)} />
          <p className="muted small">You will be asked for your password. Closing posts the closing entry on {day(y.to_date)}, locks the books up to that date and opens the next year.
            The Director can reopen it later if a correction is needed.</p>
        </div>}
      </Panel>
    </>}
  </>;
}

// ------------------------------------------------------------ dashboard
function Dashboard({ company, go }: { company: string; go: (t: Tab, x?: Record<string, string>) => void }) {
  const { data: d, err } = useLoad<any>(() => api(`/fin/dashboard?company=${company}`), [company]);
  const nav = useNavigate();
  if (err) return <ErrorNote err={err} />;
  if (!d) return <p className="muted">Loading…</p>;
  const max = Math.max(1, ...d.trend.map((t: any) => Math.max(t.income, t.expenses)));
  return <>
    <div className="tiles">
      <div className="tile"><span>Income (FY {d.fy})</span><b>{money(d.income)}</b></div>
      <div className="tile"><span>Expenses</span><b>{money(d.expenses)}</b></div>
      <div className="tile"><span>Net profit (FY)</span><b className={d.net < 0 ? 'var-short' : ''}>{money(d.net)}</b><small>this month {money(d.month_net)}</small></div>
      <div className="tile"><span>Cash · bank</span><b>{money(d.cash)}</b><small>bank {money(d.bank)}</small></div>
      <div className="tile clickable" onClick={() => go('rep', { r: 'ageing' })}><span>Receivables</span><b>{money(d.receivables)}</b></div>
      <div className="tile clickable" onClick={() => go('pr')}><span>Payables</span><b>{money(d.payables)}</b></div>
      <div className="tile clickable" onClick={() => go('gst')}><span>GST to pay (this month)</span><b>{money(d.gst_due)}</b><small>due {day(d.gst_due_on)}</small></div>
      <div className="tile clickable" onClick={() => go('tds')}><span>TDS to deposit</span><b>{money(d.tds_due)}</b></div>
      <div className="tile clickable" onClick={() => go('vch', { status: 'Pending approval' })}><span>Vouchers waiting</span><b>{d.pending}</b><small>{d.drafts} drafts · books closed to {d.lock ? day(d.lock) : '—'}</small></div>
    </div>
    <div className="grid2">
      <Panel title="Alerts">{d.alerts.length ? <ul className="alerts">{d.alerts.map((a: any, i: number) => <li key={i} className={'sev-' + a.severity}>
        <b>{a.title}</b> <span>{a.message}</span>{a.go?.report && <Btn kind="link" onClick={() => go('rep', { r: a.go.report })}>Open</Btn>}{a.go?.tab && <Btn kind="link" onClick={() => go(a.go.tab === 'compliance' ? 'cmp' : a.go.tab)}>Open</Btn>}{a.go?.page && <Btn kind="link" onClick={() => nav('/' + a.go.page)}>Open</Btn>}</li>)}</ul>
        : <p className="empty">Nothing needs attention.</p>}</Panel>
      <Panel title="Income and expenses by month">
        <div className="trend">{d.trend.map((t: any) => <div key={t.month} className="trend-row"><span className="trend-l">{t.month}</span>
          <span className="trend-bar two"><i className="pos" style={{ width: `${Math.max(1, t.income / max * 100)}%` }} /><i className="neg" style={{ width: `${Math.max(1, t.expenses / max * 100)}%` }} /></span>
          <span className={'r ' + (t.net < 0 ? 'var-short' : 'var-excess')}>{money(t.net)}</span></div>)}</div>
        <p className="muted small">Green: income · red: expenses · right: net profit.</p>
      </Panel>
    </div>
  </>;
}

// ------------------------------------------------------------ vouchers
function Vouchers({ company, meta }: { company: string; meta: any }) {
  const [sp] = useSearchParams();
  const [f, setF] = useState({ vtype: '', status: sp.get('status') || '', from: '', to: '', q: '' });
  const qs = new URLSearchParams({ company, ...Object.fromEntries(Object.entries(f).filter(([, v]) => v)) }).toString();
  const l = useLoad<any[]>(() => api(`/fin/vouchers?${qs}`), [qs]);
  const [edit, setEdit] = useState<any>(null);
  const [pay, setPay] = useState(false);
  const nav = useNavigate();
  const set = (k: string) => (e: any) => setF({ ...f, [k]: e.target.value });
  return <>
    <div className="filters">
      <select value={f.vtype} onChange={set('vtype')} aria-label="Type"><option value="">All types</option>{meta.types.map((t: any) => <option key={t.code} value={t.code}>{t.label}</option>)}</select>
      <select value={f.status} onChange={set('status')} aria-label="Status"><option value="">All statuses</option>{['Draft', 'Pending approval', 'Returned', 'Posted', 'Rejected', 'Reversed', 'Cancelled'].map(s => <option key={s}>{s}</option>)}</select>
      <input type="date" value={f.from} onChange={set('from')} aria-label="From" /><input type="date" value={f.to} onChange={set('to')} aria-label="To" />
      <input placeholder="No., ref, party, narration" value={f.q} onChange={set('q')} aria-label="Search" />
      {meta.can.add && <><Btn kind="pri" onClick={() => setEdit({ vtype: 'EX' })}>New voucher</Btn><Btn onClick={() => setPay(true)}>Payroll</Btn></>}
    </div>
    <Panel><DataTable rows={l.data} onRow={r => nav(`/finance/vouchers/${r.id}?company=${company}`)} empty="No vouchers match." cols={[
      { key: 'no', label: 'Voucher', primary: true, render: r => <span className="mono">{r.no}</span> }, { key: 'vdate', label: 'Date', render: r => day(r.vdate) },
      { key: 'type_label', label: 'Type', hideSmall: true }, { key: 'party_name', label: 'Party / narration', render: r => <>{r.party_name || ''} <span className="muted">{r.narration}</span></> },
      { key: 'tax', label: 'GST', align: 'r', hideSmall: true, render: r => (r.tax ? inr(r.tax) : '') }, { key: 'tds_amount', label: 'TDS', align: 'r', hideSmall: true, render: r => (r.tds_amount ? inr(r.tds_amount) : '') },
      { key: 'total', label: 'Amount', align: 'r', render: r => inr(r.total) }, { key: 'status', label: 'Status', render: r => <Pill s={r.status} /> }]} /></Panel>
    {edit && <VoucherEditor company={company} meta={meta} init={edit} onClose={id => { setEdit(null); l.reload(); if (id) nav(`/finance/vouchers/${id}?company=${company}`); }} />}
    {pay && <PayrollDialog company={company} onClose={id => { setPay(false); if (id) nav(`/finance/vouchers/${id}?company=${company}`); }} />}
  </>;
}

function PayrollDialog({ company, onClose }: { company: string; onClose: (id?: number) => void }) {
  const [m, setM] = useState(prevYm());
  const [run, busy] = useAction();
  return <Modal title="Salary voucher" onClose={() => onClose()} footer={<><Btn onClick={() => onClose()}>Cancel</Btn>
    <Btn kind="pri" busy={busy} onClick={async () => { const r: any = await run(() => api('/fin/payroll', { body: { company, month: m } }), (x: any) => `${x.no} prepared as a draft — review and submit.`); if (r) onClose(r.id); }}>Prepare</Btn></>}>
    <p className="muted">Gross pay, employer PF / ESI, professional tax and TDS under s.392 for every active employee. It is created as a draft to review.</p>
    <Field label="Month"><input type="month" value={m} onChange={e => setM(e.target.value)} /></Field>
  </Modal>;
}

type Line = { account: string; branch?: string; debit?: string; credit?: string; cost_centre?: string; dept?: string };
function VoucherEditor({ company, meta, init, onClose }: { company: string; meta: any; init: any; onClose: (id?: number) => void }) {
  const t0 = meta.types.find((t: any) => t.code === (init.vtype || 'EX'));
  const [v, setV] = useState<any>({ vtype: t0.code, vdate: today(), branch_id: `${company}-HO`, ref: '', narration: '', cost_centre: '', dept: '', party: null,
    lines: [{ account: '', debit: '', credit: '' }] as Line[], gstOn: false, gst: { rows: [{ hsn: '', taxable: '', rate: '18' }], itc: 'Eligible', rcm: false }, tdsOn: false,
    tds: { section: '', base: '', baseSet: false }, options: {}, ...init, id: init.id });
  const [pv, setPv] = useState<any>(null);
  const [run, busy] = useAction();
  const T = meta.types.find((t: any) => t.code === v.vtype);
  const parties = T?.party === 'cust' ? meta.customers : T?.party === 'sup' ? meta.suppliers : [];
  const amountLines = v.lines.filter((l: Line) => l.account && (Number(l.debit) || Number(l.credit)));
  const lineBase = amountLines.filter((l: Line) => !/^11[0-2]/.test(l.account)).reduce((s: number, l: Line) => s + (Number(l.debit) || Number(l.credit) || 0), 0);
  const taxable = v.gstOn ? v.gst.rows.reduce((s: number, r: any) => s + (Number(r.taxable) || 0), 0) : lineBase;
  useEffect(() => { if (v.tdsOn && !v.tds.baseSet) setV((x: any) => ({ ...x, tds: { ...x.tds, base: String(taxable || '') } })); }, [taxable, v.tdsOn]);  // eslint-disable-line react-hooks/exhaustive-deps
  const body = () => ({ vtype: v.vtype, company, vdate: v.vdate, branch_id: v.branch_id, ref: v.ref, narration: v.narration, cost_centre: v.cost_centre || null, dept: v.dept || null,
    party: v.party?.id ? v.party : null, lines: amountLines.map((l: Line) => ({ ...l, debit: Number(l.debit) || 0, credit: Number(l.credit) || 0 })),
    gst: v.gstOn ? { rows: v.gst.rows.filter((r: any) => Number(r.taxable)).map((r: any) => ({ hsn: r.hsn, taxable: Number(r.taxable), rate: Number(r.rate) })), itc: v.gst.itc, rcm: v.gst.rcm } : null,
    tds: v.tdsOn && v.tds.section ? { section: v.tds.section, base: Number(v.tds.base) || 0 } : null, options: v.options });
  const key = JSON.stringify(body());
  useEffect(() => {                                     // live preview: GST, TDS, party line, approval route, errors
    const h = setTimeout(() => { api('/fin/vouchers/preview', { body: JSON.parse(key) }).then(setPv).catch(e => setPv({ errors: [errText(e)] })); }, 350);
    return () => clearTimeout(h);
  }, [key]);
  const setLine = (i: number, p: Partial<Line>) => setV((x: any) => ({ ...x, lines: x.lines.map((l: Line, k: number) => (k === i ? { ...l, ...p } : l)) }));
  const save = async (submit: boolean) => {
    const r: any = await run(() => (v.id ? api(`/fin/vouchers/${v.id}`, { method: 'PUT', body: body() }).then(x => (submit ? api(`/fin/vouchers/${v.id}/submit`, { body: {} }) : x))
      : api('/fin/vouchers', { body: { ...body(), submit } })),
      (x: any) => (x.status === 'Posted' ? `${x.no} posted (${x.journal_no}).` : x.status === 'Pending approval' ? `${x.no} sent to the ${x.approver_role} for approval.` : `${x.no} saved as a draft.`));
    if (r) onClose(r.id);
  };
  const acct = (a: string) => meta.accounts.find((x: any) => x.code === a)?.name || '';
  return <Modal wide title={v.id ? `Edit ${v.no}` : `New ${T?.label || 'voucher'}`} onClose={() => onClose()} footer={<>
    <Btn onClick={() => onClose()}>Cancel</Btn><Btn busy={busy} onClick={() => save(false)}>Save draft</Btn>
    <Btn kind="pri" busy={busy} disabled={!!pv?.errors?.length} onClick={() => save(true)}>{pv?.route ? `Submit to ${pv.route.role}` : 'Post'}</Btn></>}>
    <div className="form grid3">
      <Field label="Voucher type *"><select value={v.vtype} disabled={!!v.id} onChange={e => setV({ ...v, vtype: e.target.value, party: null })}>{meta.types.map((t: any) => <option key={t.code} value={t.code}>{t.label}</option>)}</select></Field>
      <Field label="Date *"><input type="date" value={v.vdate} onChange={e => setV({ ...v, vdate: e.target.value })} /></Field>
      <Field label="Location"><select value={v.branch_id} onChange={e => setV({ ...v, branch_id: e.target.value })}>{meta.branches.map((b: any) => <option key={b.id} value={b.id}>{b.name}</option>)}</select></Field>
      {T?.party && <Field label={T.party === 'cust' ? 'Customer' : 'Supplier'}><select value={v.party?.id || ''} onChange={e => setV({ ...v, party: e.target.value ? { kind: T.party, id: e.target.value } : null })}>
        <option value="">—</option>{parties.map((p: any) => <option key={p.id} value={p.id}>{p.name}{p.gstin ? ` · ${p.gstin}` : ''}</option>)}</select></Field>}
      <Field label={['PI', 'EX', 'AC'].includes(v.vtype) ? 'Supplier bill no. *' : 'Reference'}><input value={v.ref} onChange={e => setV({ ...v, ref: e.target.value })} /></Field>
      <Field label="Cost centre"><select value={v.cost_centre} onChange={e => setV({ ...v, cost_centre: e.target.value })}><option value="">—</option>{meta.cost_centres.filter((c: any) => c.kind !== 'Department').map((c: any) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></Field>
      <Field label="Department"><select value={v.dept} onChange={e => setV({ ...v, dept: e.target.value })}><option value="">—</option>{meta.cost_centres.filter((c: any) => c.kind === 'Department').map((c: any) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></Field>
      <Field label="Narration" wide><input value={v.narration} onChange={e => setV({ ...v, narration: e.target.value })} /></Field>
      {['PV', 'RV'].includes(v.vtype) && <Field label="Cheque no."><input value={v.options.cheque || ''} onChange={e => setV({ ...v, options: { ...v.options, cheque: e.target.value } })} /></Field>}
      {v.vtype === 'PI' && <Field label="Purchase order (3-way match)"><input value={v.options.po || ''} onChange={e => setV({ ...v, options: { ...v.options, po: e.target.value } })} /></Field>}
    </div>
    <h4>Ledger lines {T?.party && <span className="muted small">— the {T.party === 'cust' ? 'customer' : 'supplier'} line is added automatically to balance</span>}</h4>
    <datalist id="fin-accts">{meta.accounts.filter((a: any) => a.active).map((a: any) => <option key={a.code} value={a.code}>{a.name}</option>)}</datalist>
    <div className="vlines">{v.lines.map((l: Line, i: number) => <div key={i} className="vline">
      <input list="fin-accts" placeholder="Account" value={l.account} onChange={e => setLine(i, { account: e.target.value })} aria-label="Account" /><span className="muted small vl-name">{acct(l.account)}</span>
      <input inputMode="decimal" placeholder="Debit" value={l.debit || ''} onChange={e => setLine(i, { debit: e.target.value, credit: '' })} aria-label="Debit" />
      <input inputMode="decimal" placeholder="Credit" value={l.credit || ''} onChange={e => setLine(i, { credit: e.target.value, debit: '' })} aria-label="Credit" />
      <Btn kind="link" onClick={() => setV({ ...v, lines: v.lines.filter((_: Line, k: number) => k !== i) })} aria-label="Remove line">×</Btn></div>)}
      <Btn kind="link" onClick={() => setV({ ...v, lines: [...v.lines, { account: '', debit: '', credit: '' }] })}>+ Line</Btn></div>
    {T?.gst && <><label className="check"><input type="checkbox" checked={v.gstOn} onChange={e => setV({ ...v, gstOn: e.target.checked })} /> GST on this voucher</label>
      {v.gstOn && <div className="vlines">{v.gst.rows.map((r: any, i: number) => <div key={i} className="vline">
        <input list="fin-hsn" placeholder="HSN / SAC" value={r.hsn} onChange={e => setV({ ...v, gst: { ...v.gst, rows: v.gst.rows.map((x: any, k: number) => (k === i ? { ...x, hsn: e.target.value, rate: meta.hsn.find((h: any) => h.code === e.target.value)?.rate ?? x.rate } : x)) } })} aria-label="HSN / SAC" />
        <input inputMode="decimal" placeholder="Taxable value" value={r.taxable} onChange={e => setV({ ...v, gst: { ...v.gst, rows: v.gst.rows.map((x: any, k: number) => (k === i ? { ...x, taxable: e.target.value } : x)) } })} aria-label="Taxable value" />
        <select value={r.rate} onChange={e => setV({ ...v, gst: { ...v.gst, rows: v.gst.rows.map((x: any, k: number) => (k === i ? { ...x, rate: e.target.value } : x)) } })} aria-label="GST rate">{['0', '0.25', '3', '5', '12', '18', '28', '40'].map(x => <option key={x} value={x}>{x}%</option>)}</select>
      </div>)}<datalist id="fin-hsn">{meta.hsn.map((h: any) => <option key={h.code} value={h.code}>{h.description} · {h.rate}%</option>)}</datalist>
        <div className="row"><Btn kind="link" onClick={() => setV({ ...v, gst: { ...v.gst, rows: [...v.gst.rows, { hsn: '', taxable: '', rate: '18' }] } })}>+ GST line</Btn>
          {['in', 'inRev'].includes(T.gst) && <><select value={v.gst.itc} onChange={e => setV({ ...v, gst: { ...v.gst, itc: e.target.value } })} aria-label="Input tax credit"><option>Eligible</option><option>Blocked</option><option>Ineligible</option></select>
            <label className="check"><input type="checkbox" checked={v.gst.rcm} onChange={e => setV({ ...v, gst: { ...v.gst, rcm: e.target.checked } })} /> Reverse charge</label></>}</div></div>}</>}
    {T?.tds && <><label className="check"><input type="checkbox" checked={v.tdsOn} onChange={e => setV({ ...v, tdsOn: e.target.checked })} /> {T.tds === 'recv' ? 'TDS deducted by the customer' : 'Deduct TDS'}</label>
      {v.tdsOn && <div className="form grid3"><Field label="Section"><select value={v.tds.section} onChange={e => setV({ ...v, tds: { ...v.tds, section: e.target.value } })}><option value="">Choose…</option>
        {meta.tds_sections.filter((s: any) => s.active).map((s: any) => <option key={s.code} value={s.code}>{s.code} · {s.label} ({s.new_ref})</option>)}</select></Field>
        <Field label="Base amount" hint="Follows the taxable value until you change it"><input inputMode="decimal" value={v.tds.base} onChange={e => setV({ ...v, tds: { ...v.tds, base: e.target.value, baseSet: true } })} /></Field>
        <Field label="TDS"><input readOnly value={!v.party?.id ? 'Choose the supplier first' : pv?.tds?.amount != null ? `${inr(pv.tds.amount)} @ ${pv.tds.rate}%` : '—'} /></Field>
        {pv?.tds?.note && <p className="muted small wide">{pv.tds.note}</p>}</div>}</>}
    {pv && <Panel title="Journal that will post">
      {T?.party && !v.party?.id && <div className="note info">Choose the {T.party === 'cust' ? 'customer' : 'supplier'}: the party line that balances this voucher is added for you.</div>}
      {pv.errors?.length > 0 && <div className="note bad">{pv.errors.join(' ')}</div>}
      {pv.duplicate?.msg && <div className="note warn">{pv.duplicate.msg} {pv.duplicate.soft && <label className="check"><input type="checkbox" checked={!!v.options.dup_ok} onChange={e => setV({ ...v, options: { ...v.options, dup_ok: e.target.checked } })} /> Not a duplicate</label>}</div>}
      <DataTable rows={(pv.built || []).map((x: any, i: number) => ({ ...x, k: i }))} keyOf={r => r.k} cols={[{ key: 'account', label: 'Account', primary: true, render: r => <>{r.account} {acct(r.account)}{r.auto && <span className="muted small"> · {r.auto}</span>}</> },
        { key: 'branch', label: 'Location', hideSmall: true }, { key: 'debit', label: 'Debit', align: 'r', render: r => (r.debit ? inr(r.debit) : '') }, { key: 'credit', label: 'Credit', align: 'r', render: r => (r.credit ? inr(r.credit) : '') }]} />
      <p className="muted small">Total {inr(pv.total)}{pv.tax ? ` · GST ${inr(pv.tax)}` : ''}{pv.route ? ` · needs ${pv.route.role}: ${pv.route.why}` : ' · within your limit: posts on submit'}</p>
    </Panel>}
    {pv?.errors?.length === 0 && !v.id && ['EX', 'PI', 'AC', 'GA'].includes(v.vtype) && <p className="muted small">Attach the supporting bill on the voucher page after saving; it cannot be approved without it.</p>}
  </Modal>;
}

function VoucherPage() {
  const { id = '' } = useParams();
  const [sp] = useSearchParams();
  const nav = useNavigate();
  const company = sp.get('company') || 'KG';
  const meta = useMeta(company);
  const { data: v, setData, err, reload } = useLoad<any>(() => api(`/fin/vouchers/${id}`), [id]);
  const [edit, setEdit] = useState(false);
  const [why, setWhy] = useState('');
  const [run, busy] = useAction();
  if (err) return <Page title="Voucher"><ErrorNote err={err} /></Page>;
  if (!v || !meta.data) return <Page title="Voucher"><p className="muted">Loading…</p></Page>;
  const act = async (path: string, body: any, msg: (x: any) => string) => { const r: any = await run(() => api(`/fin/vouchers/${v.id}/${path}`, { body }), msg); if (r) { if (r.id !== v.id) nav(`/finance/vouchers/${r.id}?company=${company}`); else setData(r); } };
  const acct = (a: string) => meta.data.accounts.find((x: any) => x.code === a)?.name || '';
  return <Page title={`${v.type_label} ${v.no}`} sub={<><Pill s={v.status} /> {day(v.vdate)} · {v.party_name || ''} {v.narration}{v.lock && <span className="muted"> · {v.lock}</span>}</>}
    actions={<><Btn onClick={() => nav(`/finance?tab=vch&company=${company}`)}>‹ Vouchers</Btn><Btn kind="link" onClick={reload}>Refresh</Btn></>}>
    <div className="actions">
      {v.can.edit && <Btn onClick={() => setEdit(true)}>Edit</Btn>}
      {v.can.submit && <Btn kind="pri" busy={busy} onClick={() => act('submit', {}, x => (x.status === 'Posted' ? `Posted (${x.journal_no}).` : `Sent to the ${x.approver_role} for approval.`))}>Submit</Btn>}
      {v.can.reverse && <Btn kind="danger" onClick={() => setWhy(' ')}>Reverse</Btn>}
      {v.can.cancel && <ConfirmBtn label="Cancel voucher" busy={busy} onConfirm={() => act('cancel', { reason: 'Cancelled by ' + (v.maker_name || 'maker') }, () => 'Cancelled.')} />}
      {v.approval_no && <Btn onClick={() => nav('/approvals')}>Approval {v.approval_no}</Btn>}
    </div>
    <div className="grid2">
      <Panel title="Details"><Kv items={[['Amount', inr(v.total)], ['Taxable · GST', `${inr(v.taxable)} · ${inr(v.tax)}`], ['TDS', v.tds?.section ? `${v.tds.section} · ${inr(v.tds_amount)} — ${v.tds.note || ''}` : undefined],
        ['Reference', v.ref], ['Location', v.branch_id], ['Cost centre · dept', [v.cost_centre, v.dept].filter(Boolean).join(' · ') || undefined], ['Maker', `${v.maker_name} (${v.maker_role})`],
        ['Approval', v.approval_no ? `${v.approval_no} · ${v.approver_role}` : undefined], ['Approved', v.approved_at ? `${when(v.approved_at)} · ${v.approved_by_name}` : undefined],
        ['Journal', v.journal_no], ['Reversed by', v.reversed_by], ['Reason', v.reason]]} />
        <EvidencePanel refNo={v.no} company={v.company} /></Panel>
      <Panel title="Journal"><DataTable rows={(v.built || []).map((x: any, i: number) => ({ ...x, k: i }))} keyOf={r => r.k} cols={[
        { key: 'account', label: 'Account', primary: true, render: r => <>{r.account} {acct(r.account)}{r.auto && <span className="muted small"> · {r.auto}</span>}{r.note && <span className="muted small"> · {r.note}</span>}</> },
        { key: 'branch', label: 'Location', hideSmall: true }, { key: 'debit', label: 'Debit', align: 'r', render: r => (r.debit ? inr(r.debit) : '') }, { key: 'credit', label: 'Credit', align: 'r', render: r => (r.credit ? inr(r.credit) : '') }]} />
        {v.gst?.calc && <p className="muted small">GST: {v.gst.calc.inter ? 'IGST (inter-state)' : 'CGST + SGST'} · place of supply {v.gst.calc.pos}{v.gst.rcm ? ' · reverse charge' : ''}{v.gst.itc && v.gst.itc !== 'Eligible' ? ` · ITC ${v.gst.itc.toLowerCase()}` : ''}</p>}
      </Panel>
    </div>
    <Panel title="History"><ol className="timeline">{(v.log || []).slice().reverse().map((l: any, i: number) => <li key={i}><span className="muted">{when(l[0])} · {l[1]} ({l[2]})</span> <b>{l[3]}</b>{l[4] && <div className="muted small">{l[4]}</div>}</li>)}</ol></Panel>
    {edit && <VoucherEditor company={company} meta={meta.data} init={{ ...v, lines: v.entered, gstOn: !!v.gst?.rows, gst: v.gst?.rows ? { ...v.gst, rows: v.gst.rows.map((r: any) => ({ ...r, taxable: String(r.taxable), rate: String(r.rate) })) } : { rows: [{ hsn: '', taxable: '', rate: '18' }], itc: 'Eligible', rcm: false },
      tdsOn: !!v.tds?.section, tds: v.tds ? { ...v.tds, baseSet: true } : { section: '', base: '' }, party: v.party_id ? { kind: v.party_kind, id: v.party_id } : null }} onClose={() => { setEdit(false); reload(); }} />}
    {why && <Modal title={`Reverse ${v.no}`} onClose={() => setWhy('')} footer={<><Btn onClick={() => setWhy('')}>Back</Btn>
      <Btn kind="danger" busy={busy} onClick={async () => { await act('reverse', { reason: why.trim() }, x => `Reversal ${x.no} sent for approval.`); setWhy(''); }}>Raise reversal</Btn></>}>
      <p className="muted">A reversal voucher swaps every debit and credit. It always needs a checker; the original becomes Reversed when it posts.</p>
      <Field label="Reason *" wide><textarea rows={3} value={why.trim()} onChange={e => setWhy(e.target.value || ' ')} /></Field></Modal>}
  </Page>;
}

// ------------------------------------------------------------ GST
function Gst({ company, meta }: { company: string; meta: any }) {
  const [m, setM] = useState(ym());
  const [view, setView] = useState<'sum' | '1' | '3B' | 'recon' | 'docs'>('sum');
  const s = useLoad<any>(() => api(`/fin/gst/summary?company=${company}&ym=${m}`), [company, m]);
  const [dlg, setDlg] = useState<'' | 'pay' | 'file1' | 'file3B'>('');
  return <>
    <div className="filters"><input type="month" value={m} onChange={e => setM(e.target.value)} aria-label="Month" />
      <Tabs value={view} onChange={setView} tabs={[['sum', 'Set-off & payment'], ['1', 'GSTR-1'], ['3B', 'GSTR-3B'], ['recon', '2B / 2A reconciliation'], ['docs', 'Documents & e-invoice']]} /></div>
    {view === 'sum' && s.data && <GstSummary s={s.data} canAdd={meta.can.add} canSubmit={meta.can.submit} onDlg={setDlg} />}
    {(view === '1' || view === '3B') && <GstReturn company={company} m={m} ret={view} />}
    {view === 'recon' && <GstRecon company={company} m={m} />}
    {view === 'docs' && <GstDocs company={company} m={m} />}
    {dlg && <GstDialog kind={dlg} company={company} m={m} s={s.data} onClose={() => { setDlg(''); s.reload(); }} />}
  </>;
}

function GstSummary({ s, canAdd, canSubmit, onDlg }: { s: any; canAdd: boolean; canSubmit: boolean; onDlg: (d: any) => void }) {
  const o = s.setoff;
  const H = ['igst', 'cgst', 'sgst', 'cess'];
  return <>
    <div className="tiles small">
      <div className="tile"><span>Output tax</span><b>{money(H.reduce((t, h) => t + o.liability[h], 0))}</b></div>
      <div className="tile"><span>Credit available</span><b>{money(H.reduce((t, h) => t + o.credit[h], 0) + H.reduce((t, h) => t + Object.values(o.use[h] as Record<string, number>).reduce((a, b) => a + b, 0), 0))}</b></div>
      <div className="tile"><span>Pay in cash</span><b>{money(o.cash.total)}</b><small>RCM {money(o.cash.rcm)}</small></div>
      <div className="tile"><span>Interest · late fee</span><b>{money(o.interest)} · {money(o.fee)}</b><small>{o.late_days} days late</small></div>
      <div className="tile"><span>Due</span><b>{day(o.due)}</b><small>{s.challan ? `${s.challan.status} · ${s.challan.voucher_no}` : 'not paid'}</small></div>
      <div className="tile"><span>Filed</span><b>{s.filed['1'] ? 'GSTR-1 ✓' : 'GSTR-1 —'} · {s.filed['3B'] ? '3B ✓' : '3B —'}</b><small>{s.gstin}</small></div>
    </div>
    <Panel title="Set-off under rule 88A (IGST credit first)" actions={<>{canAdd && !s.challan && <Btn kind="pri" onClick={() => onDlg('pay')}>Pay GST</Btn>}
      {canSubmit && !s.filed['1'] && <Btn onClick={() => onDlg('file1')}>Record GSTR-1 filing</Btn>}{canSubmit && !s.filed['3B'] && <Btn onClick={() => onDlg('file3B')}>Record GSTR-3B filing</Btn>}</>}>
      <div className="table-wrap scroll-x"><table className="grid report"><thead><tr><th>Head</th><th className="r">Opening credit</th><th className="r">Credit this month</th><th className="r">Liability</th><th className="r">Used from IGST</th><th className="r">Used from CGST</th><th className="r">Used from SGST</th><th className="r">Cash</th><th className="r">Credit c/f</th></tr></thead>
        <tbody>{H.map(h => <tr key={h}><td>{h.toUpperCase()}</td><td className="r">{inr(o.opening[h])}</td><td className="r">{inr(o.books.credit[h])}</td><td className="r">{inr(o.liability[h])}</td>
          <td className="r">{inr(o.use.igst[h] || 0)}</td><td className="r">{inr(o.use.cgst[h] || 0)}</td><td className="r">{inr(o.use.sgst[h] || 0)}</td><td className="r"><b>{inr(o.cash[h])}</b></td><td className="r">{inr(o.closing[h])}</td></tr>)}</tbody></table></div>
      <p className="muted small">Entries dated in a month whose GSTR-3B is filed need CFO / Director approval. Government portals are not connected: file on the GST portal and record the ARN here.</p>
    </Panel>
  </>;
}

function GstDialog({ kind, company, m, s, onClose }: { kind: string; company: string; m: string; s: any; onClose: () => void }) {
  const [f, setF] = useState<any>({ cpin: '', cin: '', bank: '1120', date: today(), arn: '' });
  const [run, busy] = useAction();
  const go = async () => {
    const r = kind === 'pay' ? await run(() => api('/fin/gst/pay', { body: { company, ym: m, ...f } }), (x: any) => (x.status === 'Posted' ? `GST paid (${x.no}).` : `${x.no} sent for approval — attach the challan.`))
      : await run(() => api('/fin/gst/file', { body: { company, ym: m, ret: kind === 'file1' ? '1' : '3B', arn: f.arn, date: f.date } }), () => 'Filing recorded.');
    if (r) onClose();
  };
  return <Modal title={kind === 'pay' ? `Pay GST for ${m}` : `Record ${kind === 'file1' ? 'GSTR-1' : 'GSTR-3B'} filing for ${m}`} onClose={onClose} footer={<><Btn onClick={onClose}>Cancel</Btn><Btn kind="pri" busy={busy} onClick={go}>{kind === 'pay' ? 'Create GST payment voucher' : 'Save'}</Btn></>}>
    {kind === 'pay' ? <><p>Cash to pay {inr(s.setoff.cash.total + s.setoff.interest + s.setoff.fee)} (tax {inr(s.setoff.cash.total)}, interest {inr(s.setoff.interest)}, late fee {inr(s.setoff.fee)}).</p>
      <div className="form grid2"><Field label="CPIN (14 digits)"><input value={f.cpin} onChange={e => setF({ ...f, cpin: e.target.value })} /></Field><Field label="CIN"><input value={f.cin} onChange={e => setF({ ...f, cin: e.target.value })} /></Field>
        <Field label="Paid from"><input value={f.bank} onChange={e => setF({ ...f, bank: e.target.value })} /></Field><Field label="Date"><input type="date" value={f.date} onChange={e => setF({ ...f, date: e.target.value })} /></Field></div></>
      : <div className="form grid2"><Field label="ARN from the GST portal *"><input value={f.arn} onChange={e => setF({ ...f, arn: e.target.value })} /></Field><Field label="Filed on"><input type="date" value={f.date} onChange={e => setF({ ...f, date: e.target.value })} /></Field></div>}
  </Modal>;
}

function GstReturn({ company, m, ret }: { company: string; m: string; ret: '1' | '3B' }) {
  const { data: g, err } = useLoad<any>(() => api(`/fin/gst/return/${ret}?company=${company}&ym=${m}`), [company, m, ret]);
  if (err) return <ErrorNote err={err} />;
  if (!g) return <p className="muted">Loading…</p>;
  const json = <Btn onClick={() => download(`/fin/gst/return/${ret}/json?company=${company}&ym=${m}`, `GSTR${ret}_${m}.json`)}>Portal JSON</Btn>;
  if (ret === '3B') {
    const r = (label: string, o: any) => ({ label, ...o });
    return <Panel title={`GSTR-3B · ${g.gstin} · ${m}`} actions={json}><DataTable rows={[r('3.1(a) Outward taxable', g.t31a), r('3.1(c) Nil-rated / exempt', g.t31c), r('3.1(d) Reverse charge inward', g.t31d),
      r('4(A)(3) ITC on reverse charge', g.itc_rcm), r('4(A)(5) ITC — all other', g.itc_other), r('4(D) Ineligible ITC', g.itc_blocked), r('5 Exempt / nil inward', g.t5)]} keyOf={x => x.label}
      cols={[{ key: 'label', label: 'Table', primary: true }, ...['taxable', 'igst', 'cgst', 'sgst', 'cess'].map(k => ({ key: k, label: k.toUpperCase(), align: 'r' as const, render: (x: any) => inr(x[k]) }))]} /></Panel>;
  }
  const docCols = [{ key: 'no', label: 'Document', primary: true, render: (d: any) => <span className="mono">{d.no}</span> }, { key: 'date', label: 'Date', render: (d: any) => day(d.date) },
    { key: 'name', label: 'Party' }, { key: 'gstin', label: 'GSTIN', hideSmall: true }, { key: 'taxable', label: 'Taxable', align: 'r' as const, render: (d: any) => inr(d.taxable) }, { key: 'tax', label: 'Tax', align: 'r' as const, render: (d: any) => inr(d.tax) }];
  return <>
    <Panel title={`GSTR-1 · ${g.gstin} · ${m}`} actions={<>{g.filed && <Pill s={`Filed · ${g.filed.arn}`} tone="ok" />}{json}</>}>
      <h4>B2B ({g.b2b.length})</h4><DataTable rows={g.b2b} keyOf={d => d.no} cols={docCols} empty="No B2B invoices." />
      <h4>B2C large ({g.b2cl.length}) · credit / debit notes ({g.cdnr.length + g.cdnur.length})</h4><DataTable rows={[...g.b2cl, ...g.cdnr, ...g.cdnur]} keyOf={d => d.no} cols={docCols} empty="None." />
      <h4>B2C small (by place of supply and rate)</h4><DataTable rows={g.b2cs} keyOf={x => x.pos + x.rate} cols={[{ key: 'pos', label: 'POS', primary: true }, { key: 'rate', label: 'Rate', align: 'r' }, ...['taxable', 'igst', 'cgst', 'sgst'].map(k => ({ key: k, label: k.toUpperCase(), align: 'r' as const, render: (x: any) => inr(x[k]) }))]} />
      <h4>HSN summary</h4><DataTable rows={[...g.hsn_b2b, ...g.hsn_b2c]} keyOf={x => x.hsn + x.rate + x.uqc} cols={[{ key: 'hsn', label: 'HSN', primary: true }, { key: 'description', label: 'Description', hideSmall: true }, { key: 'qty', label: 'Qty', align: 'r' }, { key: 'rate', label: 'Rate', align: 'r' }, { key: 'taxable', label: 'Taxable', align: 'r', render: x => inr(x.taxable) }]} />
      <h4>Documents issued</h4><DataTable rows={g.docs} keyOf={x => x.nature + x.series} cols={[{ key: 'nature', label: 'Nature', primary: true }, { key: 'series', label: 'Series' }, { key: 'from', label: 'From', align: 'r' }, { key: 'to', label: 'To', align: 'r' }, { key: 'count', label: 'Total', align: 'r' }, { key: 'cancelled', label: 'Cancelled', align: 'r' }]} />
    </Panel>
  </>;
}

function GstRecon({ company, m }: { company: string; m: string }) {
  const [kind, setKind] = useState('2B');
  const r = useLoad<any>(() => api(`/fin/gst/reconcile?company=${company}&ym=${m}&kind=${kind}`), [company, m, kind]);
  const [run, busy] = useAction();
  const file = useRef<HTMLInputElement>(null);
  const load = async (sample: boolean) => {
    const f = file.current?.files?.[0];
    const res = await run(() => (sample ? api('/fin/gst/portal', { body: { company, kind, ym: m, sample: 1 } }) : (() => { const fd = new FormData(); fd.append('file', f!); fd.append('company', company); fd.append('kind', kind); fd.append('ym', m); return api('/fin/gst/portal', { form: fd }); })()),
      (x: any) => `${x.rows} rows loaded · ${x.mismatches} mismatch(es).`);
    if (res) r.reload();
    if (file.current) file.current.value = '';
  };
  return <Panel title={`GSTR-${kind} reconciliation · ${m}`} actions={<><select value={kind} onChange={e => setKind(e.target.value)} aria-label="Return"><option value="2B">GSTR-2B</option><option value="2A">GSTR-2A</option></select>
    <input ref={file} type="file" accept=".xlsx,.csv" hidden onChange={() => load(false)} /><Btn busy={busy} onClick={() => file.current?.click()}>Import portal file</Btn><Btn busy={busy} onClick={() => load(true)}>Sample data</Btn></>}>
    {r.data && !r.data.loaded && <p className="note">Download GSTR-{kind} from the GST portal (Excel) and import it here. The portal is not connected in this version.</p>}
    {r.data?.loaded && <p className="muted small">{r.data.source} · {when(r.data.loaded_at)} · {r.data.mismatches} mismatch(es) · ITC at risk {inr(r.data.itc_at_risk)}</p>}
    <DataTable rows={(r.data?.rows || []).map((x: any, i: number) => ({ ...x, k: i }))} keyOf={x => x.k} rowClass={x => (x.status === 'Matched' ? '' : 'var-short-row')} empty="Nothing to reconcile." cols={[
      { key: 'status', label: 'Status', primary: true, render: x => <Pill s={x.status} tone={x.status === 'Matched' ? 'ok' : 'bad'} /> },
      { key: 'name', label: 'Supplier', render: x => (x.books || x.portal).name || (x.books || x.portal).gstin }, { key: 'no', label: 'Invoice', render: x => x.books?.no || x.portal?.inv_no },
      { key: 'bt', label: 'Tax in books', align: 'r', render: x => (x.books ? inr(x.books.tax) : '—') },
      { key: 'pt', label: `Tax in ${kind}`, align: 'r', render: x => (x.portal ? inr(x.portal.igst + x.portal.cgst + x.portal.sgst + x.portal.cess) : '—') },
      { key: 'diff', label: 'Difference', align: 'r', render: x => <span className={x.diff ? 'var-short' : ''}>{inr(x.diff)}</span> }]} />
  </Panel>;
}

function GstDocs({ company, m }: { company: string; m: string }) {
  const d = useLoad<any[]>(() => api(`/fin/gst/docs?company=${company}&ym=${m}`), [company, m]);
  const [run, busy] = useAction();
  const [ewb, setEwb] = useState<any>(null);
  const irn = async (no: string) => { const r = await run(() => api('/fin/gst/einvoice', { body: { company, no } }), (x: any) => `IRN generated (${x.ack_no}).`); if (r) d.reload(); };
  return <Panel title="GST documents" actions={<span className="muted small">IRN and e-way numbers are generated locally in the official format (portal not connected).</span>}>
    <DataTable rows={d.data} keyOf={x => x.dir + x.no} empty="No GST documents this month." cols={[
      { key: 'no', label: 'Document', primary: true, render: x => <><span className="mono">{x.no}</span> <span className="muted small">{x.dir === 'out' ? 'outward' : 'inward'} · {x.type} · {x.src}</span></> },
      { key: 'date', label: 'Date', render: x => day(x.date) }, { key: 'name', label: 'Party', render: x => <>{x.name} <span className="muted small">{x.gstin}</span></> },
      { key: 'val', label: 'Value', align: 'r', render: x => inr(x.val) }, { key: 'tax', label: 'Tax', align: 'r', render: x => inr(x.tax) },
      { key: 'e', label: 'E-invoice / e-way', render: x => <>{x.einvoice?.status === 'Generated' ? <Pill s={`IRN ${String(x.einvoice.irn).slice(0, 8)}…`} tone="ok" />
        : x.dir === 'out' && x.gstin ? <Btn kind="link" busy={busy} onClick={() => irn(x.no)}>Generate IRN</Btn> : ''}
        {x.dir === 'out' && x.val >= 50000 && <Btn kind="link" onClick={() => setEwb(x)}>{x.einvoice?.ewb ? 'E-way ✓' : 'E-way bill'}</Btn>}</> }]} />
    {ewb && <EwbDialog company={company} doc={ewb} onClose={() => { setEwb(null); d.reload(); }} />}
  </Panel>;
}

function EwbDialog({ company, doc, onClose }: { company: string; doc: any; onClose: () => void }) {
  const [f, setF] = useState<any>({ no: '', vehicle: '', km: '', date: today() });
  const [run, busy] = useAction();
  const go = async (generate: boolean) => { const r = await run(() => api('/fin/gst/eway', { body: { company, no: doc.no, ...f, generate } }), (x: any) => `E-way bill ${x.no} valid to ${x.valid}.`); if (r) onClose(); };
  return <Modal title={`E-way bill · ${doc.no}`} onClose={onClose} footer={<><Btn onClick={onClose}>Cancel</Btn><Btn busy={busy} onClick={() => go(true)}>Generate locally</Btn><Btn kind="pri" busy={busy} onClick={() => go(false)}>Save number</Btn></>}>
    <div className="form grid2"><Field label="E-way bill no. (12 digits)"><input value={f.no} onChange={e => setF({ ...f, no: e.target.value })} /></Field><Field label="Vehicle"><input value={f.vehicle} onChange={e => setF({ ...f, vehicle: e.target.value })} /></Field>
      <Field label="Distance (km)"><input inputMode="numeric" value={f.km} onChange={e => setF({ ...f, km: e.target.value })} /></Field><Field label="Date"><input type="date" value={f.date} onChange={e => setF({ ...f, date: e.target.value })} /></Field></div>
  </Modal>;
}

// ------------------------------------------------------------ TDS
function Tds({ company, meta }: { company: string; meta: any }) {
  const [view, setView] = useState<'reg' | 'ret' | 'chk'>('reg');
  const reg = useLoad<any[]>(() => api(`/fin/tds/register?company=${company}`), [company]);
  const [pick, setPick] = useState<number[]>([]);
  const [ch, setCh] = useState(false);
  const due = (reg.data || []).filter(e => e.kind === 'Payable' && e.status === 'Deducted' && e.amount > 0);
  return <>
    <Tabs value={view} onChange={setView} tabs={[['reg', 'Deductions & challans'], ['ret', 'Quarterly returns & certificates'], ['chk', 'PAN & threshold checks']]} />
    {view === 'reg' && <Panel title="TDS register" actions={meta.can.add && <Btn kind="pri" disabled={!pick.length} onClick={() => setCh(true)}>Pay challan ({pick.length})</Btn>}>
      <p className="muted small">Income-tax Act 2025 from 1 April 2026: s.392 salary, s.393 other TDS, s.394 TCS. Deposit by the 7th of the next month (30 April for March) with challan Form 141.</p>
      <DataTable rows={reg.data} keyOf={e => e.id} empty="No deductions yet." rowClass={e => (e.interest?.overdue ? 'row-overdue' : '')} cols={[
        { key: 'pick', label: '', render: e => (due.includes(e) ? <input type="checkbox" checked={pick.includes(e.id)} onChange={() => setPick(p => (p.includes(e.id) ? p.filter(x => x !== e.id) : [...p, e.id]))} aria-label="Select" /> : '') },
        { key: 'ded_date', label: 'Date', primary: true, render: e => day(e.ded_date) }, { key: 'name', label: 'Deductee', render: e => <>{e.name} <span className="muted small">{e.pan || 'no PAN'}</span></> },
        { key: 'section', label: 'Section', render: e => <>{e.section} <span className="muted small">{e.form}</span></> }, { key: 'base', label: 'Base', align: 'r', hideSmall: true, render: e => inr(e.base) },
        { key: 'amount', label: 'TDS', align: 'r', render: e => inr(e.amount) }, { key: 'interest', label: 'Interest', align: 'r', hideSmall: true, render: e => (e.interest?.total ? inr(e.interest.total) : '') },
        { key: 'status', label: 'Status', render: e => <Pill s={e.status} tone={e.interest?.overdue ? 'bad' : undefined} /> }]} />
    </Panel>}
    {view === 'ret' && <TdsReturns company={company} meta={meta} />}
    {view === 'chk' && <TdsChecks company={company} />}
    {ch && <TdsChallan company={company} ids={pick} onClose={() => { setCh(false); setPick([]); reg.reload(); }} />}
  </>;
}

function TdsChallan({ company, ids, onClose }: { company: string; ids: number[]; onClose: () => void }) {
  const [f, setF] = useState({ bsr: '', serial: '', date: today(), bank: '1120' });
  const [run, busy] = useAction();
  return <Modal title="TDS challan (Form 141)" onClose={onClose} footer={<><Btn onClick={onClose}>Cancel</Btn><Btn kind="pri" busy={busy}
    onClick={async () => { const r = await run(() => api('/fin/tds/pay', { body: { company, ids, ...f } }), (x: any) => (x.status === 'Posted' ? `Deposited (${x.no}).` : `${x.no} sent for approval.`)); if (r) onClose(); }}>Create payment voucher</Btn></>}>
    <p className="muted">{ids.length} deduction(s). Interest for late deduction (1 %/month) and late payment (1.5 %/month) is added automatically.</p>
    <div className="form grid2"><Field label="BSR code (7 digits)"><input value={f.bsr} onChange={e => setF({ ...f, bsr: e.target.value })} /></Field><Field label="Challan serial"><input value={f.serial} onChange={e => setF({ ...f, serial: e.target.value })} /></Field>
      <Field label="Date"><input type="date" value={f.date} onChange={e => setF({ ...f, date: e.target.value })} /></Field><Field label="Paid from"><input value={f.bank} onChange={e => setF({ ...f, bank: e.target.value })} /></Field></div>
  </Modal>;
}

function TdsReturns({ company, meta }: { company: string; meta: any }) {
  const fy = meta.fin_years.find((f: any) => f.status === 'Open')?.code || meta.fin_years[0]?.code;
  const [f, setF] = useState({ fy, quarter: 'Q1', form: '26Q' });
  const Q = useLoad<any>(() => api(`/fin/tds/quarter?company=${company}&fy=${f.fy}&quarter=${f.quarter}&form=${f.form}`), [company, f.fy, f.quarter, f.form]);
  const [token, setToken] = useState('');
  const [run, busy] = useAction();
  const parties = [...new Set((Q.data?.entries || []).map((e: any) => e.party))] as string[];
  return <Panel title={Q.data ? `${Q.data.form_name} · ${Q.data.form_label}` : 'Quarterly return'} actions={<>
    <select value={f.fy} onChange={e => setF({ ...f, fy: e.target.value })} aria-label="Year">{meta.fin_years.map((y: any) => <option key={y.code}>{y.code}</option>)}</select>
    <select value={f.quarter} onChange={e => setF({ ...f, quarter: e.target.value })} aria-label="Quarter">{['Q1', 'Q2', 'Q3', 'Q4'].map(q => <option key={q}>{q}</option>)}</select>
    <select value={f.form} onChange={e => setF({ ...f, form: e.target.value })} aria-label="Form">{Object.entries(meta.tds_forms).map(([k, v]: any) => <option key={k} value={k}>{v[0]} (old {k})</option>)}</select></>}>
    {Q.data && <>
      <Kv items={[['Period', `${day(Q.data.from)} – ${day(Q.data.to)}`], ['Due', day(Q.data.due)], ['Deductees · entries', `${Q.data.deductees} · ${Q.data.entries.length}`], ['TDS', inr(Q.data.total)],
        ['Not deposited', String(Q.data.undeposited)], ['Late fee (₹200/day)', inr(Q.data.fee_234e)], ['Status', Q.data.filed ? `Filed ${day(Q.data.filed.filed_on)} · token ${Q.data.filed.token}` : 'Not filed']]} />
      {!Q.data.filed && meta.can.submit && Q.data.entries.length > 0 && <div className="row"><input placeholder="15-digit token / receipt no." value={token} onChange={e => setToken(e.target.value)} aria-label="Token" />
        <Btn kind="pri" busy={busy} onClick={async () => { const r = await run(() => api('/fin/tds/file', { body: { company, ...f, token } }), () => 'Return filing recorded.'); if (r) Q.reload(); }}>Record filing</Btn></div>}
      {Q.data.filed && <><h4>Certificates ({f.form === '24Q' ? 'Form 130' : 'Form 131'})</h4><div className="chips">{parties.map(p => <Btn key={p} busy={busy}
        onClick={() => run(() => api('/fin/tds/certificate', { body: { company, ...f, party: p } }), (x: any) => `${x.form} ${x.cert_no} issued.`)}>{(Q.data.entries.find((e: any) => e.party === p) || {}).name}</Btn>)}</div></>}
    </>}
  </Panel>;
}

function TdsChecks({ company }: { company: string }) {
  const { data } = useLoad<any>(() => api(`/fin/tds/checks?company=${company}`), [company]);
  return <div className="grid2">
    <Panel title="PAN issues"><DataTable rows={data?.pan} keyOf={r => r.kind + r.id} empty="Every party has a valid PAN." cols={[{ key: 'name', label: 'Party', primary: true, render: r => <>{r.name} <span className="muted small">{r.kind}</span></> }, { key: 'pan', label: 'PAN' }, { key: 'issue', label: 'Issue' }]} /></Panel>
    <Panel title="Thresholds this year"><DataTable rows={data?.thresholds} keyOf={r => r.party + r.section} empty="No supplier profiles yet." cols={[{ key: 'name', label: 'Supplier', primary: true }, { key: 'section', label: 'Section' },
      { key: 'credited', label: 'Credited', align: 'r', render: r => inr(r.credited) }, { key: 'pct', label: 'Used', align: 'r', render: r => `${r.pct}%` }, { key: 'status', label: 'Status', render: r => <Pill s={r.status} tone={r.status.startsWith('Crossed · check') ? 'bad' : undefined} /> }]} /></Panel>
  </div>;
}

// ------------------------------------------------------------ reports (with drill-down)
function Reports({ company, meta }: { company: string; meta: any }) {
  const [sp, setSp] = useSearchParams();
  const fyStart = meta.fin_years.find((f: any) => f.status === 'Open')?.from_date?.slice(0, 10) || monthStart();
  const [key, setKey] = useState(sp.get('r') || 'tb');
  const [f, setF] = useState<Record<string, string>>({ from: sp.get('from') || fyStart, to: sp.get('to') || today() });
  const [stack, setStack] = useState<{ key: string; args: Record<string, string> }[]>([]);
  const nav = useNavigate();
  const args = stack.length ? stack[stack.length - 1].args : {};
  const cur = stack.length ? stack[stack.length - 1].key : key;
  const qs = new URLSearchParams({ company, ...f, ...args }).toString();
  const { data: r, err } = useLoad<any>(() => api(`/fin/reports/${cur}?${qs}`), [cur, qs]);
  const groups = useMemo(() => { const g: Record<string, any[]> = {}; for (const x of meta.reports) (g[x.group] = g[x.group] || []).push(x); return g; }, [meta.reports]);
  const drill = (d: any) => {
    if (!d) return;
    if (d.report) setStack(s => [...s, { key: d.report, args: Object.fromEntries(Object.entries(d.args || {}).map(([k, v]) => [k, String(v)])) }]);
    else if (d.voucher) api(`/fin/vouchers/by-no/${encodeURIComponent(d.voucher)}`).then((x: any) => (x.id ? nav(`/finance/vouchers/${x.id}?company=${company}`) : alertJournal(x.journal))).catch(() => undefined);
  };
  const [jv, setJv] = useState<any>(null);
  const alertJournal = (j: any) => setJv(j);
  return <div className="rep-layout">
    <aside className="rep-menu">{Object.entries(groups).map(([g, xs]) => <details key={g} open={g === 'Financial'}><summary>{g} ({xs.length})</summary>
      {xs.map(x => <button key={x.key} className={x.key === key && !stack.length ? 'on' : ''} onClick={() => { setKey(x.key); setStack([]); sp.set('r', x.key); setSp(sp, { replace: true }); }}>{x.title}</button>)}</details>)}</aside>
    <div className="rep-main">
      <div className="filters">
        <input type="date" value={f.from} onChange={e => setF({ ...f, from: e.target.value })} aria-label="From" /><input type="date" value={f.to} onChange={e => setF({ ...f, to: e.target.value })} aria-label="To" />
        <select value={f.branch || ''} onChange={e => setF({ ...f, branch: e.target.value })} aria-label="Location"><option value="">All locations</option>{meta.branches.map((b: any) => <option key={b.id} value={b.id}>{b.name}</option>)}</select>
        {['gl', 'bank'].includes(cur) && <input list="fin-accts-r" placeholder="Account" value={args.account || f.account || ''} onChange={e => setF({ ...f, account: e.target.value })} aria-label="Account" />}
        {cur === 'ageing' && <select value={f.kind || 'cust'} onChange={e => setF({ ...f, kind: e.target.value })} aria-label="Side"><option value="cust">Receivables</option><option value="sup">Payables</option></select>}
        {['g1', 'g3b', 'g2b', 'g2a', 'gmis'].includes(cur) && <input type="month" value={f.ym || ym()} onChange={e => setF({ ...f, ym: e.target.value })} aria-label="Month" />}
        <datalist id="fin-accts-r">{meta.accounts.map((a: any) => <option key={a.code} value={a.code}>{a.name}</option>)}</datalist>
      </div>
      {stack.length > 0 && <div className="crumbs"><Btn kind="link" onClick={() => setStack([])}>{meta.reports.find((x: any) => x.key === key)?.title}</Btn>
        {stack.map((s, i) => <span key={i}> › <Btn kind="link" onClick={() => setStack(st => st.slice(0, i + 1))}>{meta.reports.find((x: any) => x.key === s.key)?.title || s.key}{s.args.account ? ` ${s.args.account}` : ''}{s.args.party ? ` ${s.args.party}` : ''}</Btn></span>)}</div>}
      <ErrorNote err={err} />
      {r && <Panel title={r.title} actions={<>{r.balanced === false && <Pill s="Not balanced" tone="bad" />}{r.balanced && <Pill s="Balanced" tone="ok" />}
        <ReportActions url={`/fin/reports/${cur}?${qs}`} name={`${cur}-${company}-${f.to}`} r={r}
          formats={[...(meta.can.print ? ['print' as const] : []), ...(meta.can.files ? (['pdf', 'xlsx', 'csv'] as const) : [])]} /></>}>
        <ReportSheet r={{ ...r, company }}><ReportTable r={r} onDrill={drill} empty="No rows for this period." /></ReportSheet>
        <p className="muted small no-print">{r.rows.length} rows · click a row to open its ledger or voucher.</p>
      </Panel>}
    </div>
    {jv && <Modal title={`Journal ${jv.no}`} onClose={() => setJv(null)}><Kv items={[['Date', day(jv.txn_date)], ['Type', jv.type], ['Reference', jv.ref], ['Narration', jv.narration]]} />
      <DataTable rows={jv.lines} keyOf={l => l.line_no} cols={[{ key: 'account', label: 'Account', primary: true }, { key: 'branch_id', label: 'Location' }, { key: 'debit', label: 'Debit', align: 'r', render: l => (l.debit ? inr(l.debit) : '') }, { key: 'credit', label: 'Credit', align: 'r', render: l => (l.credit ? inr(l.credit) : '') }]} /></Modal>}
  </div>;
}

// ------------------------------------------------------------ bank
function Bank({ company, meta }: { company: string; meta: any }) {
  const [acct, setAcct] = useState(meta.banks[0]?.account || '1120');
  const [open, setOpen] = useState(true);
  const st = useLoad<any>(() => api(`/fin/bank/statement?company=${company}&account=${acct}${open ? '&open=1' : ''}`), [company, acct, open]);
  const [run, busy] = useAction();
  const file = useRef<HTMLInputElement>(null);
  const [match, setMatch] = useState<any>(null);
  const imp = async (demo: boolean) => {
    const f = file.current?.files?.[0];
    const r = await run(() => (demo ? api('/fin/bank/import', { body: { company, account: acct, demo: 1 } }) : (() => { const fd = new FormData(); fd.append('file', f!); fd.append('company', company); fd.append('account', acct); return api('/fin/bank/import', { form: fd }); })()),
      (x: any) => `${x.imported} line(s) imported · ${x.matched} matched automatically.`);
    if (r) st.reload();
    if (file.current) file.current.value = '';
  };
  const B = st.data?.brs;
  return <>
    <div className="filters"><select value={acct} onChange={e => setAcct(e.target.value)} aria-label="Bank account">{meta.banks.map((b: any) => <option key={b.id} value={b.account}>{b.account} · {b.name}</option>)}</select>
      <label className="check"><input type="checkbox" checked={open} onChange={e => setOpen(e.target.checked)} /> Unmatched only</label>
      <input ref={file} type="file" accept=".xlsx,.csv" hidden onChange={() => imp(false)} /><Btn busy={busy} onClick={() => file.current?.click()}>Import statement</Btn>
      <Btn busy={busy} onClick={() => imp(true)}>Bank feed (demo)</Btn><Btn busy={busy} onClick={async () => { const r = await run(() => api('/fin/bank/match', { body: { company, account: acct, auto: 1 } }), (x: any) => `${x.matched} matched.`); if (r) st.reload(); }}>Auto-match</Btn></div>
    {B && <div className="tiles small"><div className="tile"><span>Balance as per books</span><b>{money(B.book)}</b></div><div className="tile"><span>Cheques / payments not presented</span><b>{money(-B.issued.reduce((s: number, x: any) => s + x.amt, 0))}</b></div>
      <div className="tile"><span>Deposits not credited</span><b>{money(-B.not_credited.reduce((s: number, x: any) => s + x.amt, 0))}</b></div><div className="tile"><span>Bank-only entries</span><b>{money(B.bank_only.reduce((s: number, x: any) => s + x.amount, 0))}</b></div>
      <div className="tile"><span>Balance as per bank (derived)</span><b>{money(B.statement)}</b><small>last statement {B.last_statement?.balance != null ? inr(B.last_statement.balance) : '—'}</small></div></div>}
    <Panel title="Bank statement"><DataTable rows={st.data?.lines} keyOf={s => s.id} empty="No statement lines. Import the bank statement (Excel / CSV)." cols={[
      { key: 'sdate', label: 'Date', primary: true, render: s => day(s.sdate) }, { key: 'narration', label: 'Narration', render: s => <>{s.narration} <span className="muted small">{s.ref}</span></> },
      { key: 'amount', label: 'Amount', align: 'r', render: s => <span className={s.amount < 0 ? 'var-short' : 'var-excess'}>{inr(s.amount)}</span> },
      { key: 'how', label: 'Matched', render: s => (s.match_journal ? <><Pill s={s.how} tone="ok" /> <Btn kind="link" onClick={async () => { await run(() => api('/fin/bank/match', { body: { line: s.id, unmatch: 1 } }), () => 'Unmatched.'); st.reload(); }}>Undo</Btn></>
        : <><Btn kind="link" onClick={() => setMatch(s)}>Match</Btn> <Btn kind="link" busy={busy} onClick={async () => { const r = await run(() => api('/fin/bank/voucher', { body: { line: s.id } }), (x: any) => (x.status === 'Posted' ? `${x.no} posted and matched.` : `${x.no} sent for approval.`)); if (r) st.reload(); }}>Post as {s.amount < 0 ? 'charges' : 'income'}</Btn></>) }]} /></Panel>
    {match && <Modal title="Match with a book entry" onClose={() => setMatch(null)}><p>{day(match.sdate)} · {match.narration} · {inr(match.amount)}</p>
      <DataTable rows={(st.data?.book || []).filter((b: any) => Math.abs(b.amt - match.amount) < 1)} keyOf={b => b.jno + b.line_no} empty="No book entry with the same amount in the last 120 days." onRow={async b => { const r = await run(() => api('/fin/bank/match', { body: { line: match.id, journal: b.jno, journal_line: b.line_no } }), () => 'Matched.'); if (r) { setMatch(null); st.reload(); } }}
        cols={[{ key: 'txn_date', label: 'Date', primary: true, render: b => day(b.txn_date) }, { key: 'ref', label: 'Voucher', render: b => b.ref || b.jno }, { key: 'narration', label: 'Narration' }, { key: 'amt', label: 'Amount', align: 'r', render: b => inr(b.amt) }]} /></Modal>}
    <Cheques company={company} />
  </>;
}

function Cheques({ company }: { company: string }) {
  const c = useLoad<any[]>(() => api(`/fin/cheques?company=${company}`), [company]);
  const [dlg, setDlg] = useState<any>(null);
  const [why, setWhy] = useState('');
  const [run, busy] = useAction();
  const next: Record<string, string[]> = { Issued: ['Cleared', 'Stop payment'], Deposited: ['Cleared', 'Dishonoured'], Received: ['Deposited'] };
  return <Panel title="Cheques"><DataTable rows={c.data} keyOf={x => x.id} empty="No cheques recorded (enter the cheque no. on a payment or receipt voucher)." cols={[
    { key: 'cdate', label: 'Date', primary: true, render: x => day(x.cdate) }, { key: 'cheque_no', label: 'Cheque', render: x => <span className="mono">{x.cheque_no}</span> }, { key: 'kind', label: 'Kind' },
    { key: 'party', label: 'Party' }, { key: 'amount', label: 'Amount', align: 'r', render: x => inr(x.amount) }, { key: 'status', label: 'Status', render: x => <Pill s={x.status} tone={['Dishonoured', 'Stop payment'].includes(x.status) ? 'bad' : undefined} /> },
    { key: 'act', label: '', render: x => (next[x.status] || []).map(s => <Btn key={s} kind="link" onClick={() => setDlg({ x, s })}>{s}</Btn>) }]} />
    {dlg && <Modal title={`${dlg.s} · cheque ${dlg.x.cheque_no}`} onClose={() => setDlg(null)} footer={<><Btn onClick={() => setDlg(null)}>Back</Btn><Btn kind="pri" busy={busy}
      onClick={async () => { const r = await run(() => api(`/fin/cheques/${dlg.x.id}/status`, { body: { status: dlg.s, reason: why } }), () => `Cheque marked ${dlg.s.toLowerCase()}.`); if (r) { setDlg(null); c.reload(); } }}>Confirm</Btn></>}>
      {['Dishonoured', 'Stop payment'].includes(dlg.s) && <><p className="note">A reversal voucher is raised for approval and an alert is sent.</p><Field label="Reason *" wide><textarea rows={2} value={why} onChange={e => setWhy(e.target.value)} /></Field></>}
    </Modal>}
  </Panel>;
}

// ------------------------------------------------------------ payables & receivables
function Payables({ company, meta }: { company: string; meta: any }) {
  const [upto, setUpto] = useState(today());
  const p = useLoad<any>(() => api(`/fin/payables/proposal?company=${company}&upto=${upto}`), [company, upto]);
  const rec = useLoad<any[]>(() => api(`/fin/open-items?company=${company}&kind=cust`), [company]);
  const [pick, setPick] = useState<Record<string, any>>({});
  const [run, busy] = useAction();
  const total = Object.values(pick).reduce((s: number, x: any) => s + x.amount, 0);
  return <>
    <Panel title="Payment proposal" actions={<><label>Due up to <input type="date" value={upto} onChange={e => setUpto(e.target.value)} /></label>
      {meta.can.add && <Btn kind="pri" busy={busy} disabled={!Object.keys(pick).length} onClick={async () => { const r = await run(() => api('/fin/payables/proposal', { body: { company, picks: Object.values(pick) } }), (x: any) => `${x.length} payment voucher(s) created.`); if (r) { setPick({}); p.reload(); } }}>Create payments ({inr(total)})</Btn>}</>}>
      {p.data && <p className="muted small">Bank balance {inr(p.data.cash)}. Ordered by supplier priority, then due date; rows beyond the balance are flagged.</p>}
      <DataTable rows={p.data?.rows} keyOf={r => r.no + r.party} empty="Nothing due." rowClass={r => (r.fits ? '' : 'row-overdue')} cols={[
        { key: 'pick', label: '', render: r => <input type="checkbox" checked={!!pick[r.no]} onChange={() => setPick(s => { const n = { ...s }; if (n[r.no]) delete n[r.no]; else n[r.no] = { doc: r.no, party: r.party, amount: r.due }; return n; })} aria-label="Select" /> },
        { key: 'name', label: 'Supplier', primary: true, render: r => <>{r.name} {r.priority !== 'Normal' && <Pill s={r.priority} />}</> }, { key: 'no', label: 'Bill', render: r => <span className="mono">{r.no}</span> },
        { key: 'due_date', label: 'Due', render: r => <span className={r.overdue ? 'var-short' : ''}>{day(r.due_date || r.date)}</span> }, { key: 'due', label: 'Outstanding', align: 'r', render: r => inr(r.due) },
        { key: 'running', label: 'Running total', align: 'r', hideSmall: true, render: r => inr(r.running) }]} />
    </Panel>
    <Panel title="Receivables (open documents)"><DataTable rows={rec.data} keyOf={r => r.no} empty="No open receivables." cols={[{ key: 'name', label: 'Customer', primary: true }, { key: 'no', label: 'Document', render: r => <span className="mono">{r.no}</span> },
      { key: 'date', label: 'Date', render: r => day(r.date) }, { key: 'days', label: 'Days', align: 'r' }, { key: 'due', label: 'Outstanding', align: 'r', render: r => inr(r.due) }]} /></Panel>
  </>;
}

// ------------------------------------------------------------ fixed assets
function Assets({ company, meta }: { company: string; meta: any }) {
  const a = useLoad<any[]>(() => api(`/fin/assets?company=${company}`), [company]);
  const [dlg, setDlg] = useState<any>(null);
  const [run, busy] = useAction();
  return <>
    <div className="filters">{meta.can.add && <><Btn kind="pri" onClick={() => setDlg({ kind: 'new' })}>Capitalise asset</Btn>
      <Btn busy={busy} onClick={() => run(() => api('/fin/assets/depreciation', { body: { company, ym: prevYm() } }), (x: any) => (x.nil ? 'Nothing to depreciate.' : `Depreciation ${x.no} ${x.status === 'Posted' ? 'posted' : 'sent for approval'}.`)).then(() => a.reload())}>Depreciation for {prevYm()}</Btn></>}
      <span className="muted small">Depreciation also runs automatically after each month end (Companies Act, straight line, 5 % residual).</span></div>
    <Panel><DataTable rows={a.data} keyOf={x => x.id} empty="No fixed assets yet." cols={[
      { key: 'tag', label: 'Asset', primary: true, render: x => <>{x.name} <span className="muted small">{x.tag} · {x.category_name}</span></> }, { key: 'branch_id', label: 'Location', hideSmall: true },
      { key: 'put_to_use', label: 'Put to use', render: x => day(x.put_to_use) }, { key: 'cost', label: 'Cost', align: 'r', render: x => inr(x.cost) },
      { key: 'acc_dep', label: 'Depreciation', align: 'r', hideSmall: true, render: x => inr(x.acc_dep) }, { key: 'net_block', label: 'Net block', align: 'r', render: x => inr(x.net_block) },
      { key: 'status', label: 'Status', render: x => <Pill s={x.status} /> },
      { key: 'act', label: '', render: x => x.status === 'Active' && meta.can.edit ? <>{['verify', 'transfer', 'impair', 'dispose'].map(k => <Btn key={k} kind="link" onClick={() => setDlg({ kind: k, x })}>{k}</Btn>)}</> : '' }]} /></Panel>
    {dlg && <AssetDialog company={company} meta={meta} dlg={dlg} onClose={() => { setDlg(null); a.reload(); }} />}
  </>;
}

function AssetDialog({ company, meta, dlg, onClose }: { company: string; meta: any; dlg: any; onClose: () => void }) {
  const [f, setF] = useState<any>({ company, branch_id: meta.branches[0]?.id, category: meta.asset_categories[0]?.id, name: '', cost: '', date: today(), gst_rate: '18', supplier: '', bill: '', found: true, condition: 'Good', to: '', amount: '', sale: '', reason: '', why: '' });
  const [run, busy] = useAction();
  const set = (k: string) => (e: any) => setF({ ...f, [k]: e.type === 'change' && e.target.type === 'checkbox' ? e.target.checked : e.target.value });
  const go = async () => {
    const x = dlg.x;
    const r = dlg.kind === 'new' ? await run(() => api('/fin/assets', { body: { ...f, cost: Number(f.cost), supplier: f.supplier || undefined } }), (y: any) => `${y.asset.tag} created; ${y.voucher.no} ${y.voucher.status === 'Posted' ? 'posted' : 'sent for approval — attach the bill'}.`)
      : await run(() => api(`/fin/assets/${x.id}/${dlg.kind}`, { body: { ...f, why: f.reason, amount: Number(f.amount) || undefined, sale: Number(f.sale) || 0 } }), () => 'Done.');
    if (r) onClose();
  };
  return <Modal title={dlg.kind === 'new' ? 'Capitalise a fixed asset' : `${dlg.kind[0].toUpperCase() + dlg.kind.slice(1)} · ${dlg.x.name}`} onClose={onClose} footer={<><Btn onClick={onClose}>Cancel</Btn><Btn kind="pri" busy={busy} onClick={go}>Save</Btn></>}>
    <div className="form grid2">
      {dlg.kind === 'new' && <><Field label="Asset name *" wide><input value={f.name} onChange={set('name')} /></Field>
        <Field label="Category"><select value={f.category} onChange={set('category')}>{meta.asset_categories.map((c: any) => <option key={c.id} value={c.id}>{c.name} · {c.life_years} yrs</option>)}</select></Field>
        <Field label="Location"><select value={f.branch_id} onChange={set('branch_id')}>{meta.branches.map((b: any) => <option key={b.id} value={b.id}>{b.name}</option>)}</select></Field>
        <Field label="Cost (before GST) *"><input inputMode="decimal" value={f.cost} onChange={set('cost')} /></Field><Field label="GST rate"><select value={f.gst_rate} onChange={set('gst_rate')}>{['0', '5', '12', '18', '28'].map(x => <option key={x} value={x}>{x}%</option>)}</select></Field>
        <Field label="Put to use on"><input type="date" value={f.date} onChange={set('date')} /></Field>
        <Field label="Supplier (credit purchase)"><select value={f.supplier} onChange={set('supplier')}><option value="">Paid from bank</option>{meta.suppliers.map((s: any) => <option key={s.id} value={s.id}>{s.name}</option>)}</select></Field>
        <Field label="Bill no."><input value={f.bill} onChange={set('bill')} /></Field></>}
      {dlg.kind === 'verify' && <><label className="check"><input type="checkbox" checked={f.found} onChange={set('found')} /> Found at {dlg.x.branch_id}</label><Field label="Condition"><input value={f.condition} onChange={set('condition')} /></Field></>}
      {dlg.kind === 'transfer' && <Field label="Move to"><select value={f.to} onChange={set('to')}><option value="">Choose…</option>{meta.branches.filter((b: any) => b.id !== dlg.x.branch_id).map((b: any) => <option key={b.id} value={b.id}>{b.name}</option>)}</select></Field>}
      {dlg.kind === 'impair' && <Field label="Impairment ₹"><input inputMode="decimal" value={f.amount} onChange={set('amount')} /></Field>}
      {dlg.kind === 'dispose' && <><Field label="Sale proceeds ₹"><input inputMode="decimal" value={f.sale} onChange={set('sale')} /></Field><Field label="Date"><input type="date" value={f.date} onChange={set('date')} /></Field></>}
      {dlg.kind !== 'new' && <Field label={dlg.kind === 'verify' ? 'Note' : 'Reason *'} wide><textarea rows={2} value={f.reason} onChange={e => setF({ ...f, reason: e.target.value, note: e.target.value })} /></Field>}
    </div>
  </Modal>;
}

// ------------------------------------------------------------ compliance calendar
function Compliance({ company }: { company: string }) {
  const c = useLoad<any[]>(() => api(`/fin/compliance?company=${company}`), [company]);
  const [dlg, setDlg] = useState<any>(null);
  const [f, setF] = useState({ status: 'Filed', ref: '', note: '' });
  const [show, setShow] = useState<'open' | 'all'>('open');
  const [run, busy] = useAction();
  const rows = (c.data || []).filter(x => show === 'all' || !['Filed', 'Paid', 'Done', 'Nil', 'Not applicable'].includes(x.status));
  return <Panel title="Compliance calendar" actions={<select value={show} onChange={e => setShow(e.target.value as any)} aria-label="Show"><option value="open">Open items</option><option value="all">All</option></select>}>
    <p className="muted small">GST, TDS and payment items close themselves from the books (filing ARNs, challans). Others are marked here with the acknowledgement number; overdue items alert daily and escalate.</p>
    <DataTable rows={rows} keyOf={x => x.key} empty="Nothing open." rowClass={x => (x.late ? 'row-overdue' : x.soon ? 'row-soon' : '')} cols={[
      { key: 'label', label: 'Compliance', primary: true, render: x => <>{x.label} <span className="muted small">{x.area}</span></> }, { key: 'period', label: 'Period' },
      { key: 'due', label: 'Due', render: x => <span className={x.late ? 'var-short' : ''}>{day(x.due)}</span> }, { key: 'status', label: 'Status', render: x => <><Pill s={x.status} tone={x.late ? 'bad' : x.soon ? 'warn' : undefined} />{x.auto && <span className="muted small"> auto</span>}</> },
      { key: 'ref', label: 'Reference', hideSmall: true }, { key: 'act', label: '', render: x => (!x.auto ? <Btn kind="link" onClick={() => { setDlg(x); setF({ status: 'Filed', ref: x.ref || '', note: x.note || '' }); }}>Update</Btn> : '') }]} />
    {dlg && <Modal title={`${dlg.label} · ${dlg.period}`} onClose={() => setDlg(null)} footer={<><Btn onClick={() => setDlg(null)}>Cancel</Btn><Btn kind="pri" busy={busy}
      onClick={async () => { const r = await run(() => api('/fin/compliance/mark', { body: { key: dlg.key, ...f } }), () => 'Updated.'); if (r) { setDlg(null); c.reload(); } }}>Save</Btn></>}>
      <div className="form grid2"><Field label="Status"><select value={f.status} onChange={e => setF({ ...f, status: e.target.value })}>{['Pending', 'In progress', 'Filed', 'Paid', 'Done', 'Nil', 'Not applicable'].map(s => <option key={s}>{s}</option>)}</select></Field>
        <Field label="Acknowledgement / challan / reference"><input value={f.ref} onChange={e => setF({ ...f, ref: e.target.value })} /></Field><Field label="Note" wide><input value={f.note} onChange={e => setF({ ...f, note: e.target.value })} /></Field></div>
      <EvidencePanel refNo={'CMP:' + dlg.key} company={company} />
    </Modal>}
  </Panel>;
}

// ------------------------------------------------------------ setup: periods, masters, controls
const MASTERS: [string, string, string[]][] = [
  ['cost_centres', 'Cost centres & departments', ['id', 'name', 'kind', 'active']], ['tds_sections', 'TDS sections', ['code', 'label', 'rate_ind', 'rate_other', 'single_limit', 'year_limit', 'form', 'new_ref', 'active']],
  ['party_tax', 'Party tax profiles (PAN, section, LDC)', ['party', 'company', 'pan', 'section', 'resident', 'priority', 'related']], ['employees', 'Employees (payroll)', ['id', 'company', 'name', 'branch_id', 'pan', 'gross', 'pf', 'esi', 'joined', 'active']],
  ['hsn_codes', 'HSN / SAC codes', ['code', 'kind', 'description', 'rate', 'uqc']], ['bank_accounts', 'Bank accounts', ['id', 'company', 'account', 'name', 'ifsc', 'branch_id']],
  ['asset_categories', 'Asset categories', ['id', 'name', 'account', 'life_years', 'it_rate']], ['approval_rules', 'Approval rules', ['id', 'vtype', 'account', 'branch_id', 'above', 'role']],
  ['budgets', 'Budgets', ['company', 'fy', 'account', 'amount']], ['gst_registrations', 'GST registrations', ['id', 'company', 'state', 'gstin', 'kind', 'einvoice']],
  ['recurring', 'Recurring journals', ['id', 'company', 'name', 'vtype', 'day', 'next_date', 'end_date', 'narration', 'active']]];

function Setup({ company, meta, reload }: { company: string; meta: any; reload: () => void }) {
  const [m, setM] = useState('cost_centres');
  const [def] = MASTERS.filter(x => x[0] === m);
  const rows = useLoad<any[]>(() => api(`/fin/masters/${m}?company=${company}`), [m, company]);
  const [edit, setEdit] = useState<any>(null);
  const [why, setWhy] = useState('');
  const [run, busy] = useAction();
  const [per, setPer] = useState({ date: '', reason: '', fy: '' });
  const company_ = meta.companies.find((c: any) => c.code === company);
  return <>
    <Panel title="Periods" actions={meta.can.close && <>
      <input type="date" value={per.date} onChange={e => setPer({ ...per, date: e.target.value })} aria-label="Date" /><input placeholder="Reason" value={per.reason} onChange={e => setPer({ ...per, reason: e.target.value })} aria-label="Reason" />
      <Btn busy={busy} onClick={() => run(() => api('/fin/periods/close', { body: { company, ...per } }), () => 'Books closed.').then(reload)}>Close books to date</Btn>
      {meta.can.restore && <Btn kind="danger" busy={busy} onClick={() => run(() => api('/fin/periods/reopen', { body: { company, ...per } }), () => 'Period reopened.').then(reload)}>Reopen from date</Btn>}</>}>
      <Kv items={[['Books closed up to', company_?.books_locked_to ? day(company_.books_locked_to) : 'Open'], ...meta.fin_years.map((y: any) => [`FY ${y.code}`, `${day(y.from_date)} – ${day(y.to_date)} · ${y.status}`] as [string, string])]} />
      {meta.fin_years.some((y: any) => y.status === 'Open' && y.to_date < today()) && <p className="note info">A financial year has ended. Close it on the <a href={`?tab=ye&company=${company}`}>Year-end</a> tab: it checks what is pending, moves the profit to capital and locks the year.</p>}
      <p className="muted small">Closing and reopening are for the CFO / Director only and are recorded in the audit trail with the reason. Entries into a closed or filed period need their approval.</p>
    </Panel>
    <Panel title="Masters" actions={<><select value={m} onChange={e => setM(e.target.value)} aria-label="Master">{MASTERS.map(x => <option key={x[0]} value={x[0]}>{x[1]}</option>)}</select>{meta.can.master && <Btn kind="pri" onClick={() => setEdit({ company })}>Add</Btn>}</>}>
      <DataTable rows={(rows.data || []).map((r, i) => ({ ...r, __k: i }))} keyOf={r => r.__k} onRow={meta.can.master ? r => setEdit(r) : undefined} empty="No rows." cols={def[2].map((k, i) => ({ key: k, label: k.replace(/_/g, ' '), primary: i === 0,
        render: (r: any) => (typeof r[k] === 'boolean' ? (r[k] ? 'Yes' : 'No') : r[k] && typeof r[k] === 'object' ? JSON.stringify(r[k]) : String(r[k] ?? '')) }))} />
    </Panel>
    <Controls meta={meta} reload={reload} />
    {edit && <Modal title={`${edit.__k !== undefined ? 'Change' : 'Add'} · ${def[1]}`} onClose={() => setEdit(null)} footer={<><Btn onClick={() => setEdit(null)}>Cancel</Btn><Btn kind="pri" busy={busy}
      onClick={async () => { const { __k, ...row } = edit; const r = await run(() => api(`/fin/masters/${m}`, { method: 'PUT', body: { row, reason: why } }), () => 'Saved.'); if (r) { setEdit(null); rows.reload(); reload(); } }}>Save</Btn></>}>
      <div className="form grid2">{def[2].map(k => <Field key={k} label={k.replace(/_/g, ' ')}><input value={edit[k] == null ? '' : typeof edit[k] === 'object' ? JSON.stringify(edit[k]) : String(edit[k])} onChange={e => setEdit({ ...edit, [k]: e.target.value })} /></Field>)}
        <Field label="Reason for the change" wide><input value={why} onChange={e => setWhy(e.target.value)} /></Field></div>
    </Modal>}
  </>;
}

function Controls({ meta, reload }: { meta: any; reload: () => void }) {
  const [s, setS] = useState<any>(meta.settings);
  const [run, busy] = useAction();
  const n = (o: any, k: string, label: string, path: string[]) => <Field key={path.join('.')} label={label}><input inputMode="decimal" value={o[k]} onChange={e => {
    const v = e.target.value; setS((x: any) => { const y = JSON.parse(JSON.stringify(x)); let t = y; for (const p of path.slice(0, -1)) t = t[p]; t[path[path.length - 1]] = v === '' ? '' : Number(v); return y; }); }} /></Field>;
  return <Panel title="Controls" actions={meta.can.master && <Btn kind="pri" busy={busy} onClick={() => run(() => api('/fin/settings', { method: 'PUT', body: { value: s, reason: 'Finance controls updated' } }), () => 'Saved.').then(reload)}>Save controls</Btn>}>
    <div className="form grid3">
      {n(s.gst, 'interest', 'GST interest % p.a.', ['gst', 'interest'])}{n(s.gst, 'fee', 'GST late fee ₹/day', ['gst', 'fee'])}{n(s.gst, 'b2cl_limit', 'B2C large limit ₹', ['gst', 'b2cl_limit'])}
      {n(s.tds, 'late_deduction', 'TDS late deduction %/month', ['tds', 'late_deduction'])}{n(s.tds, 'late_payment', 'TDS late payment %/month', ['tds', 'late_payment'])}{n(s.tds, 'no_pan_rate', 'No-PAN rate %', ['tds', 'no_pan_rate'])}
      {n(s, 'dup_days', 'Duplicate bill window (days)', ['dup_days'])}{n(s, 'three_way_tolerance', 'PO / GRN / invoice tolerance %', ['three_way_tolerance'])}
      {Object.keys(s.attach).map(k => n(s.attach, k, `Attach bill: ${k} from ₹`, ['attach', k]))}
    </div>
    <p className="muted small">Always checked by a second person: {s.always_approve.join(', ')}. Changes are written to the audit trail.</p>
  </Panel>;
}

