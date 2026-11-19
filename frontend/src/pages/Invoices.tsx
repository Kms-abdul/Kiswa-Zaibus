/* Bills: search, view / print, deliver pending items, return or cancel, reprint. */
import { useState } from 'react';
import { api } from '../api';
import { useAuth } from '../auth';
import { inr, today, when } from '../format';
import { Btn, DataTable, Empty, Field, Kv, Modal, Page, Panel, Pill, Tabs, useAction, useLoad, useToast, useUrlState } from '../ui/kit';
import InvoiceView from './InvoiceView';

export default function Invoices() {
  const [tab, setTab] = useUrlState<'today' | 'all' | 'pending' | 'held'>('tab', 'today');
  const [q, setQ] = useUrlState<string>('q', '');
  const [openS, setOpenS] = useUrlState<string>('open', '');
  const open = openS ? Number(openS) : null;
  const setOpen = (id: number | null) => setOpenS(id ? String(id) : '');
  const qs = tab === 'today' && !q ? `date=${today()}` : tab === 'pending' ? 'pending=1' : '';
  const list = useLoad<any[]>(() => (tab === 'held' ? api('/pos/held') : api(`/pos/invoices?${qs}&q=${encodeURIComponent(q)}`)), [tab, q]);
  return (
    <Page title="Bills" sub="Search a bill by number or admission number. Open it to print, deliver pending items, return or cancel.">
      <Tabs value={tab} onChange={setTab} tabs={[['today', 'Today'], ['all', 'All'], ['pending', 'Pending delivery'], ['held', 'Held for approval']]} />
      {tab !== 'held' && <div className="filters"><input placeholder="Bill no. or admission no." value={q} onChange={e => setQ(e.target.value)} aria-label="Search bills" /></div>}
      <Panel>
        {tab === 'held' ? (
          <DataTable rows={list.data} cols={[
            { key: 'no', label: 'Held bill', primary: true }, { key: 'company', label: 'Co' }, { key: 'reason', label: 'Why held' },
            { key: 'approval_no', label: 'Approval' }, { key: 'status', label: 'Status', render: r => <Pill s={r.status} /> }, { key: 'invoice_no', label: 'Bill' },
          ]} />
        ) : (
          <DataTable rows={list.data} onRow={r => setOpen(r.id)} cols={[
            { key: 'no', label: 'Bill', primary: true, render: r => <span className="mono">{r.no}</span> },
            { key: 'inv_date', label: 'Date', render: r => when(`${r.inv_date} ${String(r.inv_time || '00:00').padStart(8, '0').slice(0, 5)}`) },
            { key: 'adm_no', label: 'Student / customer', render: r => r.adm_no || r.customer_id },
            { key: 'channel', label: 'Channel', hideSmall: true },
            { key: 'total', label: 'Total', align: 'r', render: r => inr(r.total) },
            { key: 'balance', label: 'Balance', align: 'r', hideSmall: true, render: r => (r.balance ? inr(r.balance) : '') },
            { key: 'status', label: 'Status', render: r => <Pill s={r.status} /> },
          ]} empty={<Empty title={q ? `No bill matches “${q}”.` : tab === 'today' ? 'No bills yet today.' : tab === 'pending' ? 'Nothing is waiting to be handed over.' : 'No bills.'}
            hint={q ? 'Search by the full or last digits of the bill number, or the admission number.' + (tab === 'today' ? ' Today’s tab searches all dates.' : '') : undefined} />} />
        )}
      </Panel>
      {open && <InvoiceDetail id={open} onClose={() => { setOpen(null); list.reload(); }} />}
    </Page>
  );
}

function InvoiceDetail({ id, onClose }: { id: number; onClose: () => void }) {
  const { can } = useAuth();
  const { data: inv, reload } = useLoad<any>(() => api(`/pos/invoices/${id}`), [id]);
  const [mode, setMode] = useState<'view' | 'return' | 'deliver'>('view');
  const [run, busy] = useAction();
  if (!inv) return <Modal title="Bill" onClose={onClose}><p className="muted">Loading…</p></Modal>;
  const pending = inv.lines.filter((l: any) => l.delivered_qty < l.qty - l.returned_qty);
  const reprint = async () => { if (await run(() => api(`/pos/invoices/${id}/reprint`, { body: {} }))) { reload(); setTimeout(() => window.print(), 50); } };
  return (
    <Modal wide title={<>Bill {inv.no} <Pill s={inv.status} /></>} onClose={onClose} footer={mode === 'view' && <>
      {can('print', 'pos') && <Btn onClick={reprint} busy={busy}>Print copy</Btn>}
      {pending.length > 0 && can('edit', 'pos') && <Btn onClick={() => setMode('deliver')}>Deliver pending</Btn>}
      {inv.channel !== 'online' && inv.status !== 'Cancelled' && can('cancel', 'pos') && <Btn kind="danger" onClick={() => setMode('return')}>Return / cancel</Btn>}
    </>}>
      {mode === 'view' && <>
        <InvoiceView inv={inv} />
        {inv.credit_notes?.length > 0 && <Panel title="Credit notes"><DataTable rows={inv.credit_notes} cols={[
          { key: 'no', label: 'No', primary: true }, { key: 'cn_date', label: 'Date' }, { key: 'kind', label: 'Kind' },
          { key: 'amount', label: 'Amount', align: 'r', render: r => inr(r.amount) }, { key: 'refund_mode', label: 'Refund' }, { key: 'reason', label: 'Reason' }]} /></Panel>}
      </>}
      {mode === 'deliver' && <Deliver inv={inv} onDone={() => { setMode('view'); reload(); }} />}
      {mode === 'return' && <Return inv={inv} onDone={() => { setMode('view'); reload(); }} />}
    </Modal>
  );
}

function Deliver({ inv, onDone }: { inv: any; onDone: () => void }) {
  const lines = inv.lines.filter((l: any) => l.delivered_qty < l.qty - l.returned_qty);
  const [q, setQ] = useState<Record<number, string>>(Object.fromEntries(lines.map((l: any) => [l.line_no, String(l.qty - l.returned_qty - l.delivered_qty)])));
  const [run, busy] = useAction();
  const go = async () => {
    const r = await run(() => api(`/pos/invoices/${inv.id}/deliver`, { body: { lines: lines.map((l: any) => ({ line_no: l.line_no, qty: Number(q[l.line_no]) || 0 })).filter((x: any) => x.qty) } }),
      (x: any) => `Delivered (${x.delivery_no}).`);
    if (r) onDone();
  };
  return <div className="form">
    <p className="muted">{inv.pending_reason}</p>
    {lines.map((l: any) => <Field key={l.line_no} label={`${l.description} (pending ${l.qty - l.returned_qty - l.delivered_qty})`}>
      <input inputMode="numeric" value={q[l.line_no]} onChange={e => setQ(x => ({ ...x, [l.line_no]: e.target.value }))} /></Field>)}
    <div className="row"><Btn onClick={onDone}>Back</Btn><Btn kind="pri" busy={busy} onClick={go}>Hand over now</Btn></div>
  </div>;
}

function Return({ inv, onDone }: { inv: any; onDone: () => void }) {
  const toast = useToast();
  const [kind, setKind] = useState<'Return' | 'Cancellation' | 'Exchange'>('Return');
  const [q, setQ] = useState<Record<number, string>>({});
  const [reason, setReason] = useState('');
  const [condition, setCondition] = useState('Good');
  const [mode, setMode] = useState(inv.msea_voucher_no ? 'credit' : inv.payments?.[0]?.mode === 'credit' ? 'credit' : 'cash');
  const [run, busy] = useAction();
  const est = inv.lines.reduce((s: number, l: any) => s + (kind === 'Cancellation' ? l.qty - l.returned_qty : Number(q[l.line_no]) || 0) * (l.amount / l.qty), 0);
  const go = async () => {
    if (!reason.trim()) { toast('Give the reason.', 'bad'); return; }
    const r = await run(() => api(`/pos/invoices/${inv.id}/return`, { body: { kind, reason, condition, refund_mode: mode,
      lines: inv.lines.map((l: any) => ({ line_no: l.line_no, qty: Number(q[l.line_no]) || 0 })) } }),
      (x: any) => (x.credit_note ? `Credit note ${x.credit_note} for ${inr(x.amount)} posted. Refund the customer now.` : ''));
    if (r) onDone();
  };
  return <div className="form">
    <Tabs value={kind} onChange={setKind} tabs={[['Return', 'Return'], ['Exchange', 'Exchange'], ['Cancellation', 'Cancel whole bill']]} />
    {kind !== 'Cancellation' && inv.lines.map((l: any) => l.qty - l.returned_qty > 0 && (
      <Field key={l.line_no} label={`${l.description} · up to ${l.qty - l.returned_qty}`}>
        <input inputMode="numeric" value={q[l.line_no] || ''} onChange={e => setQ(x => ({ ...x, [l.line_no]: e.target.value }))} placeholder="0" /></Field>))}
    <div className="grid2">
      <Field label="Goods condition"><select value={condition} onChange={e => setCondition(e.target.value)}><option>Good</option><option>Damaged</option></select></Field>
      <Field label="Refund by"><select value={mode} onChange={e => setMode(e.target.value)} disabled={!!inv.msea_voucher_no}>
        {['cash', 'upi', 'card', 'bank', 'wallet', 'credit'].map(m => <option key={m} value={m}>{m === 'credit' ? (inv.msea_voucher_no ? 'Back to MSEA voucher' : 'Against the account') : m.toUpperCase()}</option>)}</select></Field>
    </div>
    <Field label="Reason" wide><input value={reason} onChange={e => setReason(e.target.value)} placeholder="e.g. wrong size, exchanged for 32" /></Field>
    <Kv items={[['Approximate refund', inr(est)], ['Note', 'Above your limit it goes for approval first; stock and money move once approved.']]} />
    <div className="row"><Btn onClick={onDone}>Back</Btn><Btn kind="danger" busy={busy} onClick={go}>{kind === 'Cancellation' ? 'Cancel bill' : 'Post return'}</Btn></div>
  </div>;
}
