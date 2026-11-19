import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../api';
import { inr, when } from '../format';
import { Btn, DataTable, EvidencePanel, Field, Kv, Modal, Page, Panel, Pill, Tabs, useAction, useLoad, useUrlState } from '../ui/kit';

type Status = 'Pending' | 'Returned' | 'Approved' | 'Rejected' | 'Cancelled' | 'All';

export default function Approvals() {
  const [status, setStatus] = useState<Status>('Pending');
  const [onlyMine, setOnlyMine] = useState(false);
  const { data, reload } = useLoad<any[]>(() => api(`/approvals?status=${status}${onlyMine ? '&mine=1' : ''}`), [status, onlyMine]);
  const [openS, setOpenS] = useUrlState<string>('open', '');
  const openId = openS ? Number(openS) : null;
  const setOpenId = (id: number | null) => setOpenS(id ? String(id) : '');
  return (
    <Page title="Approvals" sub="Requests waiting for a decision. You never approve your own request.">
      <div className="toolbar">
        <Tabs<Status> value={status} onChange={setStatus} tabs={[['Pending', 'Pending'], ['Returned', 'Returned'], ['Approved', 'Approved'], ['Rejected', 'Rejected'], ['Cancelled', 'Withdrawn'], ['All', 'All']]} />
        {status === 'Pending' && <label className="check"><input type="checkbox" checked={onlyMine} onChange={e => setOnlyMine(e.target.checked)} /> Only those I can decide</label>}
      </div>
      <Panel>
        <DataTable rows={data} onRow={r => setOpenId(r.id)} empty="Nothing waiting."
          cols={[
            { key: 'no', label: 'No.', render: r => <span className="mono">{r.no}</span> },
            { key: 'title', label: 'Request', primary: true, render: r => <>{r.title}{r.super_admin_only ? <Pill s="Super Admin only" tone="warn" /> : null}</> },
            { key: 'requested_by_name', label: 'Raised by', render: r => <>{r.requested_by_name}<small className="block muted">{when(r.requested_at)}</small></> },
            { key: 'required_role', label: 'Needs' },
            { key: 'amount', label: 'Amount', align: 'r', render: r => (r.amount ? inr(r.amount) : '—') },
            { key: 'status', label: 'Status', render: r => <Pill s={r.status === 'Pending' && r.can_decide ? 'Awaiting you' : r.status} tone={r.status === 'Pending' && r.can_decide ? 'info' : undefined} /> },
          ]} />
      </Panel>
      {openId && <ApprovalDetail id={openId} onClose={() => setOpenId(null)} onDone={() => { setOpenId(null); reload(); }} />}
    </Page>
  );
}

function payloadLines(p: any): [string, string][] {
  if (!p || typeof p !== 'object') return [];
  const out: [string, string][] = [];
  for (const [k, v] of Object.entries(p)) {
    if (['id', 'hold_id', 'invoice_id'].includes(k)) continue;
    out.push([k.replace(/_/g, ' '), typeof v === 'object' ? JSON.stringify(v).slice(0, 300) : String(v)]);
  }
  return out;
}

export function ApprovalDetail({ id, onClose, onDone }: { id: number; onClose: () => void; onDone: () => void }) {
  const { data: a } = useLoad<any>(() => api(`/approvals/${id}`), [id]);
  const [remarks, setRemarks] = useState('');
  const [run, busy] = useAction();
  const nav = useNavigate();
  const open = a && (a.kind === 'saudit' ? `/stock-audits/${a.payload?.id}/impact` : a.kind === 'finv' ? `/finance/vouchers/${a.payload?.id}` : '');
  const act = async (action: string) => {
    const r = await run(() => api(`/approvals/${id}/decide`, { body: { action, remarks } }), (x: any) => `${x.no} ${x.status.toLowerCase()}${x.result ? ': ' + x.result : ''}`);
    if (r) onDone();
  };
  const own = async (what: 'resubmit' | 'cancel') => {
    const r = await run(() => api(`/approvals/${id}/${what}`, { body: what === 'resubmit' ? { remarks } : { reason: remarks } }), () => (what === 'resubmit' ? 'Sent again.' : 'Request withdrawn.'));
    if (r) onDone();
  };
  return (
    <Modal wide title={a ? <><span className="mono">{a.no}</span> · {a.title}</> : 'Loading…'} onClose={onClose}
      footer={a && <>
        <Btn onClick={onClose}>Close</Btn>
        {open && <Btn kind={a.kind === 'saudit' && a.status === 'Pending' && a.can_decide ? 'pri' : 'ghost'} onClick={() => nav(open)}>{a.kind === 'saudit' ? 'Open impact review' : 'Open voucher'}</Btn>}
        {a.status === 'Pending' && a.can_decide && a.kind !== 'saudit' && <><Btn onClick={() => act('return')} busy={busy}>Return for changes</Btn><Btn kind="danger" onClick={() => act('reject')} busy={busy}>Reject</Btn><Btn kind="pri" onClick={() => act('approve')} busy={busy}>Approve</Btn></>}
        {a.mine && ['Pending', 'Returned'].includes(a.status) && <Btn onClick={() => own('cancel')} busy={busy}>Withdraw</Btn>}
        {a.mine && a.status === 'Returned' && <Btn kind="pri" onClick={() => own('resubmit')} busy={busy}>Send again</Btn>}
      </>}>
      {a && <>
        {a.detail && <p>{a.detail}</p>}
        {a.kind === 'saudit' && a.status === 'Pending' && <p className="note">Stock-audit adjustments are decided item by item on the impact review: approve all, approve some, return for recount or reject. Approved items post automatically.</p>}
        <Kv items={[['Raised by', `${a.requested_by_name} (${a.requested_role})`], ['Reason', a.reason], ['Needs', a.required_role + (a.super_admin_only ? ' (Super Admin only)' : '')],
          ['Amount', a.amount ? inr(a.amount) : undefined], ['Status', <><Pill s={a.status} /> {a.result}</>]]} />
        <details><summary>What will change</summary><Kv items={payloadLines(a.payload)} /></details>
        <h4>History</h4>
        <ul className="log">{a.log.map((l: any, i: number) => <li key={i}><small>{when(l.at)}</small> {l.action} by {l.user_name} ({l.role}){l.remarks ? ' — ' + l.remarks : ''}</li>)}</ul>
        <EvidencePanel refNo={a.ref || a.no} company={a.company} />
        {a.status === 'Pending' && !a.can_decide && !a.mine && <p className="note">{a.blocked_reason}</p>}
        {(a.can_decide || (a.mine && ['Pending', 'Returned'].includes(a.status))) &&
          <Field label={a.can_decide ? 'Remarks (required to reject or return)' : 'What changed / why withdraw'}><textarea rows={2} value={remarks} onChange={e => setRemarks(e.target.value)} /></Field>}
      </>}
    </Modal>
  );
}
