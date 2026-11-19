/* Stock adjustments (damage, write-off, loss, found excess, physical count). Every adjustment needs a reason, evidence and approval. */
import { useState } from 'react';
import { api } from '../api';
import { useAuth } from '../auth';
import { day, inr } from '../format';
import { BranchSelect, Btn, DataTable, EvidencePanel, Field, ItemSearch, Kv, Modal, Page, Panel, Pill, useAction, useLoad } from '../ui/kit';

const TYPES = ['Damaged', 'Write-off', 'Obsolete', 'Lost / missing', 'Found excess', 'Physical count'];

export default function Adjustments() {
  const { can } = useAuth();
  const [open, setOpen] = useState<number | null>(null);
  const [create, setCreate] = useState(false);
  const list = useLoad<any[]>(() => api('/inventory/adjustments'), []);
  return (
    <Page title="Stock adjustments" sub="Attach a photo or count sheet before the approver can approve."
      actions={can('add', 'inv') && <Btn kind="pri" onClick={() => setCreate(true)}>New adjustment</Btn>}>
      <Panel><DataTable rows={list.data} onRow={r => setOpen(r.id)} cols={[
        { key: 'no', label: 'Adjustment', primary: true, render: r => <span className="mono">{r.no}</span> }, { key: 'branch_id', label: 'Location' },
        { key: 'adj_type', label: 'Type' }, { key: 'txn_date', label: 'Date', hideSmall: true, render: r => day(r.txn_date) },
        { key: 'value', label: 'Value', align: 'r', render: r => inr(r.value) }, { key: 'status', label: 'Status', render: r => <Pill s={r.status} /> },
      ]} /></Panel>
      {open && <Detail id={open} onClose={() => { setOpen(null); list.reload(); }} />}
      {create && <NewAdj onClose={id => { setCreate(false); list.reload(); if (id) setOpen(id); }} />}
    </Page>
  );
}

function Detail({ id, onClose }: { id: number; onClose: () => void }) {
  const { data: a } = useLoad<any>(() => api(`/inventory/adjustments/${id}`), [id]);
  if (!a) return null;
  return <Modal wide title={<>Adjustment {a.no} <Pill s={a.status} /></>} onClose={onClose}>
    <Kv items={[['Location', a.branch_id], ['Type', a.adj_type], ['Reason', a.reason], ['Value at cost', inr(a.value)], ['Approval', a.approval_no]]} />
    <DataTable rows={a.lines} keyOf={r => r.line_no} cols={[{ key: 'name', label: 'Item', primary: true }, { key: 'qty', label: 'Change', align: 'r', render: r => (r.qty > 0 ? '+' : '') + r.qty },
      { key: 'unit_cost', label: 'Cost', align: 'r', render: r => inr(r.unit_cost) }]} />
    <EvidencePanel refNo={a.no} company={a.company} />
  </Modal>;
}

function NewAdj({ onClose }: { onClose: (id?: number) => void }) {
  const { org } = useAuth();
  const [branch, setBranch] = useState('');
  const [type, setType] = useState('Damaged');
  const [reason, setReason] = useState('');
  const [lines, setLines] = useState<{ item_id: string; name: string; qty: string }[]>([]);
  const [run, busy] = useAction();
  const company = org?.branches.find(b => b.id === branch)?.company;
  const count = type === 'Physical count';
  const save = async () => {
    const r: any = await run(() => api('/inventory/adjustments', { body: { branch_id: branch, adj_type: type, reason,
      lines: lines.map(l => (count ? { item_id: l.item_id, counted_qty: Number(l.qty) } : { item_id: l.item_id, qty: Number(l.qty) })) } }),
      (x: any) => `Adjustment ${x.no} created. Attach evidence now.`);
    if (r) onClose(r.id);
  };
  return <Modal wide title="New stock adjustment" onClose={() => onClose()} footer={<><Btn onClick={() => onClose()}>Cancel</Btn><Btn kind="pri" busy={busy} disabled={!branch || !lines.length} onClick={save}>Submit for approval</Btn></>}>
    <div className="form grid2">
      <BranchSelect value={branch} onChange={setBranch} />
      <Field label="Type"><select value={type} onChange={e => setType(e.target.value)}>{TYPES.map(t => <option key={t}>{t}</option>)}</select></Field>
    </div>
    {branch && <ItemSearch company={company} onPick={it => setLines(ls => [...ls, { item_id: it.id, name: `${it.name} ${it.size || ''}`, qty: '1' }])} />}
    <ul className="cart">{lines.map((l, i) => <li key={i}><div className="cart-name">{l.name}</div><div className="cart-ctrl">
      <label className="disc">{count ? 'Counted' : 'Qty'}<input inputMode="numeric" value={l.qty} onChange={e => setLines(ls => ls.map((x, j) => (j === i ? { ...x, qty: e.target.value } : x)))} /></label>
      <button className="x" onClick={() => setLines(ls => ls.filter((_, j) => j !== i))} aria-label="Remove">×</button></div></li>)}</ul>
    <Field label="Reason" wide><input value={reason} onChange={e => setReason(e.target.value)} /></Field>
  </Modal>;
}
