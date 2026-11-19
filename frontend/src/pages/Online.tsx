/* Online orders from the parent store: fulfilment queue (pick → QC/pack → dispatch → receive at branch → collect with code),
   parent cancellation / return requests, and refunds back to the original payment. */
import { useState } from 'react';
import { api } from '../api';
import { useAuth } from '../auth';
import { co, inr, when } from '../format';
import { Btn, DataTable, Field, Kv, Modal, Page, Panel, Pill, Tabs, useAction, useLoad, useUrlState } from '../ui/kit';

const QUEUES: [string, string, string][] = [
  ['Payment Confirmed', 'To pick', 'pick'], ['Picking', 'Picking', 'picked'], ['Quality Check', 'Quality check', 'qc'], ['Packed', 'To dispatch', 'dispatch'],
  ['Dispatched to Branch', 'In transit', 'receive'], ['Ready for Collection', 'Ready', 'collect'],
];
const STEP_LABEL: Record<string, string> = { pick: 'Start picking', picked: 'Picked — send to QC', qc: 'QC passed — pack', dispatch: 'Dispatch to branch', receive: 'Received at branch', collect: 'Hand over' };

export default function Online() {
  const [tab, setTab] = useUrlState<string>('tab', 'Payment Confirmed');
  const [q, setQ] = useState('');
  const [open, setOpen] = useState<number | null>(null);
  const isQueue = QUEUES.some(x => x[0] === tab);
  const orders = useLoad<any[]>(() => (isQueue || tab === 'all' ? api(`/online/orders?${isQueue ? `status=${encodeURIComponent(tab)}&` : ''}q=${encodeURIComponent(q)}`) : Promise.resolve([])), [tab, q]);
  const reqs = useLoad<any[]>(() => (tab === 'requests' ? api('/online/requests') : Promise.resolve([])), [tab]);
  const refunds = useLoad<any[]>(() => (tab === 'refunds' ? api('/online/refunds') : Promise.resolve([])), [tab]);
  return (
    <Page title="Online orders" sub="Work each queue in order. Stock was reserved when the parent ordered; the bill is posted when payment is confirmed.">
      <Tabs value={tab} onChange={setTab} tabs={[...QUEUES.map(([k, l]) => [k, l] as [string, string]), ['all', 'All'], ['requests', 'Requests'], ['refunds', 'Refunds']]} />
      {(isQueue || tab === 'all') && <>
        <div className="filters"><input placeholder="Order no., admission no. or name" value={q} onChange={e => setQ(e.target.value)} aria-label="Search orders" /></div>
        <Panel><DataTable rows={orders.data} onRow={r => setOpen(r.id)} cols={[
          { key: 'no', label: 'Order', primary: true, render: r => <span className="mono">{r.no}</span> },
          { key: 'student_name', label: 'Student', render: r => <>{r.student_name} <span className="muted">{r.adm_no}</span></> },
          { key: 'company', label: 'Co', render: r => co(r.company) }, { key: 'collect_at', label: 'Collect at', hideSmall: true },
          { key: 'total', label: 'Total', align: 'r', render: r => inr(r.total) }, { key: 'placed_at', label: 'Placed', hideSmall: true, render: r => when(r.placed_at) },
          { key: 'status', label: 'Status', render: r => <Pill s={r.status} /> },
        ]} empty="Queue is empty." /></Panel>
      </>}
      {tab === 'requests' && <Requests rows={reqs.data} reload={reqs.reload} />}
      {tab === 'refunds' && <Refunds rows={refunds.data} reload={refunds.reload} />}
      {open && <OrderDetail id={open} onClose={() => { setOpen(null); orders.reload(); }} />}
    </Page>
  );
}

function OrderDetail({ id, onClose }: { id: number; onClose: () => void }) {
  const { data: o, setData } = useLoad<any>(() => api(`/online/orders/${id}`), [id]);
  const [note, setNote] = useState('');
  const [code, setCode] = useState('');
  const [run, busy] = useAction();
  if (!o) return null;
  const step = QUEUES.find(x => x[0] === o.status)?.[2];
  const doStep = async (s: string, body: any = {}) => {
    const r = await run(() => api(`/online/orders/${id}/step/${s}`, { body }), (x: any) => `Order is now ${x.status}.${x.dev_collect_code ? ` Test collection code: ${x.dev_collect_code}` : ''}`);
    if (r) setData(r);
  };
  return (
    <Modal wide title={<>Order {o.no} <Pill s={o.status} /></>} onClose={onClose} footer={step && (step === 'collect' ? <>
      <input placeholder="6-digit collection code" inputMode="numeric" value={code} onChange={e => setCode(e.target.value)} aria-label="Collection code" />
      <Btn kind="pri" busy={busy} onClick={async () => { const r = await run(() => api(`/online/orders/${id}/collect`, { body: { code } }), () => 'Handed over to the parent.'); if (r) setData(r); }}>Hand over</Btn>
    </> : step === 'qc' ? <>
      <input placeholder="Note if QC fails" value={note} onChange={e => setNote(e.target.value)} aria-label="QC note" />
      <Btn kind="danger" busy={busy} onClick={() => doStep('qc', { ok: false, note })}>QC failed — repick</Btn>
      <Btn kind="pri" busy={busy} onClick={() => doStep('qc', { ok: true })}>{STEP_LABEL.qc}</Btn>
    </> : <Btn kind="pri" busy={busy} onClick={() => doStep(step)}>{STEP_LABEL[step]}</Btn>)}>
      <Kv items={[['Student', `${o.student_name} (${o.adm_no})`], ['Company', co(o.company)], ['Collect at', o.collect_point], ['Bill', o.invoice_no], ['Total', inr(o.total)], ['Payment', o.payment_id]]} />
      <DataTable rows={o.lines} keyOf={r => r.line_no} cols={[{ key: 'name', label: 'Item', primary: true, render: r => <>{r.name} <span className="muted">{r.size}</span></> },
        { key: 'bin', label: 'Bin' }, { key: 'qty', label: 'Qty', align: 'r' }, { key: 'picked_qty', label: 'Picked', align: 'r' },
        { key: 'amount', label: 'Amount', align: 'r', render: r => inr(r.amount) }]} />
      {o.requests?.length > 0 && <><h4>Requests</h4><DataTable rows={o.requests} cols={[{ key: 'no', label: 'No', primary: true }, { key: 'type', label: 'Type' }, { key: 'reason', label: 'Reason' }, { key: 'status', label: 'Status', render: r => <Pill s={r.status} /> }]} /></>}
      <h4>Timeline</h4>
      <ol className="timeline">{(o.events || []).map((e: any, i: number) => <li key={i}><span className="muted">{when(e[0])} · {e[1]}</span> {e[2]}</li>)}</ol>
    </Modal>
  );
}

function Requests({ rows, reload }: { rows: any[] | null; reload: () => void }) {
  const [sel, setSel] = useState<any>(null);
  const [note, setNote] = useState('');
  const [run, busy] = useAction();
  const decide = async (approve: boolean) => {
    if (await run(() => api(`/online/requests/${sel.id}/decide`, { body: { approve, note } }), (x: any) => `Request ${x.status}.${x.credit_note_no ? ' Credit note ' + x.credit_note_no : ''}`)) { setSel(null); reload(); }
  };
  return <Panel>
    <DataTable rows={rows} onRow={setSel} cols={[{ key: 'no', label: 'Request', primary: true }, { key: 'order_no', label: 'Order' }, { key: 'type', label: 'Type' },
      { key: 'reason', label: 'Reason' }, { key: 'created_at', label: 'When', hideSmall: true, render: r => when(r.created_at) }, { key: 'status', label: 'Status', render: r => <Pill s={r.status} /> }]} />
    {sel && <Modal title={`${sel.type} ${sel.no}`} onClose={() => setSel(null)} footer={sel.status === 'Requested' && <>
      <Btn kind="danger" busy={busy} onClick={() => decide(false)}>Reject</Btn><Btn kind="pri" busy={busy} onClick={() => decide(true)}>Approve</Btn></>}>
      <Kv items={[['Order', `${sel.order_no} (${sel.order_status})`], ['Reason', sel.reason], ['Lines', (sel.line_items || []).map((l: any) => `line ${l.line_no} × ${l.qty}`).join(', ')], ['Decision', sel.decision_note]]} />
      {sel.status === 'Requested' && <Field label="Note to the parent" wide><input value={note} onChange={e => setNote(e.target.value)} /></Field>}
      <p className="muted">Approving a cancellation releases reserved stock (or reverses the bill); approving a return posts a credit note once the goods are back. A refund entry is created for Accounts.</p>
    </Modal>}
  </Panel>;
}

function Refunds({ rows, reload }: { rows: any[] | null; reload: () => void }) {
  const { can } = useAuth();
  const [run, busy] = useAction();
  return <Panel><DataTable rows={rows} cols={[{ key: 'no', label: 'Refund', primary: true }, { key: 'order_no', label: 'Order' }, { key: 'credit_note_no', label: 'Credit note' },
    { key: 'amount', label: 'Amount', align: 'r', render: r => inr(r.amount) }, { key: 'original_payment', label: 'Payment', hideSmall: true },
    { key: 'status', label: 'Status', render: r => <Pill s={r.status} /> },
    { key: 'act', label: '', render: r => r.status === 'Pending' && can('fin', 'onl') ? <Btn busy={busy} onClick={async e => { e.stopPropagation(); if (await run(() => api(`/online/refunds/${r.id}/process`, { body: {} }), () => 'Refund sent to the gateway.')) reload(); }}>Process</Btn> : null }]} /></Panel>;
}
