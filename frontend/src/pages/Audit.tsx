/* Audit trail (append-only, hash-chained), integrity check, security events and alerts. */
import { useState } from 'react';
import { api } from '../api';
import { useAuth } from '../auth';
import { when } from '../format';
import { Btn, DataTable, Kv, Modal, Page, Panel, Pill, Tabs, useAction, useLoad, useUrlState } from '../ui/kit';

const show = (v: any) => (v == null ? '' : typeof v === 'string' ? v : JSON.stringify(v));

export default function Audit() {
  const { can } = useAuth();
  const [tab, setTab] = useUrlState<'audit' | 'security' | 'alerts'>('tab', can('view', 'audit') ? 'audit' : 'security');
  const [q, setQ] = useState('');
  const [cat, setCat] = useState('');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [sel, setSel] = useState<any>(null);
  const [verify, setVerify] = useState<any>(null);
  const [run, busy] = useAction();
  const rows = useLoad<any[]>(() => (tab === 'audit' ? api(`/audit?q=${encodeURIComponent(q)}&category=${cat}&from=${from}&to=${to}`) : tab === 'security' ? api('/security/events') : api('/alerts')), [tab, q, cat, from, to]);
  const tabs: ['audit' | 'security' | 'alerts', string][] = [];
  if (can('view', 'audit')) tabs.push(['audit', 'Audit trail']);
  if (can('view', 'ctl.sec')) tabs.push(['security', 'Security events']);
  tabs.push(['alerts', 'Alerts']);
  return (
    <Page title="Audit" sub="Who did what, when, from which device — with the old and new values. Entries cannot be edited or deleted."
      actions={can('audit', 'audit') && <Btn busy={busy} onClick={async () => setVerify(await run(() => api('/audit/verify')))}>Verify integrity</Btn>}>
      {verify && <div className={'note ' + (verify.ok ? 'ok' : 'bad')}>{verify.ok ? `Chain intact: ${verify.checked} entries checked.` : `Chain broken at entry ${verify.first_bad_id}. See docs/TROUBLESHOOTING.md → audit_chain_broken.`}</div>}
      <Tabs value={tab} onChange={setTab} tabs={tabs} />
      {tab === 'audit' && <div className="filters">
        <input placeholder="Action, reference, user or value" value={q} onChange={e => setQ(e.target.value)} aria-label="Search" />
        <select value={cat} onChange={e => setCat(e.target.value)} aria-label="Category"><option value="">All categories</option>
          {['sales', 'stock', 'purchase', 'accounts', 'cash', 'online', 'master', 'security', 'settings', 'approval', 'integration', 'msea'].map(c => <option key={c}>{c}</option>)}</select>
        <input type="date" value={from} onChange={e => setFrom(e.target.value)} aria-label="From" /><input type="date" value={to} onChange={e => setTo(e.target.value)} aria-label="To" />
      </div>}
      <Panel>
        {tab === 'audit' && <DataTable rows={rows.data} onRow={setSel} cols={[{ key: 'at', label: 'When', render: r => when(r.at) }, { key: 'user_name', label: 'User' },
          { key: 'action', label: 'Action', primary: true }, { key: 'ref', label: 'Ref' }, { key: 'category', label: 'Category', hideSmall: true }, { key: 'reason', label: 'Reason', hideSmall: true }]} />}
        {tab === 'security' && <DataTable rows={rows.data} cols={[{ key: 'at', label: 'When', render: r => when(r.at) }, { key: 'type', label: 'Event', primary: true },
          { key: 'user_name', label: 'User' }, { key: 'detail', label: 'Detail' }, { key: 'ip', label: 'IP', hideSmall: true }]} />}
        {tab === 'alerts' && <DataTable rows={rows.data} cols={[{ key: 'created_at', label: 'When', render: r => when(r.created_at) }, { key: 'alert_type', label: 'Alert', primary: true },
          { key: 'message', label: 'Message' }, { key: 'ref', label: 'Ref', hideSmall: true }, { key: 'severity', label: '', render: r => <Pill s={r.severity} tone={r.severity === 'bad' ? 'bad' : r.severity === 'warn' ? 'warn' : 'info'} /> }]} empty="No alerts in the last 14 days." />}
      </Panel>
      {sel && <Modal wide title={`${sel.action} · ${sel.ref || ''}`} onClose={() => setSel(null)}>
        <Kv items={[['When', when(sel.at)], ['User', `${sel.user_name} (${sel.role || ''})`], ['Company / branch', [sel.company, sel.branch_id].filter(Boolean).join(' · ')],
          ['Status', sel.status], ['Reason', sel.reason], ['Device', `${sel.device || ''} ${sel.ip || ''}`]]} />
        <div className="grid2"><div><h4>Old value</h4><pre className="code">{show(sel.old_value)}</pre></div><div><h4>New value</h4><pre className="code">{show(sel.new_value)}</pre></div></div>
      </Modal>}
    </Page>
  );
}
