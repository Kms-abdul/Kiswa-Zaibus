/* MSEA sponsorship: vouchers that let sponsored students take items without paying, and what MSEA owes each company. */
import { useState } from 'react';
import { api } from '../api';
import { useAuth } from '../auth';
import { co, day, inr } from '../format';
import { Btn, CompanySelect, ConfirmBtn, DataTable, Field, Kv, Modal, Page, Panel, Pill, StudentSearch, Tabs, useAction, useLoad, useUrlState } from '../ui/kit';

export default function Msea() {
  const { can } = useAuth();
  const [tab, setTab] = useUrlState<'vouchers' | 'billing'>('tab', 'vouchers');
  const [status, setStatus] = useState('');
  const list = useLoad<any[]>(() => api(`/msea/vouchers${status ? `?status=${encodeURIComponent(status)}` : ''}`), [status]);
  const billing = useLoad<any[]>(() => (tab === 'billing' ? api('/msea/billing') : Promise.resolve([])), [tab]);
  const [open, setOpen] = useState<string | null>(null);
  const [create, setCreate] = useState(false);
  return (
    <Page title="MSEA sponsorship" sub="Vouchers for sponsored students. The counter bills against the voucher; MSEA pays the company later."
      actions={can('add', 'msea') && <Btn kind="pri" onClick={() => setCreate(true)}>New voucher</Btn>}>
      <Tabs value={tab} onChange={setTab} tabs={[['vouchers', 'Vouchers'], ['billing', 'Billing to MSEA']]} />
      {tab === 'vouchers' ? <>
        <div className="filters"><select value={status} onChange={e => setStatus(e.target.value)} aria-label="Status">
          <option value="">All statuses</option>{['Pending approval', 'Approved', 'Partly used', 'Fully used', 'Expired', 'Cancelled', 'Rejected'].map(s => <option key={s}>{s}</option>)}</select></div>
        <Panel><DataTable rows={list.data} onRow={r => setOpen(r.no)} keyOf={r => r.no} cols={[
          { key: 'no', label: 'Voucher', primary: true, render: r => <span className="mono">{r.no}</span> },
          { key: 'student_name', label: 'Student', render: r => <>{r.student_name} <span className="muted">{r.adm_no}</span></> },
          { key: 'company', label: 'Company', render: r => co(r.company) },
          { key: 'max_value', label: 'Limit', align: 'r', render: r => inr(r.max_value) },
          { key: 'used_value', label: 'Used', align: 'r', render: r => inr(r.used_value) },
          { key: 'valid_to', label: 'Valid to', hideSmall: true, render: r => day(r.valid_to) },
          { key: 'status', label: 'Status', render: r => <Pill s={r.status} /> },
        ]} /></Panel>
      </> : (billing.data || []).map(b => (
        <Panel key={b.company} title={<>{co(b.company)} · MSEA owes {inr(b.outstanding)}</>}>
          <DataTable rows={b.invoices} keyOf={r => r.no} cols={[{ key: 'no', label: 'Bill', primary: true }, { key: 'inv_date', label: 'Date', render: r => day(r.inv_date) },
            { key: 'adm_no', label: 'Student' }, { key: 'msea_voucher_no', label: 'Voucher' }, { key: 'total', label: 'Total', align: 'r', render: r => inr(r.total) },
            { key: 'balance', label: 'Due', align: 'r', render: r => inr(r.balance) }]} empty="Nothing outstanding." />
        </Panel>))}
      {open && <VoucherDetail no={open} onClose={() => { setOpen(null); list.reload(); }} />}
      {create && <NewVoucher onClose={() => { setCreate(false); list.reload(); }} />}
    </Page>
  );
}

function VoucherDetail({ no, onClose }: { no: string; onClose: () => void }) {
  const { can } = useAuth();
  const { data: v } = useLoad<any>(() => api(`/msea/vouchers/${encodeURIComponent(no)}`), [no]);
  const [reason, setReason] = useState('');
  const [run, busy] = useAction();
  if (!v) return null;
  return (
    <Modal wide title={<>Voucher {v.no} <Pill s={v.status} /></>} onClose={onClose}>
      <Kv items={[['Student', `${v.student?.name} (${v.adm_no}) · Class ${v.student?.class_name} · ${v.student?.category}`], ['Company', co(v.company)], ['Scope', v.scope],
        ['Limit', inr(v.max_value)], ['Used', inr(v.used_value)], ['Valid to', day(v.valid_to)],
        ['Entitlement', Object.entries(v.entitlement || {}).map(([k, q]) => `${k} × ${q} (used ${v.used_qty?.[k] || 0})`).join(', ')], ['Remarks', v.remarks]]} />
      <h4>Issues against this voucher</h4>
      <DataTable rows={v.issues} cols={[{ key: 'no', label: 'Bill', primary: true }, { key: 'inv_date', label: 'Date', render: r => day(r.inv_date) },
        { key: 'total', label: 'Value', align: 'r', render: r => inr(r.total) }, { key: 'status', label: 'Status', render: r => <Pill s={r.status} /> }]} empty="Not used yet." />
      {can('cancel', 'msea') && ['Approved', 'Pending approval'].includes(v.status) && (
        <div className="row"><input placeholder="Reason for cancelling" value={reason} onChange={e => setReason(e.target.value)} aria-label="Reason" />
          <ConfirmBtn label="Cancel voucher" busy={busy} onConfirm={async () => { if (await run(() => api(`/msea/vouchers/${encodeURIComponent(no)}/cancel`, { body: { reason } }), () => 'Voucher cancelled.')) onClose(); }} /></div>)}
    </Modal>
  );
}

function NewVoucher({ onClose }: { onClose: () => void }) {
  const { org } = useAuth();
  const [st, setSt] = useState<any>(null);
  const [company, setCompany] = useState(org?.companies[0]?.code || 'KG');
  const [ent, setEnt] = useState<[string, string][]>([['', '1']]);
  const [max, setMax] = useState('');
  const [validTo, setValidTo] = useState('');
  const [scope, setScope] = useState('Sponsored issue');
  const [remarks, setRemarks] = useState('');
  const { data: cats } = useLoad<any[]>(() => api(`/categories?company=${company}`), [company]);
  const [run, busy] = useAction();
  const save = async () => {
    const r = await run(() => api('/msea/vouchers', { body: { adm_no: st?.adm_no, company, max_value: Number(max), valid_to: validTo || undefined, scope, remarks,
      entitlement: Object.fromEntries(ent.filter(([k]) => k).map(([k, q]) => [k, Number(q) || 0])) } }), (x: any) => `Voucher ${x.no} created.`);
    if (r) onClose();
  };
  return (
    <Modal title="New MSEA voucher" onClose={onClose} footer={<><Btn onClick={onClose}>Cancel</Btn><Btn kind="pri" busy={busy} onClick={save} disabled={!st}>Create</Btn></>}>
      <div className="form">
        {st ? <p><b>{st.name}</b> {st.adm_no} · <Pill s={st.category} tone="info" /> <Btn kind="link" onClick={() => setSt(null)}>Change</Btn></p> : <StudentSearch onPick={setSt} />}
        <CompanySelect value={company} onChange={setCompany} />
        <Field label="Entitlement (category and quantity)" wide>
          {ent.map(([k, q], i) => <div className="row" key={i}>
            <select value={k} onChange={e => setEnt(x => x.map((y, j) => (j === i ? [e.target.value, y[1]] : y)))} aria-label="Category"><option value="">Category…</option>{(cats || []).map(c => <option key={c.name}>{c.name}</option>)}</select>
            <input inputMode="numeric" value={q} onChange={e => setEnt(x => x.map((y, j) => (j === i ? [y[0], e.target.value] : y)))} aria-label="Quantity" className="w-s" />
          </div>)}
          <Btn kind="link" onClick={() => setEnt(x => [...x, ['', '1']])}>+ Add line</Btn>
        </Field>
        <Field label="Maximum value (₹)"><input inputMode="decimal" value={max} onChange={e => setMax(e.target.value)} /></Field>
        <Field label="Valid to" hint="Default 60 days"><input type="date" value={validTo} onChange={e => setValidTo(e.target.value)} /></Field>
        <Field label="Scope"><input value={scope} onChange={e => setScope(e.target.value)} /></Field>
        <Field label="Remarks" wide><input value={remarks} onChange={e => setRemarks(e.target.value)} /></Field>
      </div>
    </Modal>
  );
}
