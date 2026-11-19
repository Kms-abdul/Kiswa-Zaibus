/* Public page a parent opens from a payment-link SMS or QR code: /pay/<link id>. No sign-in. */
import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { api, errText } from '../api';
import { inr } from '../format';
import { ErrorNote, Pill } from '../ui/kit';
import { Qr } from './PayLinks';

export default function PayPublic() {
  const { id = '' } = useParams();
  const [l, setL] = useState<any>(null);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  const load = () => api(`/pay/${encodeURIComponent(id)}`).then(setL).catch(e => setErr(errText(e)));
  useEffect(() => { load(); document.title = 'Payment · Kiswa-Zaibus'; }, [id]);  // eslint-disable-line react-hooks/exhaustive-deps
  const pay = async () => {
    setBusy(true); setErr('');
    try { await api(`/pay/${encodeURIComponent(id)}/simulate`, { body: {} }); await load(); } catch (e) { setErr(errText(e)); } finally { setBusy(false); }
  };
  return (
    <div className="shop">
      <header className="shop-top"><span className="mark">KZ</span><div><b>Kiswa-Zaibus</b><small>Secure payment</small></div></header>
      <div className="shop-body">
        <div className="panel narrow paycard">
          {!l && !err && <p className="muted">Loading…</p>}
          <ErrorNote err={err} />
          {l && <>
            <h2>{l.company}</h2>
            <p>{l.description}</p>
            <div className="grand">Amount <b>{inr(l.amount)}</b></div>
            <p><Pill s={l.status === 'Issued' ? 'Waiting for payment' : l.status} tone={l.status === 'Paid' ? 'ok' : l.status === 'Issued' ? 'warn' : 'bad'} /> {l.status === 'Issued' && <span className="muted">Pay before {l.expires_on}</span>}</p>
            {l.status === 'Paid' && <p className="note ok">Thank you. Payment received — receipt {l.receipt_no}.</p>}
            {l.status === 'Issued' && (l.simulated
              ? <><p className="note">Test mode: no real money moves.</p><button className="pri wide" disabled={busy} onClick={pay}>{busy ? 'Please wait…' : `Pay ${inr(l.amount)} (test)`}</button></>
              : <><a className="pri wide btnlike" href={l.short_url}>Pay {inr(l.amount)} securely</a><div className="qr-wrap"><Qr text={l.short_url} size={180} /><span className="muted">Or scan with any UPI app</span></div></>)}
            <p className="muted">Questions? Call {l.phone}.</p>
          </>}
        </div>
      </div>
    </div>
  );
}
