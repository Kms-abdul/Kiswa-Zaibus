/* Accounts: voucher register, customer receipts, expenses, contra (cash ↔ bank), journal vouchers, reversals, inter-company funds. */
import { useState } from 'react';
import { api } from '../api';
import { useAuth } from '../auth';
import { co, day, inr, today } from '../format';
import { BranchSelect, Btn, CompanySelect, ConfirmBtn, DataTable, EvidencePanel, Field, Kv, Modal, Page, Panel, useAction, useLoad } from '../ui/kit';

type Form = '' | 'receipt' | 'expense' | 'contra' | 'journal' | 'ic';

export default function Accounts() {
  const { can, org } = useAuth();
  const [company, setCompany] = useState('');
  const [type, setType] = useState('');
  const [q, setQ] = useState('');
  const [from, setFrom] = useState(today().slice(0, 8) + '01');
  const [to, setTo] = useState(today());
  const [open, setOpen] = useState<number | null>(null);
  const [form, setForm] = useState<Form>('');
  const list = useLoad<any[]>(() => api(`/accounts/vouchers?company=${company}&type=${encodeURIComponent(type)}&q=${encodeURIComponent(q)}&from=${from}&to=${to}`), [company, type, q, from, to]);
  const add = can('add', 'acc');
  return (
    <Page title="Accounts" sub="Every document posts a balanced voucher. Vouchers are never edited — a wrong one is reversed."
      actions={add && <>
        <Btn onClick={() => setForm('receipt')}>Receipt</Btn><Btn onClick={() => setForm('expense')}>Expense</Btn><Btn onClick={() => setForm('contra')}>Contra</Btn>
        <Btn onClick={() => setForm('journal')}>Journal</Btn>{(org?.companies.length || 0) > 1 && <Btn onClick={() => setForm('ic')}>Inter-company</Btn>}</>}>
      <div className="filters">
        <select value={company} onChange={e => setCompany(e.target.value)} aria-label="Company"><option value="">All companies</option>{(org?.companies || []).map(c => <option key={c.code} value={c.code}>{c.name}</option>)}</select>
        <select value={type} onChange={e => setType(e.target.value)} aria-label="Type"><option value="">All types</option>
          {['Sale', 'Receipt', 'Payment', 'Expense', 'Contra', 'Journal', 'GRN', 'Purchase invoice', 'Credit note', 'Debit note', 'Delivery', 'Stock adjustment', 'Stock transfer', 'Settlement', 'Cash difference', 'Opening', 'Reversal'].map(t => <option key={t}>{t}</option>)}</select>
        <input type="date" value={from} onChange={e => setFrom(e.target.value)} aria-label="From" /><input type="date" value={to} onChange={e => setTo(e.target.value)} aria-label="To" />
        <input placeholder="Voucher, ref or narration" value={q} onChange={e => setQ(e.target.value)} aria-label="Search" />
      </div>
      <Panel><DataTable rows={list.data} onRow={r => setOpen(r.id)} cols={[
        { key: 'no', label: 'Voucher', primary: true, render: r => <span className="mono">{r.no}</span> }, { key: 'txn_date', label: 'Date', render: r => day(r.txn_date) },
        { key: 'type', label: 'Type' }, { key: 'company', label: 'Co', hideSmall: true, render: r => co(r.company) }, { key: 'ref', label: 'Ref', hideSmall: true },
        { key: 'narration', label: 'Narration' }, { key: 'rev', label: '', render: r => (r.reversed_by_no ? <span className="pill bad">Reversed</span> : r.reverses_no ? <span className="pill info">Reversal</span> : '') },
      ]} /></Panel>
      {open && <VoucherDetail id={open} onClose={() => { setOpen(null); list.reload(); }} />}
      {form && <Modal wide title={{ receipt: 'Customer receipt', expense: 'Expense', contra: 'Contra entry', journal: 'Journal voucher', ic: 'Inter-company funds' }[form]} onClose={() => setForm('')}>
        {form === 'receipt' && <ReceiptForm done={() => { setForm(''); list.reload(); }} />}
        {form === 'expense' && <ExpenseForm done={() => { setForm(''); list.reload(); }} />}
        {form === 'contra' && <ContraForm done={() => { setForm(''); list.reload(); }} />}
        {form === 'journal' && <JournalForm done={() => { setForm(''); list.reload(); }} />}
        {form === 'ic' && <IcForm done={() => { setForm(''); list.reload(); }} />}
      </Modal>}
    </Page>
  );
}

function VoucherDetail({ id, onClose }: { id: number; onClose: () => void }) {
  const { can } = useAuth();
  const { data: j } = useLoad<any>(() => api(`/accounts/vouchers/${id}`), [id]);
  const [reason, setReason] = useState('');
  const [run, busy] = useAction();
  if (!j) return null;
  const dr = j.lines.reduce((s: number, l: any) => s + (l.debit || 0), 0);
  return <Modal wide title={`${j.type} ${j.no}`} onClose={onClose} footer={can('reverse', 'acc') && !j.reversed_by_no && !j.reverses_no && <>
    <input placeholder="Reason for reversal" value={reason} onChange={e => setReason(e.target.value)} aria-label="Reason" />
    <ConfirmBtn label="Reverse voucher" busy={busy} onConfirm={async () => { if (await run(() => api('/accounts/reverse', { body: { company: j.company, journal_no: j.no, reason } }), (x: any) => `Reversed by ${x.reversal_no}.`)) onClose(); }} /></>}>
    <Kv items={[['Company', co(j.company)], ['Date', day(j.txn_date)], ['Ref', j.ref], ['Narration', j.narration], ['Approval', j.approval_no], ['Reverses', j.reverses_no], ['Reversed by', j.reversed_by_no]]} />
    <DataTable rows={j.lines} keyOf={r => r.line_no} cols={[{ key: 'account', label: 'Account', primary: true, render: r => `${r.account} ${r.account_name || ''}` },
      { key: 'branch_id', label: 'Branch', hideSmall: true }, { key: 'party', label: 'Party', hideSmall: true },
      { key: 'debit', label: 'Debit', align: 'r', render: r => (r.debit ? inr(r.debit) : '') }, { key: 'credit', label: 'Credit', align: 'r', render: r => (r.credit ? inr(r.credit) : '') }]} />
    <p className="r"><b>Total {inr(dr)}</b></p>
    <EvidencePanel refNo={j.ref || j.no} company={j.company} />
  </Modal>;
}

function useCo() { const { org } = useAuth(); return useState(org?.companies[0]?.code || 'KG'); }
const msg = (x: any) => (x.journal_no ? `Posted ${x.journal_no}.` : x.no ? `Posted ${x.no}.` : '');

function ReceiptForm({ done }: { done: () => void }) {
  const [company, setCompany] = useCo();
  const [f, setF] = useState({ customer_id: '', amount: '', mode: 'bank', reference: '', date: today(), branch_id: '', against_invoice: '' });
  const [cq, setCq] = useState('');
  const { data: custs } = useLoad<any[]>(() => api(`/customers?company=${company}&q=${encodeURIComponent(cq)}`), [company, cq]);
  const [run, busy] = useAction();
  return <div className="form grid2">
    <CompanySelect value={company} onChange={setCompany} />
    <Field label="Find customer"><input value={cq} onChange={e => setCq(e.target.value)} placeholder="Name, mobile or code" /></Field>
    <Field label="Customer" wide><select value={f.customer_id} onChange={e => setF({ ...f, customer_id: e.target.value })}><option value="">Choose…</option>{(custs || []).map(c => <option key={c.id} value={c.id}>{c.name} · {c.id}{c.balance ? ` · due ${inr(c.balance)}` : ''}</option>)}</select></Field>
    <Field label="Amount (₹)"><input inputMode="decimal" value={f.amount} onChange={e => setF({ ...f, amount: e.target.value })} /></Field>
    <Field label="Mode"><select value={f.mode} onChange={e => setF({ ...f, mode: e.target.value })}>{['cash', 'upi', 'card', 'bank'].map(m => <option key={m} value={m}>{m.toUpperCase()}</option>)}</select></Field>
    {f.mode !== 'cash' && <Field label="UTR / reference"><input value={f.reference} onChange={e => setF({ ...f, reference: e.target.value })} /></Field>}
    <Field label="Date"><input type="date" value={f.date} onChange={e => setF({ ...f, date: e.target.value })} /></Field>
    <BranchSelect value={f.branch_id} onChange={v => setF({ ...f, branch_id: v })} company={company} label="Received at (blank = HO)" />
    <Field label="Against bill no. (optional)" hint="Otherwise oldest bills first"><input value={f.against_invoice} onChange={e => setF({ ...f, against_invoice: e.target.value })} /></Field>
    <div className="row"><Btn kind="pri" busy={busy} onClick={async () => { if (await run(() => api('/accounts/receipts', { body: { ...f, company, amount: Number(f.amount), against_invoice: f.against_invoice || undefined } }), msg)) done(); }}>Post receipt</Btn></div>
  </div>;
}

function ExpenseForm({ done }: { done: () => void }) {
  const [company, setCompany] = useCo();
  const [f, setF] = useState({ account: '', amount: '', mode: 'bank', narration: '', date: today(), branch_id: '' });
  const { data: accts } = useLoad<any[]>(() => api(`/accounts?company=${company}`), [company]);
  const [run, busy] = useAction();
  return <div className="form grid2">
    <CompanySelect value={company} onChange={setCompany} />
    <Field label="Expense head"><select value={f.account} onChange={e => setF({ ...f, account: e.target.value })}><option value="">Choose…</option>{(accts || []).filter(a => a.grp === 'X' && !a.is_control && a.active).map(a => <option key={a.code} value={a.code}>{a.code} {a.name}</option>)}</select></Field>
    <Field label="Amount (₹)"><input inputMode="decimal" value={f.amount} onChange={e => setF({ ...f, amount: e.target.value })} /></Field>
    <Field label="Paid from"><select value={f.mode} onChange={e => setF({ ...f, mode: e.target.value })}><option value="bank">Bank</option><option value="cash">Cash</option></select></Field>
    <Field label="Date"><input type="date" value={f.date} onChange={e => setF({ ...f, date: e.target.value })} /></Field>
    <BranchSelect value={f.branch_id} onChange={v => setF({ ...f, branch_id: v })} company={company} label="Branch (blank = HO)" />
    <Field label="Narration" wide><input value={f.narration} onChange={e => setF({ ...f, narration: e.target.value })} placeholder="What was paid and why" /></Field>
    <p className="muted">Attach the bill on the approval or the voucher afterwards — approvers cannot approve expenses without evidence.</p>
    <div className="row"><Btn kind="pri" busy={busy} onClick={async () => { if (await run(() => api('/accounts/expenses', { body: { ...f, company, amount: Number(f.amount) } }), msg)) done(); }}>Submit expense</Btn></div>
  </div>;
}

function ContraForm({ done }: { done: () => void }) {
  const [company, setCompany] = useCo();
  const [f, setF] = useState({ kind: 'deposit', amount: '', branch_id: '', reference: '', date: today() });
  const [run, busy] = useAction();
  return <div className="form grid2">
    <CompanySelect value={company} onChange={setCompany} />
    <Field label="Entry"><select value={f.kind} onChange={e => setF({ ...f, kind: e.target.value })}><option value="deposit">Cash deposited to bank</option><option value="withdraw">Cash withdrawn from bank</option><option value="settle">UPI & card settlement to bank</option></select></Field>
    <Field label="Amount (₹)"><input inputMode="decimal" value={f.amount} onChange={e => setF({ ...f, amount: e.target.value })} /></Field>
    <BranchSelect value={f.branch_id} onChange={v => setF({ ...f, branch_id: v })} company={company} label="Branch (blank = HO)" />
    <Field label="Slip / reference"><input value={f.reference} onChange={e => setF({ ...f, reference: e.target.value })} /></Field>
    <Field label="Date"><input type="date" value={f.date} onChange={e => setF({ ...f, date: e.target.value })} /></Field>
    <div className="row"><Btn kind="pri" busy={busy} onClick={async () => { if (await run(() => api('/accounts/contra', { body: { ...f, company, amount: Number(f.amount) } }), msg)) done(); }}>Post</Btn></div>
  </div>;
}

function JournalForm({ done }: { done: () => void }) {
  const [company, setCompany] = useCo();
  const [narration, setNarration] = useState('');
  const [date, setDate] = useState(today());
  const [lines, setLines] = useState([{ account: '', debit: '', credit: '', branch_id: '' }, { account: '', debit: '', credit: '', branch_id: '' }]);
  const { data: accts } = useLoad<any[]>(() => api(`/accounts?company=${company}`), [company]);
  const [run, busy] = useAction();
  const dr = lines.reduce((s, l) => s + (Number(l.debit) || 0), 0), cr = lines.reduce((s, l) => s + (Number(l.credit) || 0), 0);
  const upd = (i: number, k: string, v: string) => setLines(ls => ls.map((l, j) => (j === i ? { ...l, [k]: v } : l)));
  return <div className="form">
    <div className="grid2"><CompanySelect value={company} onChange={setCompany} /><Field label="Date"><input type="date" value={date} onChange={e => setDate(e.target.value)} /></Field></div>
    {lines.map((l, i) => <div className="jv-line" key={i}>
      <select value={l.account} onChange={e => upd(i, 'account', e.target.value)} aria-label="Account"><option value="">Account…</option>{(accts || []).filter(a => !a.is_control && a.active).map(a => <option key={a.code} value={a.code}>{a.code} {a.name}</option>)}</select>
      <input inputMode="decimal" placeholder="Debit" value={l.debit} onChange={e => upd(i, 'debit', e.target.value)} aria-label="Debit" />
      <input inputMode="decimal" placeholder="Credit" value={l.credit} onChange={e => upd(i, 'credit', e.target.value)} aria-label="Credit" />
      {lines.length > 2 && <button className="x" onClick={() => setLines(ls => ls.filter((_, j) => j !== i))} aria-label="Remove line">×</button>}
    </div>)}
    <div className="row"><Btn kind="link" onClick={() => setLines(ls => [...ls, { account: '', debit: '', credit: '', branch_id: '' }])}>+ Line</Btn>
      <span className={Math.abs(dr - cr) > 0.009 ? 'warn-text' : ''}>Debit {inr(dr)} · Credit {inr(cr)}</span></div>
    <Field label="Narration" wide><input value={narration} onChange={e => setNarration(e.target.value)} /></Field>
    <p className="muted">Control accounts (cash, bank, receivables, payables, stock, GST) are posted only by their own documents and are not listed.</p>
    <div className="row"><Btn kind="pri" busy={busy} disabled={Math.abs(dr - cr) > 0.009 || !dr} onClick={async () => { if (await run(() => api('/accounts/journals', { body: { company, date, narration, lines: lines.filter(l => l.account).map(l => ({ ...l, debit: Number(l.debit) || 0, credit: Number(l.credit) || 0 })) } }), msg)) done(); }}>Submit journal</Btn></div>
  </div>;
}

function IcForm({ done }: { done: () => void }) {
  const [f, setF] = useState({ from_company: 'KG', to_company: 'ZE', amount: '', reason: '' });
  const [run, busy] = useAction();
  return <div className="form grid2">
    <Field label="Paying company"><select value={f.from_company} onChange={e => setF({ ...f, from_company: e.target.value })}><option value="KG">Kiswa</option><option value="ZE">Zaibus</option></select></Field>
    <Field label="Receiving company"><select value={f.to_company} onChange={e => setF({ ...f, to_company: e.target.value })}><option value="KG">Kiswa</option><option value="ZE">Zaibus</option></select></Field>
    <Field label="Amount (₹)"><input inputMode="decimal" value={f.amount} onChange={e => setF({ ...f, amount: e.target.value })} /></Field>
    <Field label="Reason" wide><input value={f.reason} onChange={e => setF({ ...f, reason: e.target.value })} /></Field>
    <p className="muted">Needs CFO approval. Both companies' books get matching due-to / due-from entries.</p>
    <div className="row"><Btn kind="pri" busy={busy} onClick={async () => { if (await run(() => api('/accounts/intercompany', { body: { ...f, amount: Number(f.amount) } }), () => '')) done(); }}>Request transfer</Btn></div>
  </div>;
}
