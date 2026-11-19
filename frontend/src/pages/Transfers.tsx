/* Stock transfers between locations of the same company: request → (approval) → dispatch → receive (with shortages / damage). */
import { useState } from 'react';
import { api } from '../api';
import { useAuth } from '../auth';
import { day, inr } from '../format';
import { BranchSelect, Btn, ConfirmBtn, DataTable, Field, ItemSearch, Kv, Modal, Page, Panel, Pill, Tabs, useAction, useLoad, useUrlState } from '../ui/kit';

export default function Transfers() {
  const { can, org } = useAuth();
  const [status, setStatus] = useUrlState<string>('status', '');
  const [open, setOpen] = useState<number | null>(null);
  const [newS, setNewS] = useUrlState<string>('new', '');
  const create = newS === '1' && can('add', 'inv');
  const setCreate = (v: boolean) => setNewS(v ? '1' : '');
  const list = useLoad<any[]>(() => api(`/inventory/transfers${status ? `?status=${encodeURIComponent(status)}` : ''}`), [status]);
  const bname = (id: string) => org?.branches.find(b => b.id === id)?.name || id;
  return (
    <Page title="Transfers" sub="Move stock between locations. The sending location dispatches; the receiving location confirms what arrived."
      actions={can('add', 'inv') && <Btn kind="pri" onClick={() => setCreate(true)}>New transfer</Btn>}>
      <Tabs value={status} onChange={setStatus} tabs={[['', 'All'], ['Pending approval', 'Pending'], ['Approved', 'To dispatch'], ['In transit', 'In transit'], ['Received', 'Received']]} />
      <Panel><DataTable rows={list.data} onRow={r => setOpen(r.id)} cols={[
        { key: 'no', label: 'Transfer', primary: true, render: r => <span className="mono">{r.no}</span> },
        { key: 'from_branch', label: 'From', render: r => bname(r.from_branch) }, { key: 'to_branch', label: 'To', render: r => bname(r.to_branch) },
        { key: 'txn_date', label: 'Date', hideSmall: true, render: r => day(r.txn_date) }, { key: 'value', label: 'Value', align: 'r', hideSmall: true, render: r => inr(r.value) },
        { key: 'status', label: 'Status', render: r => <Pill s={r.status} /> },
      ]} /></Panel>
      {open && <TransferDetail id={open} bname={bname} onClose={() => { setOpen(null); list.reload(); }} />}
      {create && <NewTransfer onClose={() => { setCreate(false); list.reload(); }} />}
    </Page>
  );
}

function TransferDetail({ id, bname, onClose }: { id: number; bname: (b: string) => string; onClose: () => void }) {
  const { can, myBranches } = useAuth();
  const { data: t, reload } = useLoad<any>(() => api(`/inventory/transfers/${id}`), [id]);
  const [rec, setRec] = useState<Record<number, { r: string; d: string }>>({});
  const [reason, setReason] = useState('');
  const [run, busy] = useAction();
  if (!t) return null;
  const mine = (b: string) => myBranches.some(x => x.id === b);
  const act = async (path: string, body: any, msg: string) => { if (await run(() => api(`/inventory/transfers/${id}/${path}`, { body }), () => msg)) reload(); };
  return (
    <Modal wide title={<>Transfer {t.no} <Pill s={t.status} /></>} onClose={onClose} footer={<>
      {t.status === 'Approved' && can('edit', 'inv') && mine(t.from_branch) && <Btn kind="pri" busy={busy} onClick={() => act('dispatch', {}, 'Dispatched.')}>Dispatch</Btn>}
      {t.status === 'In transit' && can('edit', 'inv') && mine(t.to_branch) && <Btn kind="pri" busy={busy} onClick={() => act('receive', {
        lines: t.lines.map((l: any) => ({ line_no: l.line_no, received_qty: Number(rec[l.line_no]?.r ?? l.qty), damaged_qty: Number(rec[l.line_no]?.d || 0) })) }, 'Received.')}>Confirm receipt</Btn>}
      {['Pending approval', 'Approved'].includes(t.status) && can('cancel', 'inv') && <><input placeholder="Reason" value={reason} onChange={e => setReason(e.target.value)} aria-label="Reason" />
        <ConfirmBtn label="Cancel transfer" busy={busy} onConfirm={() => act('cancel', { reason }, 'Cancelled.')} /></>}
    </>}>
      <Kv items={[['From', bname(t.from_branch)], ['To', bname(t.to_branch)], ['Reason', t.reason], ['Value at cost', inr(t.value)], ['Receipt note', t.receipt_note]]} />
      <DataTable rows={t.lines} keyOf={r => r.line_no} cols={[{ key: 'name', label: 'Item', primary: true }, { key: 'qty', label: 'Sent', align: 'r' },
        ...(t.status === 'In transit' ? [
          { key: 'r', label: 'Received', render: (l: any) => <input className="w-s" inputMode="numeric" value={rec[l.line_no]?.r ?? l.qty} onChange={e => setRec(x => ({ ...x, [l.line_no]: { r: e.target.value, d: x[l.line_no]?.d || '' } }))} aria-label="Received" /> },
          { key: 'd', label: 'Damaged', render: (l: any) => <input className="w-s" inputMode="numeric" value={rec[l.line_no]?.d ?? ''} onChange={e => setRec(x => ({ ...x, [l.line_no]: { r: x[l.line_no]?.r ?? String(l.qty), d: e.target.value } }))} aria-label="Damaged" /> }]
          : [{ key: 'received_qty', label: 'Received', align: 'r' as const }, { key: 'damaged_qty', label: 'Damaged', align: 'r' as const }])]} />
    </Modal>
  );
}

function NewTransfer({ onClose }: { onClose: () => void }) {
  const { org } = useAuth();
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [lines, setLines] = useState<{ item_id: string; name: string; qty: string }[]>([]);
  const [reason, setReason] = useState('');
  const [run, busy] = useAction();
  const company = org?.branches.find(b => b.id === from)?.company;
  const save = async () => {
    const r = await run(() => api('/inventory/transfers', { body: { from_branch: from, to_branch: to, reason, lines: lines.map(l => ({ item_id: l.item_id, qty: Number(l.qty) || 0 })) } }),
      (x: any) => (x.approval ? '' : `Transfer ${x.no} created.`));
    if (r) onClose();
  };
  return <Modal wide title="New transfer" onClose={onClose} footer={<><Btn onClick={onClose}>Cancel</Btn><Btn kind="pri" busy={busy} disabled={!from || !to || !lines.length} onClick={save}>Request transfer</Btn></>}>
    <div className="form grid2">
      <BranchSelect label="From" value={from} onChange={v => { setFrom(v); setLines([]); }} />
      <BranchSelect label="To" value={to} onChange={setTo} company={company} all />
    </div>
    {from && <ItemSearch company={company} onPick={it => setLines(ls => [...ls, { item_id: it.id, name: `${it.name} ${it.size || ''}`, qty: '1' }])} />}
    <ul className="cart">{lines.map((l, i) => <li key={i}><div className="cart-name">{l.name}</div><div className="cart-ctrl">
      <input className="w-s" inputMode="numeric" value={l.qty} onChange={e => setLines(ls => ls.map((x, j) => (j === i ? { ...x, qty: e.target.value } : x)))} aria-label="Quantity" />
      <button className="x" onClick={() => setLines(ls => ls.filter((_, j) => j !== i))} aria-label="Remove">×</button></div></li>)}</ul>
    <Field label="Reason" wide><input value={reason} onChange={e => setReason(e.target.value)} placeholder="e.g. counter stock for school reopening" /></Field>
  </Modal>;
}
