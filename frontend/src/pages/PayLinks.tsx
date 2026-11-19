/* Payment links and QR codes: create a link for a student's / customer's dues, one bill or an advance; share it by SMS /
   WhatsApp or show the QR code at the counter. When the parent pays, the receipt is posted automatically. */
import QRCode from 'qrcode';
import { useEffect, useState } from 'react';
import { api } from '../api';
import { useAuth } from '../auth';
import { co, day, inr, when } from '../format';
import { Btn, CompanySelect, ConfirmBtn, DataTable, Field, Kv, Modal, Page, Panel, Pill, StudentSearch, Tabs, useAction, useLoad, useToast } from '../ui/kit';

export function Qr({ text, size = 220 }: { text: string; size?: number }) {
  const [src, setSrc] = useState('');
  useEffect(() => { QRCode.toDataURL(text, { width: size, margin: 1, errorCorrectionLevel: 'M' }).then(setSrc).catch(() => setSrc('')); }, [text, size]);
  return src ? <img className="qr" src={src} width={size} height={size} alt={`QR code for ${text}`} /> : null;
}

export default function PayLinks() {
  const { can } = useAuth();
  const [status, setStatus] = useState('');
  const l = useLoad<any[]>(() => api(`/payments/links${status ? `?status=${status}` : ''}`), [status]);
  const [sel, setSel] = useState<any>(null);
  const [nw, setNw] = useState(false);
  return (
    <Page title="Payment links & QR" sub="Collect dues or advances without cash: the parent pays by UPI, card or net banking and the receipt posts automatically."
      actions={can('add', 'ctl.pay') && <Btn kind="pri" onClick={() => setNw(true)}>New payment link</Btn>}>
      <Tabs value={status} onChange={setStatus} tabs={[['', 'All'], ['Issued', 'Waiting'], ['Paid', 'Paid'], ['Expired', 'Expired'], ['Cancelled', 'Cancelled']]} />
      <Panel><DataTable rows={l.data} onRow={setSel} cols={[{ key: 'description', label: 'For', primary: true }, { key: 'customer_name', label: 'Student / customer' },
        { key: 'company', label: 'Co', hideSmall: true, render: r => co(r.company) }, { key: 'amount', label: 'Amount', align: 'r', render: r => inr(r.amount) },
        { key: 'expires_on', label: 'Expires', hideSmall: true, render: r => day(r.expires_on) }, { key: 'status', label: 'Status', render: r => <Pill s={r.status} tone={r.status === 'Issued' ? 'warn' : undefined} /> }]}
        empty="No payment links yet." /></Panel>
      {sel && <LinkDetail l={sel} onClose={() => { setSel(null); l.reload(); }} />}
      {nw && <NewLink onClose={x => { setNw(false); l.reload(); if (x) setSel(x); }} />}
    </Page>
  );
}

function LinkDetail({ l, onClose }: { l: any; onClose: () => void }) {
  const { can } = useAuth();
  const toast = useToast();
  const [reason, setReason] = useState('');
  const [run, busy] = useAction();
  const copy = async () => { try { await navigator.clipboard.writeText(l.short_url); toast('Link copied.', 'ok'); } catch { toast(l.short_url, 'info'); } };
  const wa = `https://wa.me/${l.mobile ? '91' + l.mobile : ''}?text=${encodeURIComponent(`Please pay ${inr(l.amount)} (${l.description}): ${l.short_url}`)}`;
  return <Modal title={<>Payment link <Pill s={l.status} /></>} onClose={onClose} footer={<>
    {l.status === 'Issued' && can('cancel', 'ctl.pay') && <><input placeholder="Reason" value={reason} onChange={e => setReason(e.target.value)} aria-label="Reason" />
      <ConfirmBtn label="Cancel link" busy={busy} onConfirm={async () => { if (await run(() => api(`/payments/links/${l.id}/cancel`, { body: { reason } }), () => 'Link cancelled.')) onClose(); }} /></>}
    <Btn onClick={onClose}>Close</Btn></>}>
    <div className="qr-wrap">{l.status === 'Issued' && <Qr text={l.short_url} />}
      <Kv items={[['Amount', <b>{inr(l.amount)}</b>], ['For', l.description], ['Customer', l.customer_name], ['Bill', l.invoice_no], ['Expires', day(l.expires_on)],
        ['Paid', l.paid_at ? `${when(l.paid_at)} · receipt ${l.receipt_no}` : undefined], ['Mode', l.env]]} /></div>
    <p className="mono small-break">{l.short_url}</p>
    {l.status === 'Issued' && <div className="row"><Btn onClick={copy}>Copy link</Btn><a className="ghost btnlike" href={wa} target="_blank" rel="noreferrer">Send on WhatsApp</a></div>}
  </Modal>;
}

function NewLink({ onClose }: { onClose: (x?: any) => void }) {
  const { org } = useAuth();
  const [company, setCompany] = useState(org?.companies[0]?.code || 'KG');
  const [st, setSt] = useState<any>(null);
  const [f, setF] = useState({ purpose: 'dues', amount: '', invoice_no: '', description: '', expires_on: '', mobile: '' });
  const [run, busy] = useAction();
  const save = async () => {
    const r = await run(() => api('/payments/links', { body: { ...f, company, adm_no: st?.adm_no, amount: Number(f.amount), invoice_no: f.invoice_no || undefined, expires_on: f.expires_on || undefined } }),
      () => 'Link created. Share it or show the QR code.');
    if (r) onClose(r);
  };
  return <Modal title="New payment link" onClose={() => onClose()} footer={<><Btn onClick={() => onClose()}>Cancel</Btn><Btn kind="pri" busy={busy} disabled={!st} onClick={save}>Create link</Btn></>}>
    <div className="form">
      <CompanySelect value={company} onChange={setCompany} />
      {st ? <p><b>{st.name}</b> {st.adm_no} · parent {st.parent_mobile} <Btn kind="link" onClick={() => setSt(null)}>Change</Btn></p> : <StudentSearch onPick={setSt} />}
      <div className="grid2">
        <Field label="For"><select value={f.purpose} onChange={e => setF({ ...f, purpose: e.target.value })}><option value="dues">Outstanding dues</option><option value="invoice">One bill</option><option value="advance">Advance (kept in wallet)</option></select></Field>
        <Field label="Amount (₹)"><input inputMode="decimal" value={f.amount} onChange={e => setF({ ...f, amount: e.target.value })} /></Field>
        {f.purpose === 'invoice' && <Field label="Bill number"><input value={f.invoice_no} onChange={e => setF({ ...f, invoice_no: e.target.value })} /></Field>}
        <Field label="Expires on" hint="Default 7 days"><input type="date" value={f.expires_on} onChange={e => setF({ ...f, expires_on: e.target.value })} /></Field>
        <Field label="Send SMS to" hint="Default: parent mobile"><input inputMode="tel" value={f.mobile} onChange={e => setF({ ...f, mobile: e.target.value })} /></Field>
      </div>
      <Field label="Description" wide><input value={f.description} onChange={e => setF({ ...f, description: e.target.value })} placeholder="e.g. Blazer balance, Term 2 books" /></Field>
    </div>
  </Modal>;
}
