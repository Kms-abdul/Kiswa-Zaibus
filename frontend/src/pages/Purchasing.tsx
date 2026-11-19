/* Purchasing: requisition → supplier quotes → submit PO (approval by value) → receive goods (GRN) → book supplier bill → pay.
   Purchase returns raise a debit note against the supplier. */
import { useState } from 'react';
import { api } from '../api';
import { useAuth } from '../auth';
import { co, day, inr, today } from '../format';
import { BranchSelect, Btn, CompanySelect, ConfirmBtn, DataTable, EvidencePanel, Field, ItemSearch, Kv, Modal, Page, Panel, Pill, Tabs, useAction, useLoad, useUrlState } from '../ui/kit';

export default function Purchasing() {
  const { can } = useAuth();
  const [tab, setTab] = useUrlState<'orders' | 'bills' | 'payments'>('tab', 'orders');
  const [status, setStatus] = useState('');
  const [open, setOpen] = useState<number | null>(null);
  const [create, setCreate] = useState(false);
  const [pay, setPay] = useState(false);
  const orders = useLoad<any[]>(() => (tab === 'orders' ? api(`/purchasing/orders${status ? `?status=${encodeURIComponent(status)}` : ''}`) : Promise.resolve([])), [tab, status]);
  const bills = useLoad<any[]>(() => (tab === 'bills' ? api('/purchasing/bills') : Promise.resolve([])), [tab]);
  const pays = useLoad<any[]>(() => (tab === 'payments' ? api('/purchasing/payments') : Promise.resolve([])), [tab]);
  return (
    <Page title="Purchasing" sub="Requisition, quotes, purchase orders, goods receipt, supplier bills and payments."
      actions={<>{can('add', 'acc') && <Btn onClick={() => setPay(true)}>Pay supplier</Btn>}{can('add', 'pur') && <Btn kind="pri" onClick={() => setCreate(true)}>New requisition</Btn>}</>}>
      <Tabs value={tab} onChange={setTab} tabs={[['orders', 'Orders'], ['bills', 'Supplier bills'], ['payments', 'Payments']]} />
      {tab === 'orders' && <>
        <div className="filters"><select value={status} onChange={e => setStatus(e.target.value)} aria-label="Status"><option value="">All statuses</option>
          {['Requisition', 'Quotes', 'Pending approval', 'PO approved', 'Partly received', 'Goods received', 'Invoiced', 'Cancelled', 'Rejected'].map(s => <option key={s}>{s}</option>)}</select></div>
        <Panel><DataTable rows={orders.data} onRow={r => setOpen(r.id)} cols={[
          { key: 'no', label: 'PO', primary: true, render: r => <span className="mono">{r.no}</span> }, { key: 'branch_id', label: 'Location' },
          { key: 'supplier_name', label: 'Supplier' }, { key: 'required_by', label: 'Needed by', hideSmall: true, render: r => day(r.required_by) },
          { key: 'value', label: 'Value', align: 'r', render: r => inr(r.value) }, { key: 'status', label: 'Status', render: r => <Pill s={r.status} /> },
        ]} /></Panel></>}
      {tab === 'bills' && <Panel><DataTable rows={bills.data} cols={[
        { key: 'internal_no', label: 'Our no', primary: true }, { key: 'bill_no', label: 'Supplier inv' }, { key: 'supplier_name', label: 'Supplier' },
        { key: 'bill_date', label: 'Date', render: r => day(r.bill_date) }, { key: 'due_date', label: 'Due', hideSmall: true, render: r => day(r.due_date) },
        { key: 'amount', label: 'Amount', align: 'r', render: r => inr(r.amount) }, { key: 'outstanding', label: 'Outstanding', align: 'r', render: r => inr(r.outstanding) }]} /></Panel>}
      {tab === 'payments' && <Panel><DataTable rows={pays.data} cols={[
        { key: 'no', label: 'Voucher', primary: true }, { key: 'supplier_name', label: 'Supplier' }, { key: 'pay_date', label: 'Date', render: r => day(r.pay_date || r.txn_date) },
        { key: 'mode', label: 'Mode' }, { key: 'reference', label: 'Ref', hideSmall: true }, { key: 'amount', label: 'Amount', align: 'r', render: r => inr(r.amount) }]} /></Panel>}
      {open && <PoDetail id={open} onClose={() => { setOpen(null); orders.reload(); }} />}
      {create && <NewPo onClose={id => { setCreate(false); orders.reload(); if (id) setOpen(id); }} />}
      {pay && <PaySupplier onClose={() => { setPay(false); pays.reload(); bills.reload(); }} />}
    </Page>
  );
}

function PoDetail({ id, onClose }: { id: number; onClose: () => void }) {
  const { can } = useAuth();
  const { data: po, setData } = useLoad<any>(() => api(`/purchasing/orders/${id}`), [id]);
  const [mode, setMode] = useState<'' | 'quote' | 'receive' | 'bill' | 'return'>('');
  const [run, busy] = useAction();
  const [reason, setReason] = useState('');
  const [sup, setSup] = useState('');
  const { data: sups } = useLoad<any[]>(() => (po ? api(`/suppliers?company=${po.company}`) : Promise.resolve([])), [po?.company]);
  if (!po) return null;
  const post = async (path: string, body: any, msg: (x: any) => string) => {
    const r = await run(() => api(`/purchasing/orders/${id}/${path}`, { body }), msg);
    if (r) { setMode(''); setData(await api(`/purchasing/orders/${id}`)); }
  };
  const early = ['Requisition', 'Quotes'].includes(po.status);
  const receivable = ['PO approved', 'Partly received'].includes(po.status);
  return (
    <Modal wide title={<>{po.no} <Pill s={po.status} /></>} onClose={onClose} footer={!mode && <>
      {early && can('edit', 'pur') && <Btn onClick={() => setMode('quote')}>Add quote</Btn>}
      {early && can('submit', 'pur') && <><select value={sup} onChange={e => setSup(e.target.value)} aria-label="Supplier"><option value="">Supplier…</option>{(sups || []).map(s => <option key={s.id} value={s.id}>{s.name}</option>)}</select>
        <input placeholder="Reason (if not cheapest)" value={reason} onChange={e => setReason(e.target.value)} aria-label="Reason" />
        <Btn kind="pri" busy={busy} onClick={() => post('submit', { supplier_id: sup || po.supplier_id, reason }, x => (x.approval ? '' : 'PO approved.'))}>Submit PO</Btn></>}
      {receivable && can('edit', 'pur') && <Btn kind="pri" onClick={() => setMode('receive')}>Receive goods</Btn>}
      {['Goods received', 'Partly received'].includes(po.status) && !po.bill && can('add', 'acc') && <Btn kind="pri" onClick={() => setMode('bill')}>Book supplier bill</Btn>}
      {po.grns?.length > 0 && can('edit', 'pur') && <Btn onClick={() => setMode('return')}>Purchase return</Btn>}
      {(early || po.status === 'PO approved') && can('cancel', 'pur') && <ConfirmBtn label="Cancel PO" busy={busy} onConfirm={() => post('cancel', { reason: reason || 'Not needed' }, () => 'PO cancelled.')} />}
    </>}>
      <Kv items={[['Company', co(po.company)], ['Location', po.branch_id], ['Supplier', po.supplier_name], ['Needed by', day(po.required_by)], ['Value', inr(po.value)], ['Freight', po.freight ? inr(po.freight) : undefined], ['Notes', po.notes]]} />
      <DataTable rows={po.lines} keyOf={r => r.line_no} cols={[{ key: 'name', label: 'Item', primary: true }, { key: 'qty', label: 'Qty', align: 'r' },
        { key: 'rate', label: 'Rate', align: 'r', render: r => inr(r.rate) }, { key: 'received_qty', label: 'Received', align: 'r' }, { key: 'rejected_qty', label: 'Rejected', align: 'r' }, { key: 'returned_qty', label: 'Returned', align: 'r' }]} />
      {po.quotes?.length > 0 && <><h4>Quotes</h4><DataTable rows={po.quotes} keyOf={r => r.supplier_id} cols={[{ key: 'supplier_name', label: 'Supplier', primary: true },
        { key: 'total', label: 'Total', align: 'r', render: r => inr(r.rates.reduce((s: number, x: string, i: number) => s + Number(x) * po.lines[i].qty, 0)) },
        { key: 'lead_days', label: 'Lead days', align: 'r' }, { key: 'selected', label: '', render: r => (r.selected ? <Pill s="Selected" tone="ok" /> : '') }]} /></>}
      {po.grns?.length > 0 && <><h4>Goods receipts</h4><DataTable rows={po.grns} keyOf={r => r.no} cols={[{ key: 'no', label: 'GRN', primary: true }, { key: 'txn_date', label: 'Date', render: r => day(r.txn_date) }, { key: 'value', label: 'Value', align: 'r', render: r => inr(r.value) }]} /></>}
      {po.bill && <Kv items={[['Supplier bill', `${po.bill.bill_no} (${po.bill.internal_no})`], ['Bill amount', inr(po.bill.amount)], ['Due', day(po.bill.due_date)]]} />}
      {mode === 'quote' && <QuoteForm po={po} sups={sups || []} busy={busy} onSave={b => post('quotes', b, () => 'Quote recorded.')} onCancel={() => setMode('')} />}
      {mode === 'receive' && <ReceiveForm po={po} busy={busy} onSave={b => post('receive', b, (x: any) => `GRN ${x.grn} posted (${inr(x.value)}).`)} onCancel={() => setMode('')} />}
      {mode === 'bill' && <BillForm busy={busy} onSave={b => post('bill', b, (x: any) => `Bill booked as ${x.internal_no}.`)} onCancel={() => setMode('')} />}
      {mode === 'return' && <ReturnForm po={po} busy={busy} onSave={b => post('return', b, (x: any) => `Debit note ${x.no} raised.`)} onCancel={() => setMode('')} />}
      <EvidencePanel refNo={po.no} company={po.company} />
    </Modal>
  );
}

function QuoteForm({ po, sups, busy, onSave, onCancel }: { po: any; sups: any[]; busy: boolean; onSave: (b: any) => void; onCancel: () => void }) {
  const [s, setS] = useState('');
  const [rates, setRates] = useState<string[]>(po.lines.map(() => ''));
  const [lead, setLead] = useState('');
  return <Panel title="Supplier quote"><div className="form">
    <Field label="Supplier"><select value={s} onChange={e => setS(e.target.value)}><option value="">Choose…</option>{sups.map(x => <option key={x.id} value={x.id}>{x.name}</option>)}</select></Field>
    {po.lines.map((l: any, i: number) => <Field key={l.line_no} label={`${l.name} × ${l.qty} — rate excl. GST`}><input inputMode="decimal" value={rates[i]} onChange={e => setRates(r => r.map((x, j) => (j === i ? e.target.value : x)))} /></Field>)}
    <Field label="Lead time (days)"><input inputMode="numeric" value={lead} onChange={e => setLead(e.target.value)} /></Field>
    <div className="row"><Btn onClick={onCancel}>Back</Btn><Btn kind="pri" busy={busy} onClick={() => onSave({ supplier_id: s, rates: rates.map(Number), lead_days: Number(lead) || null })}>Save quote</Btn></div>
  </div></Panel>;
}

function ReceiveForm({ po, busy, onSave, onCancel }: { po: any; busy: boolean; onSave: (b: any) => void; onCancel: () => void }) {
  const [rec, setRec] = useState<Record<number, { r: string; j: string }>>(Object.fromEntries(po.lines.map((l: any) => [l.line_no, { r: String(l.qty - l.received_qty), j: '' }])));
  const [date, setDate] = useState(today());
  const [freight, setFreight] = useState('');
  return <Panel title="Goods receipt (GRN)"><div className="form">
    {po.lines.map((l: any) => <div className="grid2" key={l.line_no}>
      <Field label={`${l.name} — received (open ${l.qty - l.received_qty})`}><input inputMode="numeric" value={rec[l.line_no].r} onChange={e => setRec(x => ({ ...x, [l.line_no]: { ...x[l.line_no], r: e.target.value } }))} /></Field>
      <Field label="of which rejected"><input inputMode="numeric" value={rec[l.line_no].j} onChange={e => setRec(x => ({ ...x, [l.line_no]: { ...x[l.line_no], j: e.target.value } }))} /></Field></div>)}
    <div className="grid2"><Field label="Date"><input type="date" value={date} onChange={e => setDate(e.target.value)} /></Field>
      <Field label="Freight (₹)" hint="Spread over accepted items as landed cost"><input inputMode="decimal" value={freight} onChange={e => setFreight(e.target.value)} placeholder={String(po.freight || 0)} /></Field></div>
    <div className="row"><Btn onClick={onCancel}>Back</Btn><Btn kind="pri" busy={busy} onClick={() => onSave({ date, freight: freight === '' ? undefined : Number(freight),
      lines: po.lines.map((l: any) => ({ line_no: l.line_no, received: Number(rec[l.line_no].r) || 0, rejected: Number(rec[l.line_no].j) || 0 })) })}>Post GRN</Btn></div>
  </div></Panel>;
}

function BillForm({ busy, onSave, onCancel }: { busy: boolean; onSave: (b: any) => void; onCancel: () => void }) {
  const [f, setF] = useState({ bill_no: '', bill_date: today(), bill_total: '' });
  return <Panel title="Supplier bill"><div className="form grid2">
    <Field label="Supplier invoice no."><input value={f.bill_no} onChange={e => setF({ ...f, bill_no: e.target.value })} /></Field>
    <Field label="Invoice date"><input type="date" value={f.bill_date} onChange={e => setF({ ...f, bill_date: e.target.value })} /></Field>
    <Field label="Invoice total (₹)" hint="Checked against GRN value + GST"><input inputMode="decimal" value={f.bill_total} onChange={e => setF({ ...f, bill_total: e.target.value })} /></Field>
    <div className="row"><Btn onClick={onCancel}>Back</Btn><Btn kind="pri" busy={busy} onClick={() => onSave(f)}>Book bill</Btn></div>
  </div></Panel>;
}

function ReturnForm({ po, busy, onSave, onCancel }: { po: any; busy: boolean; onSave: (b: any) => void; onCancel: () => void }) {
  const [line, setLine] = useState(String(po.lines[0]?.line_no || ''));
  const [qty, setQty] = useState('1');
  const [reason, setReason] = useState('');
  return <Panel title="Purchase return"><div className="form grid2">
    <Field label="Line"><select value={line} onChange={e => setLine(e.target.value)}>{po.lines.map((l: any) => <option key={l.line_no} value={l.line_no}>{l.name} (returnable {l.received_qty - l.rejected_qty - l.returned_qty})</option>)}</select></Field>
    <Field label="Quantity"><input inputMode="numeric" value={qty} onChange={e => setQty(e.target.value)} /></Field>
    <Field label="Reason" wide><input value={reason} onChange={e => setReason(e.target.value)} /></Field>
    <div className="row"><Btn onClick={onCancel}>Back</Btn><Btn kind="danger" busy={busy} onClick={() => onSave({ line_no: Number(line), qty: Number(qty), reason })}>Return to supplier</Btn></div>
  </div></Panel>;
}

function NewPo({ onClose }: { onClose: (id?: number) => void }) {
  const { org } = useAuth();
  const [branch, setBranch] = useState('');
  const [lines, setLines] = useState<{ item_id: string; name: string; qty: string; rate: string }[]>([]);
  const [requiredBy, setRequiredBy] = useState('');
  const [notes, setNotes] = useState('');
  const [run, busy] = useAction();
  const company = org?.branches.find(b => b.id === branch)?.company;
  const save = async () => {
    const r: any = await run(() => api('/purchasing/orders', { body: { branch_id: branch, required_by: requiredBy || undefined, notes,
      lines: lines.map(l => ({ item_id: l.item_id, qty: Number(l.qty), rate: l.rate === '' ? undefined : Number(l.rate) })) } }), (x: any) => `Requisition ${x.no} raised.`);
    if (r) onClose(r.id);
  };
  return <Modal wide title="New requisition" onClose={() => onClose()} footer={<><Btn onClick={() => onClose()}>Cancel</Btn><Btn kind="pri" busy={busy} disabled={!branch || !lines.length} onClick={save}>Raise requisition</Btn></>}>
    <div className="form grid2"><BranchSelect value={branch} onChange={v => { setBranch(v); setLines([]); }} label="For location" />
      <Field label="Needed by"><input type="date" value={requiredBy} onChange={e => setRequiredBy(e.target.value)} /></Field></div>
    {branch && <ItemSearch company={company} onPick={it => setLines(ls => [...ls, { item_id: it.id, name: `${it.name} ${it.size || ''}`, qty: '1', rate: '' }])} />}
    <ul className="cart">{lines.map((l, i) => <li key={i}><div className="cart-name">{l.name}</div><div className="cart-ctrl">
      <label className="disc">Qty<input inputMode="numeric" value={l.qty} onChange={e => setLines(ls => ls.map((x, j) => (j === i ? { ...x, qty: e.target.value } : x)))} /></label>
      <label className="disc">Rate<input inputMode="decimal" value={l.rate} placeholder="cost" onChange={e => setLines(ls => ls.map((x, j) => (j === i ? { ...x, rate: e.target.value } : x)))} /></label>
      <button className="x" onClick={() => setLines(ls => ls.filter((_, j) => j !== i))} aria-label="Remove">×</button></div></li>)}</ul>
    <Field label="Notes" wide><input value={notes} onChange={e => setNotes(e.target.value)} /></Field>
  </Modal>;
}

function PaySupplier({ onClose }: { onClose: () => void }) {
  const { org } = useAuth();
  const [company, setCompany] = useState(org?.companies[0]?.code || 'KG');
  const [f, setF] = useState({ supplier_id: '', amount: '', mode: 'bank', reference: '', date: today(), reason: '' });
  const { data: sups } = useLoad<any[]>(() => api(`/suppliers?company=${company}`), [company]);
  const { data: bills } = useLoad<any[]>(() => api('/purchasing/bills'), []);
  const out = (bills || []).filter(b => b.company === company && b.supplier_id === f.supplier_id).reduce((s, b) => s + (b.outstanding || 0), 0);
  const [run, busy] = useAction();
  const save = async () => { if (await run(() => api('/purchasing/payments', { body: { ...f, company, amount: Number(f.amount) } }), (x: any) => (x.no ? `Payment ${x.no} posted.` : ''))) onClose(); };
  return <Modal title="Pay supplier" onClose={onClose} footer={<><Btn onClick={onClose}>Cancel</Btn><Btn kind="pri" busy={busy} onClick={save}>Pay</Btn></>}>
    <div className="form">
      <CompanySelect value={company} onChange={setCompany} />
      <Field label="Supplier" hint={f.supplier_id ? `Outstanding ${inr(out)}` : undefined}><select value={f.supplier_id} onChange={e => setF({ ...f, supplier_id: e.target.value })}><option value="">Choose…</option>{(sups || []).map(s => <option key={s.id} value={s.id}>{s.name}</option>)}</select></Field>
      <div className="grid2"><Field label="Amount (₹)"><input inputMode="decimal" value={f.amount} onChange={e => setF({ ...f, amount: e.target.value })} /></Field>
        <Field label="Mode"><select value={f.mode} onChange={e => setF({ ...f, mode: e.target.value })}><option value="bank">Bank</option><option value="cash">Cash</option></select></Field>
        <Field label="UTR / cheque no."><input value={f.reference} onChange={e => setF({ ...f, reference: e.target.value })} /></Field>
        <Field label="Date"><input type="date" value={f.date} onChange={e => setF({ ...f, date: e.target.value })} /></Field></div>
    </div>
  </Modal>;
}
