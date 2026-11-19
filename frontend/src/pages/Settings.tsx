/* Settings: policies (security, controls, maker-checker, escalation, online store, student categories, counters),
   books lock date, alert rules, ERP student import, and Razorpay status / reconciliation. */
import { useEffect, useRef, useState } from 'react';
import { api } from '../api';
import { useAuth } from '../auth';
import { co, day, inr, today, when } from '../format';
import { Btn, DataTable, ErrorNote, Field, Kv, Page, Panel, Pill, Tabs, useAction, useLoad, useUrlState } from '../ui/kit';
import ErpMapping from './ErpMapping';

const KEYS: [string, string, string][] = [
  ['security', 'Security policy', 'Password length, lockout attempts, session timeout, OTP. Changes need approval when maker-checker is on.'],
  ['controls', 'Business controls', 'Return days, cancel window, discount caps, evidence-before-approval list, negative stock rule.'],
  ['maker_checker', 'Maker-checker', 'Which kinds of change need a second person (access, cfg, masters…).'],
  ['escalation', 'Escalation', 'Hours before a pending approval escalates to the next level.'],
  ['online', 'Online store', 'Warehouse per company, collection points, per-student limits.'],
  ['student_categories', 'Student categories', '[name, sponsored?] pairs. Sponsored students are billed to MSEA.'],
  ['counters', 'Counters', 'POS counters per location.'],
  ['stock_audit', 'Stock audits', 'high_value (₹, escalates to management), photo_above (₹, evidence needed), due_days, spot_size, max_lines.'],
];

export default function Settings() {
  const { can } = useAuth();
  const tabs: [string, string][] = [];
  if (can('view', 'set')) tabs.push(['policy', 'Policies'], ['books', 'Books & alerts']);
  if (can('view', 'ctl.int')) tabs.push(['erp', 'ERP import']);
  if (can('view', 'ctl.pay')) tabs.push(['pay', 'Payments']);
  const [tab, setTab] = useUrlState<string>('tab', tabs[0]?.[0] || 'policy');
  return (
    <Page title="Settings" sub="Each change needs a reason and is written to the audit trail.">
      <Tabs value={tab} onChange={setTab} tabs={tabs} />
      {tab === 'policy' && <Policies />}
      {tab === 'books' && <Books />}
      {tab === 'erp' && <Erp />}
      {tab === 'pay' && <Payments />}
    </Page>
  );
}

function Policies() {
  const { can } = useAuth();
  const { data, reload } = useLoad<any>(() => api('/settings'), []);
  const [key, setKey] = useState('security');
  const [text, setText] = useState<string | null>(null);
  const [reason, setReason] = useState('');
  const [err, setErr] = useState('');
  const [run, busy] = useAction();
  if (!data) return <Panel><p className="muted">Loading…</p></Panel>;
  const value = text ?? JSON.stringify(data[key] ?? null, null, 2);
  const save = async () => {
    let v; try { v = JSON.parse(value); setErr(''); } catch (e) { setErr('Not valid JSON: ' + (e as Error).message); return; }
    if (await run(() => api(`/settings/${key}`, { method: 'PUT', body: { value: v, reason } }), (x: any) => (x.status === 'Applied' ? 'Setting applied.' : ''))) { setText(null); setReason(''); reload(); }
  };
  return <div className="split">
    <Panel><ul className="opt-list">{KEYS.map(([k, l, d]) => <li key={k}><button className={k === key ? 'on' : ''} onClick={() => { setKey(k); setText(null); }}><b>{l}</b><span className="muted">{d}</span></button></li>)}</ul></Panel>
    <Panel title={KEYS.find(k => k[0] === key)?.[1]}>
      <textarea className="code" rows={18} value={value} onChange={e => setText(e.target.value)} readOnly={!can('edit', 'set')} spellCheck={false} aria-label="Setting value (JSON)" />
      <ErrorNote err={err} />
      {can('edit', 'set') && <div className="row"><input placeholder="Reason for the change" value={reason} onChange={e => setReason(e.target.value)} aria-label="Reason" /><Btn kind="pri" busy={busy} onClick={save} disabled={text === null}>Save</Btn></div>}
    </Panel>
  </div>;
}

function Books() {
  const { can } = useAuth();
  const { data, reload } = useLoad<any>(() => api('/settings'), []);
  const [lock, setLock] = useState({ company: 'KG', date: today(), reason: '' });
  const [run, busy] = useAction();
  if (!data) return null;
  return <>
    <Panel title="Books lock date" actions={can('approve', 'acc') && <span className="muted">Nothing can be posted on or before the lock date.</span>}>
      <DataTable rows={data.companies} keyOf={r => r.code} cols={[{ key: 'name', label: 'Company', primary: true }, { key: 'books_locked_to', label: 'Locked up to', render: r => day(r.books_locked_to) }]} />
      {can('approve', 'acc') && <div className="row">
        <select value={lock.company} onChange={e => setLock({ ...lock, company: e.target.value })} aria-label="Company">{data.companies.map((c: any) => <option key={c.code} value={c.code}>{c.name}</option>)}</select>
        <input type="date" value={lock.date} onChange={e => setLock({ ...lock, date: e.target.value })} aria-label="Lock date" />
        <input placeholder="Reason" value={lock.reason} onChange={e => setLock({ ...lock, reason: e.target.value })} aria-label="Reason" />
        <Btn kind="pri" busy={busy} onClick={async () => { if (await run(() => api('/settings/lock', { body: lock }))) reload(); }}>Request lock</Btn></div>}
    </Panel>
    <Panel title="Alert rules"><DataTable rows={data.alert_rules} keyOf={r => r.alert_type} cols={[{ key: 'alert_type', label: 'Alert', primary: true },
      { key: 'in_app', label: 'In app', render: r => (r.in_app ? 'Yes' : 'No') }, { key: 'email', label: 'Email', render: r => (r.email ? 'Yes' : 'No') },
      { key: 'whatsapp', label: 'WhatsApp', render: r => (r.whatsapp ? 'Yes' : 'No') }, { key: 'roles', label: 'Roles', render: r => (r.roles || []).join(', ') || 'All with access' }]} /></Panel>
    <Panel title="Number series"><DataTable rows={data.number_series} keyOf={r => r.company + r.series + r.fy} cols={[{ key: 'company', label: 'Co' }, { key: 'series', label: 'Series', primary: true },
      { key: 'fy', label: 'FY' }, { key: 'last_no', label: 'Last number', align: 'r' }]} /></Panel>
  </>;
}

function Erp() {
  const { can } = useAuth();
  const runs = useLoad<any[]>(() => api('/erp/runs'), []);
  const tpl = useLoad<any>(() => api('/erp/template'), []);
  const [sel, setSel] = useState<any>(null);
  const recs = useLoad<any[]>(() => (sel ? api(`/erp/runs/${sel.id}/records`) : Promise.resolve([])), [sel?.id]);
  const file = useRef<HTMLInputElement>(null);
  const [dry, setDry] = useState(true);
  const [run, busy] = useAction();
  const running = (runs.data || []).some(r => r.status === 'Running');
  useEffect(() => {                                   // a background pull is working: refresh the list every 3 s
    if (!running) return;
    const t = setInterval(() => runs.reload(), 3000);
    return () => clearInterval(t);
  }, [running]);  // eslint-disable-line react-hooks/exhaustive-deps
  const upload = async () => {
    const f = file.current?.files?.[0]; if (!f) return;
    const fd = new FormData(); fd.append('file', f); fd.append('dry_run', dry ? '1' : '0');
    if (await run(() => api('/erp/import', { form: fd }), (x: any) => `${x.run}: ${x.created} created, ${x.updated} updated, ${x.skipped} unchanged, ${x.failed} failed${x.dry_run ? ' (trial run — nothing saved)' : ''}.`)) runs.reload();
  };
  return <>
    <ErpMapping onStarted={() => runs.reload()} />
    {can('import', 'ctl.int') && <Panel title="Import students from a CSV file">
      <p className="muted">CSV columns: <span className="mono">{tpl.data?.columns?.join(', ')}</span>. Re-importing the same file updates instead of duplicating.</p>
      <div className="row"><input ref={file} type="file" accept=".csv,text/csv" aria-label="CSV file" />
        <label className="check"><input type="checkbox" checked={dry} onChange={e => setDry(e.target.checked)} />Trial run first</label>
        <Btn kind="pri" busy={busy} onClick={upload}>Upload</Btn></div>
    </Panel>}
    <Panel title="Sync runs"><DataTable rows={runs.data} onRow={setSel} cols={[{ key: 'no', label: 'Run', primary: true }, { key: 'started_at', label: 'Started', render: r => when(r.started_at) },
      { key: 'trigger_type', label: 'How', render: r => r.trigger_type + (r.dry_run ? ' · trial' : '') }, { key: 'created', label: 'New', align: 'r' },
      { key: 'updated', label: 'Updated', align: 'r' }, { key: 'skipped', label: 'Unchanged', align: 'r' }, { key: 'failed', label: 'Failed', align: 'r' },
      { key: 'status', label: 'Status', render: r => <Pill s={r.status} tone={r.status === 'Running' ? 'info' : r.status === 'Failed' ? 'bad' : r.failed ? 'warn' : 'ok'} /> }]} /></Panel>
    {sel && <Panel title={`Records of ${sel.no}`} actions={<Btn onClick={() => setSel(null)}>Close</Btn>}><DataTable rows={recs.data} cols={[{ key: 'source_ref', label: 'ERP ref', primary: true },
      { key: 'action', label: 'Action' }, { key: 'status', label: 'Status' }, { key: 'error', label: 'Error' }]} /></Panel>}
  </>;
}

function Payments() {
  const { can } = useAuth();
  const status = useLoad<any>(() => api('/payments/status'), []);
  const [date, setDate] = useState(today());
  const recon = useLoad<any>(() => api(`/payments/reconciliation?date=${date}`), [date]);
  const pays = useLoad<any[]>(() => api(`/payments?date=${date}`), [date]);
  const [res, setRes] = useState<Record<string, string>>({});
  const [run, busy] = useAction();
  return <>
    <Panel title="Razorpay">{status.data && <Kv items={Object.entries(status.data).map(([c, s]: [string, any]) => [co(c), <>{s.mode === 'simulated' ? <Pill s="Simulated (no keys)" tone="warn" /> : <Pill s={s.mode} tone="ok" />} webhook secret {s.webhook_secret_set ? 'set' : 'missing'}</>])} />}
      {can('edit', 'ctl.pay') && <Btn onClick={() => run(() => api('/payments/go-live', { body: { reason: 'Keys configured and tested' } }))} busy={busy}>Request switch to Live</Btn>}</Panel>
    <div className="filters"><Field label="Date"><input type="date" value={date} onChange={e => setDate(e.target.value)} /></Field></div>
    <Panel title="Payments"><DataTable rows={pays.data} cols={[{ key: 'id', label: 'Payment', primary: true, render: r => <span className="mono">{r.id}</span> }, { key: 'ref', label: 'Ref' },
      { key: 'method', label: 'Method' }, { key: 'amount', label: 'Amount', align: 'r', render: r => inr(r.amount) }, { key: 'status', label: 'Status', render: r => <Pill s={r.status} tone={r.status === 'Captured' ? 'ok' : r.status === 'Failed' ? 'bad' : 'warn'} /> },
      { key: 'env', label: 'Env', hideSmall: true }]} empty="No payments on this date." /></Panel>
    <Panel title="Reconciliation exceptions"><DataTable rows={recon.data?.open_exceptions} keyOf={r => r.ex_key} cols={[{ key: 'ex_type', label: 'Type', primary: true }, { key: 'ref', label: 'Ref' },
      { key: 'amount', label: 'Amount', align: 'r', render: r => inr(r.amount) }, { key: 'detail', label: 'Detail' },
      { key: 'act', label: '', render: r => can('approve', 'ctl.pay') ? <div className="row"><input placeholder="Resolution" value={res[r.ex_key] || ''} onChange={e => setRes(x => ({ ...x, [r.ex_key]: e.target.value }))} aria-label="Resolution" />
        <Btn busy={busy} onClick={async () => { if (await run(() => api(`/payments/exceptions/${encodeURIComponent(r.ex_key)}/resolve`, { body: { resolution: res[r.ex_key] } }), () => 'Resolved.')) recon.reload(); }}>Resolve</Btn></div> : null }]} empty="No open exceptions." /></Panel>
  </>;
}
