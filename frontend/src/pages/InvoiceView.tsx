/* Printable GST tax invoice (also the receipt shown after billing). Uses the browser print dialog; the print
   stylesheet hides everything else, so it fits an A5/A4 page or an 80 mm thermal printer. */
import { api } from '../api';
import { inr } from '../format';
import { useLoad } from '../ui/kit';

function words(n: number): string {
  const a = ['', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight', 'Nine', 'Ten', 'Eleven', 'Twelve', 'Thirteen', 'Fourteen', 'Fifteen', 'Sixteen', 'Seventeen', 'Eighteen', 'Nineteen'];
  const b = ['', '', 'Twenty', 'Thirty', 'Forty', 'Fifty', 'Sixty', 'Seventy', 'Eighty', 'Ninety'];
  const two = (x: number) => (x < 20 ? a[x] : b[Math.floor(x / 10)] + (x % 10 ? ' ' + a[x % 10] : ''));
  const three = (x: number) => (x >= 100 ? a[Math.floor(x / 100)] + ' Hundred' + (x % 100 ? ' ' + two(x % 100) : '') : two(x));
  if (!n) return 'Zero';
  const cr = Math.floor(n / 1e7), lk = Math.floor((n % 1e7) / 1e5), th = Math.floor((n % 1e5) / 1e3), rest = n % 1e3;
  return [cr && three(cr) + ' Crore', lk && two(lk) + ' Lakh', th && two(th) + ' Thousand', rest && three(rest)].filter(Boolean).join(' ');
}

let posCache: any = null;   // receipt settings change rarely; one fetch per app load

export default function InvoiceView({ inv }: { inv: any }) {
  const { data: pub } = useLoad<any>(() => (posCache ? Promise.resolve(posCache) : api('/settings/public').then(x => (posCache = x))), []);
  const pos = pub?.pos || {};
  const w = pos.printer || '80';
  const c = inv.company_info || {};
  const st = inv.student_snapshot || {};
  const tax = (inv.cgst || 0) + (inv.sgst || 0) + (inv.igst || 0);
  return (
    <div className={`invoice print-area w${w}`}>
      <div className="inv-head">
        <div><b className="inv-co">{c.name}</b><div>{c.address}</div><div>GSTIN {c.gstin || '—'} · {c.phone}</div></div>
        <div className="r"><b>{inv.channel === 'sponsored' ? 'TAX INVOICE · SPONSORED ISSUE' : 'TAX INVOICE'}</b><div className="mono">{inv.no}</div>
          <div>{inv.inv_date} {String(inv.inv_time).slice(0, 5)}</div><div>{inv.branch_name}</div></div>
      </div>
      <div className="inv-to">
        {st.adm_no ? <><b>{st.name}</b> · Adm {st.adm_no} · Class {st.class_name}{st.section ? '-' + st.section : ''} · {st.category}</> : <b>{inv.customer_id}</b>}
        {inv.msea_voucher_no && <div>MSEA voucher {inv.msea_voucher_no} · issue {inv.issue_no} · <b>No cash collected from the student</b></div>}
      </div>
      <table className="inv-lines">
        <thead><tr><th>Item</th><th>HSN</th><th className="r">Qty</th><th className="r">Rate</th><th className="r">Disc</th><th className="r">GST</th><th className="r">Amount</th></tr></thead>
        <tbody>{inv.lines.map((l: any) => (
          <tr key={l.line_no}><td>{l.description}{l.delivered_qty < l.qty ? <small className="block">To deliver later: {l.qty - l.delivered_qty}</small> : null}
            {l.returned_qty ? <small className="block">Returned: {l.returned_qty}</small> : null}</td>
            <td>{l.hsn}</td><td className="r">{l.qty}</td><td className="r">{inr(l.rate)}</td><td className="r">{l.disc_pct ? l.disc_pct + '%' : ''}</td><td className="r">{l.gst_rate}%</td><td className="r">{inr(l.amount)}</td></tr>
        ))}</tbody>
      </table>
      <div className="inv-tot">
        <div>Taxable value <span>{inr(inv.taxable)}</span></div>
        {inv.igst ? <div>IGST <span>{inr(inv.igst)}</span></div> : <><div>CGST <span>{inr(inv.cgst)}</span></div><div>SGST <span>{inr(inv.sgst)}</span></div></>}
        {inv.round_off ? <div>Round off <span>{inr(inv.round_off)}</span></div> : null}
        <div className="grand">Total <span>{inr(inv.total)}</span></div>
        <small>Rupees {words(Math.round(inv.total))} only · tax {inr(tax)} included</small>
      </div>
      <div className="inv-pay">Paid: {inv.payments.map((p: any) => `${p.mode.toUpperCase()} ${inr(p.amount)}${p.ref ? ' (' + p.ref + ')' : ''}`).join(' + ')}
        {inv.balance ? ` · Balance due ${inr(inv.balance)}` : ''}</div>
      {inv.pending_reason && <div className="inv-pay">Pending delivery: {inv.pending_reason}</div>}
      <div className="inv-foot">Cashier {inv.cashier_name || '—'}{inv.reprints ? ` · Copy ${inv.reprints}` : ''}
        <div>{(inv.company === 'ZE' ? pos.policyZE : pos.policyKG) || 'Exchange within the return period with this bill and tags intact.'}</div>
        {pos.thanks && <div className="inv-thanks">{pos.thanks}</div>}</div>
    </div>
  );
}
