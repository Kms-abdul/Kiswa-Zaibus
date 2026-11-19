/* Stock audits: plan an audit, count stock on a phone (type, scan with a barcode scanner or the camera, or upload a count
   sheet), submit, verify, review, then the CFO / Director decides on the impact review (approve all, approve some items, return
   for recount, reject). Approved items post the stock adjustment and journal automatically (release 2.3). Dashboard and reports.
   Business rules are enforced by the API (services/stock_audit.py); this screen only shows what the user may do. */
import { useEffect, useMemo, useRef, useState } from 'react';
import { Route, Routes, useNavigate, useParams } from 'react-router-dom';
import { api, download, errText } from '../api';
import { useAuth } from '../auth';
import { co, day, inr, monthStart, today, when } from '../format';
import { Btn, ConfirmBtn, DataTable, ErrorNote, EvidencePanel, Field, ItemSearch, Kv, Modal, Page, Panel, Pill, Tabs, useAction, useLoad, useToast, useUrlState } from '../ui/kit';
import { fromObjects, ReportActions, ReportSheet, ReportTable, useFormats } from '../ui/report';

const STATUSES = ['Draft', 'In Progress', 'Submitted', 'Under Review', 'Approved', 'Rejected', 'Closed', 'Cancelled'];
const money = (v: any) => (v == null ? '—' : inr(v));
const tone = (v: number | null | undefined) => (v == null || v === 0 ? '' : v < 0 ? 'var-short' : 'var-excess');

export default function StockAudits() {
  return <Routes><Route index element={<Home />} /><Route path=":id" element={<AuditPage />} /><Route path=":id/impact" element={<ImpactPage />} /></Routes>;
}

// ------------------------------------------------------------ home: dashboard | audits | reports
function Home() {
  const { can } = useAuth();
  const [tab, setTab] = useUrlState<'audits' | 'dash' | 'reports'>('tab', 'audits');
  const [create, setCreate] = useState(false);
  const nav = useNavigate();
  return (
    <Page title="Stock audits" sub="Physical count against system stock, by date and location. Counting never changes stock; the CFO / Director approves, and approved items post automatically."
      actions={can('add', 'saudit') && <Btn kind="pri" onClick={() => setCreate(true)}>New stock audit</Btn>}>
      <Tabs value={tab} onChange={setTab} tabs={[['audits', 'Audits'], ['dash', 'Dashboard'], ['reports', 'Reports']]} />
      {tab === 'audits' && <AuditList />}
      {tab === 'dash' && <Dashboard />}
      {tab === 'reports' && <Reports />}
      {create && <NewAudit onClose={id => { setCreate(false); if (id) nav(`/stock-audits/${id}`); }} />}
    </Page>
  );
}

function useMeta() { return useLoad<any>(() => api('/stock-audits/meta'), []); }

function AuditList() {
  const { org } = useAuth();
  const meta = useMeta();
  const nav = useNavigate();
  const [f, setF] = useState<any>({ from: '', to: '', company: '', location_type: '', branch: '', status: '', auditor: '', flag: '', min_value: '', max_value: '', q: '', item: '', category: '', mine: '' });
  const qs = new URLSearchParams(Object.entries(f).filter(([, v]) => v) as [string, string][]).toString();
  const l = useLoad<any[]>(() => api(`/stock-audits?${qs}`), [qs]);
  const [more, setMore] = useState(false);
  const set = (k: string) => (e: any) => setF({ ...f, [k]: e.target.value });
  const brs = (meta.data?.branches || []).filter((b: any) => (!f.company || b.company === f.company) && (!f.location_type || b.location_type === f.location_type));
  return <>
    <div className="filters">
      <input placeholder="Audit no." value={f.q} onChange={set('q')} aria-label="Audit number" />
      <select value={f.status} onChange={set('status')} aria-label="Status"><option value="">All statuses</option>{STATUSES.map(s => <option key={s}>{s}</option>)}</select>
      <select value={f.location_type} onChange={set('location_type')} aria-label="Location type"><option value="">All location types</option>{(meta.data?.location_types || []).map((t: string) => <option key={t}>{t}</option>)}</select>
      <select value={f.branch} onChange={set('branch')} aria-label="Location"><option value="">All locations</option>{brs.map((b: any) => <option key={b.id} value={b.id}>{b.name} · {b.company}</option>)}</select>
      <label className="check"><input type="checkbox" checked={!!f.mine} onChange={e => setF({ ...f, mine: e.target.checked ? '1' : '' })} />Assigned to me</label>
      <Btn kind="link" onClick={() => setMore(!more)}>{more ? 'Fewer filters' : 'More filters'}</Btn>
    </div>
    {more && <div className="filters">
      <input type="date" value={f.from} onChange={set('from')} aria-label="From date" /><input type="date" value={f.to} onChange={set('to')} aria-label="To date" />
      <select value={f.company} onChange={set('company')} aria-label="Company"><option value="">All companies</option>{(org?.companies || []).map(c => <option key={c.code} value={c.code}>{c.name}</option>)}</select>
      <select value={f.auditor} onChange={set('auditor')} aria-label="Auditor"><option value="">Any auditor</option>{(meta.data?.people || []).filter((p: any) => p.count).map((p: any) => <option key={p.id} value={p.id}>{p.name}</option>)}</select>
      <select value={f.flag} onChange={set('flag')} aria-label="Variance"><option value="">Any variance</option><option value="shortage">With shortage</option><option value="excess">With excess</option><option value="damaged">With damaged / expired</option></select>
      <input inputMode="decimal" placeholder="Variance ₹ from" value={f.min_value} onChange={set('min_value')} aria-label="Variance value from" />
      <input inputMode="decimal" placeholder="Variance ₹ to" value={f.max_value} onChange={set('max_value')} aria-label="Variance value to" />
      <input placeholder="Item code" value={f.item} onChange={set('item')} aria-label="Item code" /><input placeholder="Category" value={f.category} onChange={set('category')} aria-label="Category" />
    </div>}
    <Panel actions={<><Btn onClick={() => download(`/stock-audits?${qs}&format=xlsx`, 'stock-audits.xlsx')}>Excel</Btn></>}>
      <DataTable rows={l.data} onRow={r => nav(`/stock-audits/${r.id}`)} rowClass={r => (r.overdue ? 'row-overdue' : '')} empty="No stock audits match." cols={[
        { key: 'no', label: 'Audit', primary: true, render: r => <span className="mono">{r.no}</span> },
        { key: 'audit_date', label: 'Date', render: r => day(r.audit_date) }, { key: 'location_name', label: 'Location', render: r => <>{r.location_name} <span className="muted">{r.location_type}</span></> },
        { key: 'type_label', label: 'Type', hideSmall: true }, { key: 'auditor_names', label: 'Auditors', hideSmall: true, render: r => r.auditor_names.join(', ') },
        { key: 'counted', label: 'Counted', align: 'r', render: r => `${r.counted_count}/${r.lines_count}` },
        { key: 'shortage_value', label: 'Shortage', align: 'r', render: r => <span className={r.shortage_value ? 'var-short' : ''}>{money(r.shortage_value)}</span> },
        { key: 'excess_value', label: 'Excess', align: 'r', hideSmall: true, render: r => <span className={r.excess_value ? 'var-excess' : ''}>{money(r.excess_value)}</span> },
        { key: 'status', label: 'Status', render: r => <>{<Pill s={r.status} />}{r.overdue && <Pill s="Overdue" tone="bad" />}</> },
      ]} />
    </Panel>
  </>;
}

// ------------------------------------------------------------ create
function NewAudit({ onClose }: { onClose: (id?: number) => void }) {
  const { data: meta } = useMeta();
  const toast = useToast();
  const [f, setF] = useState<any>({ audit_date: today(), audit_type: 'Full', location_type: '', branch_id: '', stock_area: '', bin_prefix: '', categories: [], items: [], spot_size: 20, auditors: [], supervisor_id: '', due_date: '', instructions: '' });
  const [run, busy] = useAction();
  const br = meta?.branches.find((b: any) => b.id === f.branch_id);
  const cats = useLoad<any[]>(() => (br ? api(`/categories?company=${br.company}`) : Promise.resolve([])), [br?.company]);
  const [itemNames, setItemNames] = useState<Record<string, string>>({});
  if (!meta) return null;
  const inBranch = (p: any) => p.branches === '*' || p.branches.includes(f.branch_id);
  const counters = meta.people.filter((p: any) => p.count && (!f.branch_id || inBranch(p)));
  const sups = meta.people.filter((p: any) => p.verify && !f.auditors.includes(p.id) && (!f.branch_id || inBranch(p)));
  const toggle = (k: string, v: string) => setF({ ...f, [k]: f[k].includes(v) ? f[k].filter((x: string) => x !== v) : [...f[k], v] });
  const save = async () => {
    if (!f.branch_id) { toast('Choose the location.', 'bad'); return; }
    const r: any = await run(() => api('/stock-audits', { body: { ...f, spot_size: Number(f.spot_size) || undefined, due_date: f.due_date || undefined } }),
      (x: any) => `Audit ${x.no} created with ${x.lines_count} items. The auditors have been notified.`);
    if (r) onClose(r.id);
  };
  return <Modal wide title="New stock audit" onClose={() => onClose()} footer={<><Btn onClick={() => onClose()}>Cancel</Btn><Btn kind="pri" busy={busy} onClick={save}>Create audit</Btn></>}>
    <div className="form grid2">
      <Field label="Audit reference"><input value="Generated on save (e.g. KG/SA/2627/0001)" readOnly /></Field>
      <Field label="Audit date *"><input type="date" value={f.audit_date} onChange={e => setF({ ...f, audit_date: e.target.value })} /></Field>
      <Field label="Audit type *"><select value={f.audit_type} onChange={e => setF({ ...f, audit_type: e.target.value })}>{Object.entries(meta.types).map(([k, l]) => <option key={k} value={k}>{l as string}</option>)}</select></Field>
      <Field label="Location type *"><select value={f.location_type} onChange={e => setF({ ...f, location_type: e.target.value, branch_id: '' })}><option value="">All types</option>{meta.location_types.map((t: string) => <option key={t}>{t}</option>)}</select></Field>
      <Field label="Location and company *" wide><select value={f.branch_id} onChange={e => setF({ ...f, branch_id: e.target.value, auditors: [], supervisor_id: '' })}><option value="">Choose…</option>
        {meta.branches.filter((b: any) => !f.location_type || b.location_type === f.location_type).map((b: any) => <option key={b.id} value={b.id}>{b.name} ({b.location_type}) · {co(b.company)}</option>)}</select></Field>
      <Field label="Department / stock area"><input value={f.stock_area} onChange={e => setF({ ...f, stock_area: e.target.value })} placeholder="e.g. Rack A – uniforms" /></Field>
      <Field label="Only bins starting with" hint="Optional: limits the items to a rack / bin"><input value={f.bin_prefix} onChange={e => setF({ ...f, bin_prefix: e.target.value })} placeholder="e.g. A-" /></Field>
      {f.audit_type === 'Spot' && <Field label="Random items to check"><input inputMode="numeric" value={f.spot_size} onChange={e => setF({ ...f, spot_size: e.target.value })} /></Field>}
      <Field label="Due by" hint={`Default ${meta.settings.due_days} days after the audit date`}><input type="date" value={f.due_date} onChange={e => setF({ ...f, due_date: e.target.value })} /></Field>
    </div>
    {f.audit_type === 'Category' && <><h4>Categories *</h4><div className="chips">{(cats.data || []).map(c => <label key={c.name} className="chip"><input type="checkbox" checked={f.categories.includes(c.name)} onChange={() => toggle('categories', c.name)} />{c.name}</label>)}</div></>}
    {(f.audit_type === 'Item' || f.audit_type === 'Spot') && <>
      <h4>{f.audit_type === 'Spot' ? 'Items (leave empty for a random sample)' : 'Items *'}</h4>
      {br ? <ItemSearch company={br.company} onPick={it => { setItemNames(n => ({ ...n, [it.id]: `${it.name} ${it.size || ''}` })); setF((x: any) => ({ ...x, items: x.items.includes(it.id) ? x.items : [...x.items, it.id] })); }} /> : <p className="muted">Choose the location first.</p>}
      <div className="chips">{f.items.map((i: string) => <span key={i} className="chip">{itemNames[i] || i}<button className="x" onClick={() => toggle('items', i)} aria-label="Remove">×</button></span>)}</div>
    </>}
    <h4>Auditors * (who count)</h4>
    <div className="chips">{counters.map((p: any) => <label key={p.id} className="chip"><input type="checkbox" checked={f.auditors.includes(p.id)} onChange={() => setF({ ...f, auditors: f.auditors.includes(p.id) ? f.auditors.filter((x: string) => x !== p.id) : [...f.auditors, p.id], supervisor_id: f.supervisor_id === p.id ? '' : f.supervisor_id })} />{p.name} <span className="muted">{p.role}</span></label>)}
      {!counters.length && <p className="muted">{f.branch_id ? 'Nobody with the stock-count permission works at this location.' : 'Choose the location first.'}</p>}</div>
    <div className="form grid2">
      <Field label="Audit supervisor / verifier *" hint="Location in-charge who verifies the count; cannot be an auditor"><select value={f.supervisor_id} onChange={e => setF({ ...f, supervisor_id: e.target.value })}><option value="">Choose…</option>
        {sups.map((p: any) => <option key={p.id} value={p.id}>{p.name} · {p.role}</option>)}</select></Field>
      <Field label="Remarks / audit instructions" wide><textarea rows={3} value={f.instructions} onChange={e => setF({ ...f, instructions: e.target.value })} placeholder="e.g. Count sealed cartons by label, open loose stock. Note damaged pieces separately." /></Field>
    </div>
  </Modal>;
}

// ------------------------------------------------------------ one audit
function AuditPage() {
  const { id = '' } = useParams();
  const nav = useNavigate();
  const toast = useToast();
  const { data: a, setData, reload, err } = useLoad<any>(() => api(`/stock-audits/${id}`), [id]);
  const [edits, setEdits] = useState<Record<number, any>>({});
  const [filter, setFilter] = useState<'all' | 'todo' | 'short' | 'excess' | 'ok'>('all');
  const [q, setQ] = useState('');
  const [ev, setEv] = useState<any>(null);
  const [act, setAct] = useState<'' | 'verify' | 'review' | 'reopen' | 'cancel'>('');
  const [addAud, setAddAud] = useState(false);
  const [run, busy] = useAction();
  const dirty = Object.keys(edits).length;
  useEffect(() => {   // warn before leaving with unsaved counts
    const h = (e: BeforeUnloadEvent) => { if (dirty) { e.preventDefault(); e.returnValue = ''; } };
    window.addEventListener('beforeunload', h); return () => window.removeEventListener('beforeunload', h);
  }, [dirty]);
  const lines = useMemo(() => (a?.lines || []).filter((l: any) => {
    if (q && !`${l.item_id} ${l.name} ${l.barcode || ''} ${l.bin || ''}`.toLowerCase().includes(q.toLowerCase())) return false;
    return filter === 'all' || (filter === 'todo' && l.physical_qty == null) || (filter === 'short' && l.status === 'Shortage') || (filter === 'excess' && l.status === 'Excess') || (filter === 'ok' && l.status === 'Matched');
  }), [a, q, filter]);
  if (err) return <Page title="Stock audit"><ErrorNote err={err} /></Page>;
  if (!a) return <Page title="Stock audit"><p className="muted">Loading…</p></Page>;
  const c = a.can;
  const upd = (ln: number, p: any) => setEdits(e => ({ ...e, [ln]: { ...(e[ln] || {}), ...p } }));
  const saveCounts = async () => {
    const rows = Object.entries(edits).map(([ln, v]) => ({ line_no: Number(ln), ...v }));
    const r = await run(() => api(`/stock-audits/${a.id}/counts`, { body: { rows } }), () => `Saved ${rows.length} line(s). You can continue later.`);
    if (r) { setEdits({}); setData(r); }
  };
  const post = async (path: string, body: any = {}, msg?: string) => { const r: any = await run(() => api(`/stock-audits/${a.id}/${path}`, { body }), () => msg || ''); if (r) { setData(r.lines ? r : await api(`/stock-audits/${a.id}`)); setAct(''); } return r; };
  const val = (k: string) => (a.show_cost ? money(a[k]) : '—');
  return (
    <Page title={`Stock audit ${a.no}`}
      sub={<><Pill s={a.status} />{a.overdue && <Pill s="Overdue" tone="bad" />}{a.escalated && <Pill s="Escalated" tone="bad" />} {a.type_label} · {a.location_name} ({a.location_type}) · {a.company_name}{a.stock_area ? ` · ${a.stock_area}` : ''} · {day(a.audit_date)}, due {day(a.due_date)}</>}
      actions={<>
        <Btn onClick={() => nav('/stock-audits')}>‹ All audits</Btn>
        <Btn onClick={() => download(`/stock-audits/${a.id}/export?format=pdf`, `${a.no.replace(/\//g, '-')}.pdf`)}>PDF</Btn>
        <Btn onClick={() => download(`/stock-audits/${a.id}/export?format=xlsx`, `${a.no.replace(/\//g, '-')}.xlsx`)}>Excel</Btn>
      </>}>
      <div className="tiles small">
        <div className="tile"><span>Counted</span><b>{a.counted_count} / {a.lines_count}</b></div>
        <div className="tile"><span>Matched · accuracy</span><b>{a.matched_count} · {a.accuracy_pct ?? '—'}%</b></div>
        <div className="tile"><span>System value</span><b>{val('system_value')}</b></div>
        <div className="tile"><span>Physical value</span><b>{val('physical_value')}</b></div>
        <div className="tile"><span>Shortage</span><b className="var-short">{val('shortage_value')}</b></div>
        <div className="tile"><span>Excess</span><b className="var-excess">{val('excess_value')}</b></div>
        <div className="tile"><span>Damaged / expired</span><b>{val('damaged_value')}</b></div>
      </div>
      <div className="actions sa-actions">
        {c.count && a.status === 'Draft' && <Btn kind="pri" busy={busy} onClick={() => post('start', {}, 'Count started.')}>Start count</Btn>}
        {c.count && <><Btn onClick={() => download(`/stock-audits/${a.id}/count-sheet`, `${a.no.replace(/\//g, '-')}-count-sheet.xlsx`)}>Count sheet</Btn><SheetUpload id={a.id} onDone={setData} /></>}
        {c.submit && <ConfirmBtn label="Submit for verification" busy={busy} onConfirm={async () => { if (dirty) { toast('Save your counts first.', 'bad'); return; } await post('submit', {}, 'Submitted. The supervisor has been notified.'); }} />}
        {c.verify && <Btn kind="pri" onClick={() => setAct('verify')}>Verify count</Btn>}
        {c.review && <Btn kind="pri" onClick={() => setAct('review')}>Review variance</Btn>}
        {a.status === 'Under Review' && a.approval_no && <Btn kind={c.decide ? 'pri' : 'ghost'} onClick={() => nav(`/stock-audits/${a.id}/impact`)}>{c.decide ? 'Impact review & decision' : 'Impact review'}</Btn>}
        {c.post && <ConfirmBtn label="Post stock adjustment (approved before 2.3)" busy={busy} onConfirm={() => post('post', {}, 'Adjustment posted; audit closed.')} />}
        {c.add_auditor && <Btn onClick={() => setAddAud(true)}>+ Auditor</Btn>}
        {c.reopen && <Btn onClick={() => setAct('reopen')}>Reopen</Btn>}
        {c.cancel && <Btn kind="danger" onClick={() => setAct('cancel')}>Cancel audit</Btn>}
      </div>
      <NextStep a={a} onImpact={() => nav(`/stock-audits/${a.id}/impact`)} />
      {c.count && <Scanner id={a.id} onScan={(ln, cnt) => { setData((x: any) => ({ ...x, counted_count: cnt.counted_count, lines_count: cnt.lines_count, lines: ln ? (x.lines.some((l: any) => l.line_no === ln.line_no) ? x.lines.map((l: any) => (l.line_no === ln.line_no ? ln : l)) : [...x.lines, ln]) : x.lines })); }} reloadAll={reload} />}
      <Panel title="Items" actions={<div className="row">
        <input placeholder="Find item, barcode or bin" value={q} onChange={e => setQ(e.target.value)} aria-label="Find item" />
        <select value={filter} onChange={e => setFilter(e.target.value as any)} aria-label="Show"><option value="all">All</option><option value="todo">Not counted</option><option value="short">Shortage</option><option value="excess">Excess</option><option value="ok">Matched</option></select></div>}>
        <div className="sa-lines">{lines.map((l: any) => {
          const e = edits[l.line_no] || {};
          const phys = e.physical_qty !== undefined ? e.physical_qty : l.physical_qty ?? '';
          const preview = phys === '' || phys == null ? null : Number(phys) - (l.physical_qty != null ? l.system_qty : l.live_qty);
          return <div key={l.line_no} className={'sa-line ' + tone(l.variance_qty) + (e.physical_qty !== undefined || e.condition || e.remarks !== undefined ? ' dirty' : '')}>
            <div className="sa-item"><b>{l.name}</b> <span className="muted">{l.item_id}{l.sub ? ` · ${l.sub}` : ''}{l.bin ? ` · bin ${l.bin}` : ''}</span>
              <div className="muted small">{l.category} · {l.uom}{l.added_in_count ? ' · added during count' : ''}{l.counted_at ? ` · counted ${when(l.counted_at)}` : ''}</div></div>
            <div className="sa-nums">
              <span><small>System</small><b>{l.physical_qty != null ? l.system_qty : l.live_qty}</b></span>
              <span><small>Physical</small>{c.count ? <input inputMode="numeric" className="sa-qty" value={phys} onChange={ev2 => upd(l.line_no, { physical_qty: ev2.target.value.replace(/\D/g, '') })} aria-label={`Physical count of ${l.name}`} /> : <b>{l.physical_qty ?? '—'}</b>}</span>
              <span><small>Variance</small><b className={tone(l.physical_qty != null ? l.variance_qty : preview)}>{(l.physical_qty != null && e.physical_qty === undefined ? l.variance_qty : preview) ?? '—'}</b></span>
              {a.show_cost && <span className="hide-sm"><small>Value</small><b className={tone(l.variance_value)}>{l.variance_value != null ? inr(l.variance_value) : '—'}</b></span>}
            </div>
            <div className="sa-meta">
              {l.decision && l.decision !== 'Matched' && <Pill s={l.decision === 'Approved' ? `Approved · ${l.adjustment_no || ''}` : l.decision} tone={l.decision === 'Approved' ? 'ok' : 'bad'} />}
              {l.recount && <Pill s="Recount" tone="warn" />}
              {c.count ? <select value={e.condition || l.condition} onChange={ev2 => upd(l.line_no, { condition: ev2.target.value })} aria-label="Condition">{['Good', 'Damaged', 'Expired', 'Missing', 'Excess', 'Returned'].map(x => <option key={x}>{x}</option>)}</select>
                : <Pill s={l.condition} tone={['Damaged', 'Expired', 'Missing'].includes(l.condition) ? 'bad' : ''} />}
              {c.count ? <input placeholder="Remarks" value={e.remarks ?? l.remarks ?? ''} onChange={ev2 => upd(l.line_no, { remarks: ev2.target.value })} aria-label="Remarks" /> : l.remarks && <span className="muted">{l.remarks}</span>}
              {['Damaged', 'Expired'].includes(e.condition || l.condition) && (c.count
                ? <input inputMode="numeric" className="sa-qty" placeholder="Damaged qty" title="How many of the counted pieces are damaged / expired (blank = all)" value={e.dmg_qty ?? l.dmg_qty ?? ''}
                    onChange={ev2 => upd(l.line_no, { dmg_qty: ev2.target.value.replace(/\D/g, '') })} aria-label="Damaged or expired quantity" />
                : <span className="muted">damaged {l.dmg_qty ?? l.physical_qty}</span>)}
              {c.count && <input placeholder="Batch / serial" className="sa-batch" value={e.batch_no ?? l.batch_no ?? ''} onChange={ev2 => upd(l.line_no, { batch_no: ev2.target.value })} aria-label="Batch or serial" />}
              <Btn kind="link" onClick={() => setEv(l)}>{l.evidence ? `📎 ${l.evidence}` : '+ Photo'}</Btn>
              {l.status && <Pill s={l.status} />}
            </div>
          </div>;
        })}{!lines.length && <p className="empty">No items match.</p>}</div>
      </Panel>
      {dirty > 0 && <div className="paybar"><span>{dirty} unsaved line{dirty === 1 ? '' : 's'}</span><Btn onClick={() => setEdits({})}>Discard</Btn><Btn kind="pri" busy={busy} onClick={saveCounts}>Save counts</Btn></div>}
      <div className="grid2">
        <Panel title="People and approvals">
          <Kv items={[['Auditors', a.auditor_names.join(', ')], ['Supervisor', a.supervisor_name], ['Created', `${when(a.created_at)} · ${a.created_by_name}`],
            ['Submitted', a.submitted_at ? `${when(a.submitted_at)} · ${a.submitted_by_name}` : undefined], ['Verified', a.verified_at ? `${when(a.verified_at)} · ${a.verified_by_name}` : undefined],
            ['Reviewed', a.reviewed_at ? `${when(a.reviewed_at)} · ${a.reviewed_by_name}${a.review_note ? ' — ' + a.review_note : ''}` : undefined],
            ['Approval', a.approval ? `${a.approval.no} · ${a.approval.status} (needs ${a.approval.required_role})${a.approval.decided_by_name ? ' · ' + a.approval.decided_by_name : ''}` : undefined],
            ['Approved', a.approved_at ? `${when(a.approved_at)} · ${a.approved_by_name}` : undefined], ['Rejected', a.rejected_reason],
            ['Adjustment', a.adjustment ? `${a.adjustment.no} · ${inr(a.adjustment.value)} · voucher ${a.adjustment.journal_no || '—'}` : a.adjustment_no || undefined],
            ['Instructions', a.instructions]]} />
          <EvidencePanel refNo={a.no} company={a.company} />
        </Panel>
        <Panel title="Activity log">
          <ol className="timeline">{a.events.slice().reverse().map((e: any, i: number) => <li key={i}><span className="muted">{when(e.at)} · {e.user_name}</span> <b>{e.action}</b>
            {e.detail && <div className="muted small">{fmtDetail(e.detail)}</div>}</li>)}</ol>
        </Panel>
      </div>
      {a.decisions?.length > 0 && <DecisionRecord rows={a.decisions} showCost={a.show_cost} />}
      {ev && <Modal title={`Evidence · ${ev.name}`} onClose={() => { setEv(null); reload(); }}><p className="muted">Photos or documents for this item (required for damaged, expired or missing stock and large variances).</p><EvidencePanel refNo={`${a.no}/${ev.item_id}`} company={a.company} /></Modal>}
      {act && <ActionDialog act={act} a={a} busy={busy} onClose={() => setAct('')} onDo={post} />}
      {addAud && <AddAuditor a={a} onClose={r => { setAddAud(false); if (r) setData(r); }} />}
    </Page>
  );
}

function fmtDetail(d: any) {
  if (d == null) return '';
  if (typeof d === 'string') { try { d = JSON.parse(d); } catch { return d; } }
  if (typeof d !== 'object') return String(d);
  return Object.entries(d).filter(([, v]) => v != null && v !== '').map(([k, v]) => `${k.replace(/_/g, ' ')}: ${typeof v === 'object' ? JSON.stringify(v) : v}`).join(' · ');
}

function ActionDialog({ act, a, busy, onClose, onDo }: { act: string; a: any; busy: boolean; onClose: () => void; onDo: (path: string, body: any, msg?: string) => Promise<any> }) {
  const [note, setNote] = useState('');
  const title = { verify: 'Verify the physical count', review: 'Review the variance', reopen: 'Reopen this audit', cancel: 'Cancel this audit' }[act];
  return <Modal title={title} onClose={onClose} footer={<>
    <Btn onClick={onClose}>Back</Btn>
    {act === 'verify' && <><Btn busy={busy} onClick={() => onDo('verify', { ok: false, note }, 'Returned to the auditors for recount.')}>Return for recount</Btn><Btn kind="pri" busy={busy} onClick={() => onDo('verify', { ok: true, note }, 'Verified. Sent to accounts / inventory for review.')}>Count verified</Btn></>}
    {act === 'review' && <><Btn busy={busy} onClick={() => onDo('review', { action: 'return', note }, 'Returned for recount.')}>Recount</Btn><Btn kind="danger" busy={busy} onClick={() => onDo('review', { action: 'reject', note }, 'Audit rejected.')}>Reject</Btn>
      <Btn kind="pri" busy={busy} onClick={() => onDo('review', { action: 'approve', note }, 'Sent to the CFO / Director for approval.')}>Send for approval</Btn></>}
    {act === 'reopen' && <Btn kind="pri" busy={busy} onClick={() => onDo('reopen', { reason: note }, 'Reopened. Counts must be entered again.')}>Reopen</Btn>}
    {act === 'cancel' && <Btn kind="danger" busy={busy} onClick={() => onDo('cancel', { reason: note }, 'Audit cancelled.')}>Cancel audit</Btn>}
  </>}>
    <Kv items={[['Items counted', `${a.counted_count} of ${a.lines_count} (${a.matched_count} matched)`], ['Shortage', money(a.shortage_value)], ['Excess', money(a.excess_value)], ['Damaged / expired', money(a.damaged_value)]]} />
    {act === 'review' && <p className="muted">Send for approval passes the items with a variance to the CFO (or the Director for high values) for the final decision. Stock does not change until they approve; approved items then post the stock adjustment and journal automatically. Items that match are closed as matched.</p>}
    {act === 'reopen' && <p className="note">{a.status === 'Closed' ? 'The adjustment already posted stays. All counts are cleared and must be entered again, so nothing can be adjusted twice.' : 'The audit goes back to counting.'}</p>}
    <Field label={act === 'verify' || act === 'review' ? 'Note (required to return or reject)' : 'Reason *'} wide><textarea rows={3} value={note} onChange={e => setNote(e.target.value)} /></Field>
  </Modal>;
}

// What happens next and who does it, so nobody wonders why the count boxes are not shown.
function NextStep({ a, onImpact }: { a: any; onImpact: () => void }) {
  const n = a.next_step;
  if (!n) return null;
  return <div className={'next-step' + (n.mine ? ' mine' : '')} role="status">
    <span className="ns-step">Next: {n.step}</span>
    <span className="ns-text">{n.text} {n.who?.length ? <span className="muted">· {n.who.slice(0, 4).join(', ')}{n.who.length > 4 ? ` +${n.who.length - 4}` : ''}</span> : null}</span>
    {n.mine ? <Pill s="Your step" tone="ok" /> : <span className="muted small">This step is for the people named; sign in as one of them to do it.</span>}
    {n.step.startsWith('Approve') && <Btn kind={n.mine ? 'pri' : 'ghost'} onClick={onImpact}>Open impact review</Btn>}
  </div>;
}

function DecisionRecord({ rows, showCost }: { rows: any[]; showCost: boolean }) {
  return <Panel title="Approval decisions (permanent record)">
    <DataTable rows={rows} keyOf={r => r.id} cols={[
      { key: 'decided_at', label: 'When', primary: true, render: r => when(r.decided_at) }, { key: 'mode', label: 'Decision' },
      { key: 'decided_by_name', label: 'By', render: r => `${r.decided_by_name} (${r.decided_role})` },
      { key: 'adjustment_no', label: 'Adjustment / journal', hideSmall: true, render: r => r.adjustment_no ? <span className="mono">{r.adjustment_no} · {r.journal_no || '—'}</span> : '—' },
      ...(showCost ? [{ key: 'net', label: 'Net ₹', align: 'r' as const, render: (r: any) => <span className={tone(r.net)}>{money(r.net)}</span> }] : []),
      { key: 'remarks', label: 'Remarks', hideSmall: true }]} />
  </Panel>;
}

function AddAuditor({ a, onClose }: { a: any; onClose: (r?: any) => void }) {
  const meta = useMeta();
  const [uid, setUid] = useState('');
  const [run, busy] = useAction();
  const people = (meta.data?.people || []).filter((p: any) => p.count && !a.auditors.includes(p.id) && p.id !== a.supervisor_id && (p.branches === '*' || p.branches.includes(a.branch_id)));
  return <Modal title="Add an auditor" onClose={() => onClose()} footer={<><Btn onClick={() => onClose()}>Cancel</Btn>
    <Btn kind="pri" busy={busy} disabled={!uid} onClick={async () => { const r = await run(() => api(`/stock-audits/${a.id}/auditors`, { body: { user_id: uid } }), () => 'Auditor added and notified.'); if (r) onClose(r); }}>Add</Btn></>}>
    <Field label="Person who will count" hint="Anyone with the stock-count right at this location, including the internal Auditor"><select value={uid} onChange={e => setUid(e.target.value)}><option value="">Choose…</option>
      {people.map((p: any) => <option key={p.id} value={p.id}>{p.name} · {p.role}</option>)}</select></Field>
  </Modal>;
}

// ------------------------------------------------------------ CFO / Director: impact review and decision
const DECISIONS = ['Approved', 'Pending', 'Recount', 'Rejected'] as const;
function ImpactPage() {
  const { id = '' } = useParams();
  const nav = useNavigate();
  const { data: im, err, reload } = useLoad<any>(() => api(`/stock-audits/${id}/impact`), [id]);
  const [sel, setSel] = useState<Record<number, string>>({});
  const [remarks, setRemarks] = useState('');
  const [run, busy] = useAction();
  const [only, setOnly] = useState<'cycle' | 'all'>('cycle');
  useEffect(() => { if (im) setSel(Object.fromEntries(im.lines.filter((l: any) => l.in_cycle).map((l: any) => [l.line_no, 'Approved']))); }, [im]);
  if (err) return <Page title="Impact review"><ErrorNote err={err} /></Page>;
  if (!im) return <Page title="Impact review"><p className="muted">Loading…</p></Page>;
  const A = im.audit, T = im.totals, show = A.show_cost;
  const rows = im.lines.filter((l: any) => only === 'all' || l.in_cycle);
  const counts = DECISIONS.map(d => [d, Object.values(sel).filter(v => v === d).length] as const);
  const decide = async (mode: string) => {
    const body: any = { mode, remarks, decisions: mode === 'partial' ? sel : undefined };
    const msg = ({ all: 'Approved. The stock adjustment and journal are posted.', partial: 'Decision recorded. Approved items are posted.',
      return: 'Returned to the auditors for recount / clarification.', reject: 'Adjustment rejected.' } as Record<string, string>)[mode];
    const r: any = await run(() => api(`/stock-audits/${id}/decide`, { body }), () => msg || '');
    if (r) nav(`/stock-audits/${id}`);
  };
  const v = (x: number | null | undefined) => (show ? money(x) : '—');
  return <Page title={`Impact review · ${A.no}`} sub={<><Pill s={A.status} />{A.escalated && <Pill s="High value · Director" tone="bad" />} {A.type_label} · {A.location_name} ({A.location_type}){A.stock_area ? ` · ${A.stock_area}` : ''} · {day(A.audit_date)}
      {im.approval && <> · approval {im.approval.no} needs <b>{im.approval.role}</b> · {im.approval.items} item(s) to decide</>}</>}
    actions={<><Btn onClick={() => nav(`/stock-audits/${id}`)}>‹ Audit</Btn><Btn kind="link" onClick={reload}>Refresh</Btn></>}>
    {!im.can_decide && <p className="note">{im.approval ? `Only the ${im.approval.role}${im.approval.role === 'CFO' ? ' or Director' : ''} can decide. You can read the impact; auditors cannot approve their own audit.` : 'This audit is not waiting for a decision.'}</p>}
    <div className="tiles small">
      <div className="tile"><span>Items audited · matched</span><b>{T.audited} · {T.matched}</b></div>
      <div className="tile"><span>Shortage</span><b className="var-short">−{T.short_qty} units · {v(T.short_value)}</b></div>
      <div className="tile"><span>Excess</span><b className="var-excess">+{T.excess_qty} units · {v(T.excess_value)}</b></div>
      <div className="tile"><span>Damaged / expired</span><b>{T.dmg_qty} units · {v(T.dmg_value)}</b></div>
      <div className="tile"><span>Net impact</span><b className={tone(T.net_value)}>{v(T.net_value)}</b></div>
      <div className="tile"><span>Stock value</span><b>{v(T.system_value)} → {v(T.physical_value)}</b></div>
    </div>
    <Panel title="Items" actions={<div className="row"><select value={only} onChange={e => setOnly(e.target.value as any)} aria-label="Show"><option value="cycle">Items to decide</option><option value="all">All counted items</option></select>
      {im.can_decide && <><Btn onClick={() => setSel(s => Object.fromEntries(Object.keys(s).map(k => [k, 'Approved'])))}>Approve all</Btn><Btn onClick={() => setSel(s => Object.fromEntries(Object.keys(s).map(k => [k, 'Pending'])))}>Hold all</Btn></>}</div>}>
      <DataTable rows={rows} keyOf={r => r.line_no} rowClass={r => (r.colour === 'shortage' ? 'var-short-row' : r.colour === 'excess' ? 'var-excess-row' : '') + (r.high_value ? ' row-hi' : '')} cols={[
        { key: 'name', label: 'Item', primary: true, render: r => <><b>{r.name}</b> <span className="muted">{r.item_id}{r.sub ? ` · ${r.sub}` : ''}</span>{r.high_value && <Pill s="High value" tone="bad" />}<div className="muted small">{r.category} · {r.uom}{r.bin ? ` · bin ${r.bin}` : ''}{r.batch_no ? ` · batch ${r.batch_no}` : ''}</div></> },
        ...(show ? [{ key: 'unit_cost', label: 'Unit cost', align: 'r' as const, hideSmall: true, render: (r: any) => money(r.unit_cost) }] : []),
        { key: 'system_qty', label: 'System', align: 'r', render: r => <>{r.system_qty}{show && <div className="muted small">{money(r.system_value)}</div>}</> },
        { key: 'physical_qty', label: 'Physical', align: 'r', render: r => <>{r.physical_qty}{show && <div className="muted small">{money(r.physical_value)}</div>}</> },
        { key: 'variance_qty', label: 'Short / excess', align: 'r', render: r => <span className={tone(r.variance_qty)}>{r.short_qty ? `−${r.short_qty}` : r.excess_qty ? `+${r.excess_qty}` : '0'}{show && <div className="small">{r.short_value ? money(-r.short_value) : r.excess_value ? money(r.excess_value) : ''}</div>}</span> },
        { key: 'dmg_qty', label: 'Damaged', align: 'r', hideSmall: true, render: r => r.dmg_qty ? <>{r.dmg_qty} {String(r.condition).toLowerCase()}{show && <div className="small">{money(r.dmg_value)}</div>}</> : '' },
        ...(show ? [{ key: 'net_value', label: 'Net ₹', align: 'r' as const, render: (r: any) => <b className={tone(r.net_value)}>{money(r.net_value)}</b> }] : []),
        { key: 'remarks', label: 'Remarks / evidence', hideSmall: true, render: r => <>{r.remarks}{r.evidence ? <span className="muted"> · 📎 {r.evidence}</span> : ''}</> },
        { key: 'decision', label: 'Decision', render: r => r.in_cycle && im.can_decide
          ? <select value={sel[r.line_no] || 'Pending'} onChange={e => setSel(s => ({ ...s, [r.line_no]: e.target.value }))} aria-label={`Decision for ${r.name}`}>{DECISIONS.map(d => <option key={d}>{d}</option>)}</select>
          : r.decision ? <Pill s={r.decision} tone={r.decision === 'Approved' ? 'ok' : r.decision === 'Rejected' ? 'bad' : ''} /> : r.in_cycle ? <Pill s="To decide" /> : '' },
      ]} empty="No counted items." />
    </Panel>
    <div className="grid2">
      <Panel title="By category">
        <DataTable rows={im.categories} keyOf={r => r.category} cols={[{ key: 'category', label: 'Category', primary: true }, { key: 'items', label: 'Items', align: 'r' },
          { key: 'short_qty', label: 'Short', align: 'r', render: r => <span className="var-short">{r.short_qty ? `−${r.short_qty}` : ''} {show && r.short_value ? money(r.short_value) : ''}</span> },
          { key: 'excess_qty', label: 'Excess', align: 'r', render: r => <span className="var-excess">{r.excess_qty ? `+${r.excess_qty}` : ''} {show && r.excess_value ? money(r.excess_value) : ''}</span> },
          ...(show ? [{ key: 'net_value', label: 'Net', align: 'r' as const, render: (r: any) => <span className={tone(r.net_value)}>{money(r.net_value)}</span> }] : [])]} />
        <h4>By condition</h4>
        <Kv items={Object.entries(im.conditions).map(([k, x]: [string, any]) => [k, `${x.items} item(s) · ${x.qty} units${show ? ' · ' + money(x.value) : ''}`] as [string, string])} />
      </Panel>
      <Panel title="Accounting entry that will post">
        {show ? <><DataTable rows={im.journal.map((j: any, i: number) => ({ ...j, k: i }))} keyOf={r => r.k} cols={[{ key: 'account', label: 'Ledger', primary: true, render: r => <>{r.account} {r.name}<div className="muted small">{r.why}</div></> },
          { key: 'debit', label: 'Debit', align: 'r', render: r => (r.debit ? inr(r.debit) : '') }, { key: 'credit', label: 'Credit', align: 'r', render: r => (r.credit ? inr(r.credit) : '') }]} empty="No entry: nothing to adjust." />
          <p className="muted small">Ledgers come from Settings → Stock audit (shortage {im.ledgers.short}, damage {im.ledgers.dmg}, excess {im.ledgers.excess}, stock {im.ledgers.inv}). Damaged / expired stock is {im.write_off_damaged ? 'written off' : 'not written off'}. Only approved items post.</p></>
          : <p className="muted">Values are hidden for your role.</p>}
      </Panel>
    </div>
    <div className="grid2">
      <Panel title="Comments and history"><ol className="timeline">{im.comments.slice().reverse().map((e: any, i: number) => <li key={i}><span className="muted">{when(e.at)} · {e.user_name}</span> <b>{e.action}</b>{e.detail && <div className="muted small">{fmtDetail(e.detail)}</div>}</li>)}</ol></Panel>
      <Panel title="Evidence">{im.evidence.length ? <ul className="plain">{im.evidence.map((x: any) => <li key={x.id}><a href={`/api/evidence/${x.id}/download`}>{x.file_name}</a> <span className="muted">{x.ref} · {x.kind}{x.remarks ? ' · ' + x.remarks : ''}</span></li>)}</ul> : <p className="muted">No evidence attached.</p>}</Panel>
    </div>
    {im.decisions.length > 0 && <DecisionRecord rows={im.decisions} showCost={show} />}
    {im.can_decide && <div className="paybar decide">
      <span className="muted">{counts.filter(([, n]) => n).map(([d, n]) => `${n} ${d.toLowerCase()}`).join(' · ')}</span>
      <input placeholder="Remarks (required to return or reject)" value={remarks} onChange={e => setRemarks(e.target.value)} aria-label="Remarks" />
      <ConfirmBtn label="Reject" busy={busy} onConfirm={() => decide('reject')} />
      <Btn busy={busy} onClick={() => decide('return')}>Return for recount</Btn>
      <Btn busy={busy} onClick={() => decide('partial')}>Approve selected</Btn>
      <ConfirmBtn kind="pri" label="Approve all & post" confirm="Approve and post now" busy={busy} onConfirm={() => decide('all')} />
    </div>}
  </Page>;
}

function SheetUpload({ id, onDone }: { id: number; onDone: (a: any) => void }) {
  const ref = useRef<HTMLInputElement>(null);
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const go = async () => {
    const f = ref.current?.files?.[0]; if (!f) return;
    setBusy(true);
    try { const fd = new FormData(); fd.append('file', f); const r: any = await api(`/stock-audits/${id}/import`, { form: fd }); onDone(r); toast(`${r.imported_rows} rows imported from ${f.name}.`, 'ok'); }
    catch (e) { toast(errText(e), 'bad'); } finally { setBusy(false); if (ref.current) ref.current.value = ''; }
  };
  return <><input ref={ref} type="file" accept=".xlsx,.csv" hidden onChange={go} /><Btn busy={busy} onClick={() => ref.current?.click()}>Upload count sheet</Btn></>;
}

// Barcode counting: a hardware scanner types the code and Enter into the box; on phones with BarcodeDetector the camera works too.
function Scanner({ id, onScan, reloadAll }: { id: number; onScan: (line: any, c: any) => void; reloadAll: () => void }) {
  const toast = useToast();
  const [code, setCode] = useState('');
  const [qty, setQty] = useState('1');
  const [last, setLast] = useState<any>(null);
  const [cam, setCam] = useState(false);
  const box = useRef<HTMLInputElement>(null);
  const send = async (c: string) => {
    if (!c.trim()) return;
    try {
      const r: any = await api(`/stock-audits/${id}/scan`, { body: { code: c.trim(), qty: Number(qty) || 1 } });
      setLast(r.line); onScan(r.line, r); if (navigator.vibrate) navigator.vibrate(40);
    } catch (e) { toast(errText(e), 'bad'); if (navigator.vibrate) navigator.vibrate([80, 60, 80]); }
    setCode(''); box.current?.focus();
  };
  const hasDetector = typeof window !== 'undefined' && 'BarcodeDetector' in window;
  return <Panel title="Scan to count" actions={<Btn kind="link" onClick={reloadAll}>Refresh</Btn>}>
    <div className="row">
      <input ref={box} autoFocus placeholder="Scan barcode / type item code + Enter" value={code} onChange={e => setCode(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); send(code); } }} aria-label="Barcode" />
      <input className="w-s" inputMode="numeric" value={qty} onChange={e => setQty(e.target.value)} aria-label="Quantity per scan" title="Quantity added per scan" />
      <Btn onClick={() => send(code)}>Add</Btn>
      {hasDetector && <Btn onClick={() => setCam(true)}>Camera</Btn>}
    </div>
    {last && <p className="note ok">{last.name} {last.sub} → counted <b>{last.physical_qty}</b> (system {last.system_qty})</p>}
    <p className="muted small">Each scan adds the quantity shown. Items found on the shelf that are not on the list are added to the audit.</p>
    {cam && <CameraScan onCode={c => send(c)} onClose={() => setCam(false)} />}
  </Panel>;
}

function CameraScan({ onCode, onClose }: { onCode: (c: string) => void; onClose: () => void }) {
  const video = useRef<HTMLVideoElement>(null);
  const [err, setErr] = useState('');
  useEffect(() => {
    let stream: MediaStream | null = null, stop = false, lastCode = '', lastAt = 0;
    (async () => {
      try {
        stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' } });
        if (!video.current) return;
        video.current.srcObject = stream; await video.current.play();
        const det = new (window as any).BarcodeDetector({ formats: ['code_128', 'ean_13', 'ean_8', 'upc_a', 'qr_code', 'code_39'] });
        while (!stop) {
          const found = await det.detect(video.current).catch(() => []);
          const v = found[0]?.rawValue;
          if (v && (v !== lastCode || Date.now() - lastAt > 1500)) { lastCode = v; lastAt = Date.now(); onCode(v); }
          await new Promise(r => setTimeout(r, 250));
        }
      } catch (e) { setErr('Camera not available: ' + errText(e)); }
    })();
    return () => { stop = true; stream?.getTracks().forEach(t => t.stop()); };
  }, []);  // eslint-disable-line react-hooks/exhaustive-deps
  return <Modal title="Point the camera at the barcode" onClose={onClose} footer={<Btn kind="pri" onClick={onClose}>Done</Btn>}>
    {err ? <p className="err">{err}</p> : <video ref={video} className="cam" playsInline muted />}
    <p className="muted">Each barcode read adds one count. Hold steady; the same code is counted again after 1.5 seconds.</p>
  </Modal>;
}

// ------------------------------------------------------------ dashboard
function Dashboard() {
  const { org } = useAuth();
  const [from, setFrom] = useState(monthStart());
  const [to, setTo] = useState(today());
  const [company, setCompany] = useState('');
  const { data } = useLoad<any>(() => api(`/stock-audits/dashboard?from=${from}&to=${to}&company=${company}`), [from, to, company]);
  const trend = useMemo(() => {
    const t: Record<string, Record<string, number>> = {};
    for (const r of data?.trend || []) { t[r.month] = t[r.month] || {}; t[r.month][r.location] = r.net_variance_value; }
    return t;
  }, [data]);
  const max = Math.max(1, ...Object.values(trend).flatMap(m => Object.values(m).map(v => Math.abs(v || 0))));
  return <>
    <div className="filters"><input type="date" value={from} onChange={e => setFrom(e.target.value)} aria-label="From" /><input type="date" value={to} onChange={e => setTo(e.target.value)} aria-label="To" />
      <select value={company} onChange={e => setCompany(e.target.value)} aria-label="Company"><option value="">All companies</option>{(org?.companies || []).map(c => <option key={c.code} value={c.code}>{c.name}</option>)}</select></div>
    {data && <>
      <div className="tiles">
        <div className="tile"><span>Current (system) stock value</span><b>{money(data.system_value)}</b></div>
        <div className="tile"><span>Physical stock value</span><b>{money(data.physical_value)}</b></div>
        <div className="tile"><span>Shortage value</span><b className="var-short">{money(data.shortage_value)}</b></div>
        <div className="tile"><span>Excess value</span><b className="var-excess">{money(data.excess_value)}</b></div>
        <div className="tile"><span>Damaged / expired value</span><b>{money(data.damaged_value)}</b></div>
        <div className="tile"><span>Pending approval</span><b>{data.pending_approval}</b><small>{data.pending_count} counting · {data.overdue} overdue · {data.to_post} to post</small></div>
        <div className="tile"><span>Stock accuracy</span><b>{data.accuracy_pct ?? '—'}%</b><small>{data.audits} audits in period</small></div>
      </div>
      <Panel title="Location-wise variance trend (net ₹, last 6 months)">
        {Object.keys(trend).length ? <div className="trend">{Object.entries(trend).map(([m, locs]) => <div key={m} className="trend-m"><b>{m}</b>
          {Object.entries(locs).map(([loc, v]) => <div key={loc} className="trend-row"><span className="trend-l">{loc}</span>
            <span className="trend-bar"><i className={v < 0 ? 'neg' : 'pos'} style={{ width: `${Math.max(2, (Math.abs(v || 0) / max) * 100)}%` }} /></span><span className={'r ' + tone(v)}>{money(v)}</span></div>)}</div>)}</div>
          : <p className="empty">No audits in the last six months.</p>}
      </Panel>
      <Panel title="By location"><DataTable rows={data.by_location} keyOf={r => r.location + r.company} cols={[{ key: 'location', label: 'Location', primary: true }, { key: 'location_type', label: 'Type' },
        { key: 'audits', label: 'Audits', align: 'r' }, { key: 'shortage_value', label: 'Shortage', align: 'r', render: r => <span className="var-short">{money(r.shortage_value)}</span> },
        { key: 'excess_value', label: 'Excess', align: 'r', render: r => <span className="var-excess">{money(r.excess_value)}</span> },
        { key: 'damaged_value', label: 'Damaged', align: 'r', render: r => money(r.damaged_value) }]} empty="No audits in this period." /></Panel>
    </>}
  </>;
}

// ------------------------------------------------------------ reports
function Reports() {
  const { org } = useAuth();
  const meta = useMeta();
  const keys: [string, string][] = Object.entries(meta.data?.reports || {}) as [string, string][];
  const [key, setKey] = useState('summary');
  const [f, setF] = useState({ from: monthStart(), to: today(), company: '', location_type: '', branch: '' });
  const qs = new URLSearchParams(Object.entries(f).filter(([, v]) => v) as [string, string][]).toString();
  const { data: got, err } = useLoad<{ k: string; d: any[] }>(() => api<any[]>(`/stock-audits/reports/${key}?${qs}`).then(d => ({ k: key, d })), [key, qs]);
  const rows = got?.k === key ? got.d : null;
  const title = keys.find(([k]) => k === key)?.[1] || key;
  const table = useMemo(() => (rows ? { ...fromObjects(title, rows), period: { from: f.from, to: f.to }, company: f.company } : null), [rows, title, f.from, f.to, f.company]);
  const formats = useFormats('saudit');
  return <>
    <div className="report-pick" role="tablist">{keys.map(([k, l]) => <button key={k} className={k === key ? 'on' : ''} onClick={() => setKey(k)}>{l}</button>)}</div>
    <div className="filters">
      <input type="date" value={f.from} onChange={e => setF({ ...f, from: e.target.value })} aria-label="From" /><input type="date" value={f.to} onChange={e => setF({ ...f, to: e.target.value })} aria-label="To" />
      <select value={f.company} onChange={e => setF({ ...f, company: e.target.value })} aria-label="Company"><option value="">All companies</option>{(org?.companies || []).map(c => <option key={c.code} value={c.code}>{c.name}</option>)}</select>
      <select value={f.location_type} onChange={e => setF({ ...f, location_type: e.target.value })} aria-label="Location type"><option value="">All location types</option>{(meta.data?.location_types || []).map((t: string) => <option key={t}>{t}</option>)}</select>
      <select value={f.branch} onChange={e => setF({ ...f, branch: e.target.value })} aria-label="Location"><option value="">All locations</option>{(meta.data?.branches || []).map((b: any) => <option key={b.id} value={b.id}>{b.name} · {b.company}</option>)}</select>
    </div>
    <ErrorNote err={err} />
    <Panel>
      <div className="rep-head no-print"><h2>{title}</h2>{rows && <span className="muted rep-count">{rows.length} {rows.length === 1 ? "row" : "rows"}</span>}
        <ReportActions url={`/stock-audits/reports/${key}?${qs}`} name={`stock-audit-${key}-${f.to}`} formats={formats} r={table} /></div>
      {!table ? <p className="muted">Loading…</p> : <ReportSheet r={table} subtitle={[f.location_type, f.branch].filter(Boolean).join(' · ') || undefined}>
        <ReportTable r={table} empty="No rows for this period." rowClass={i => tone(rows![i].variance_qty ?? rows![i].net_variance_value ?? null)} /></ReportSheet>}</Panel>
  </>;
}
