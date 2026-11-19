/* Parent online store (/shop). Sign in with the child's admission number + the mobile registered with the school (one-time code),
   pick items allowed for the child, choose a collection point, pay (Razorpay, or a simulated payment when no keys are configured),
   track the order and ask for cancellation / return. Phone-first layout. */
import { useEffect, useMemo, useState } from 'react';
import { api, ApiError, errText } from '../api';
import { co, inr, when } from '../format';
import { Btn, ErrorNote, Field, Modal, Pill, Tabs, useLoad, useToast } from '../ui/kit';

declare global { interface Window { Razorpay?: any } }

export default function Shop() {
  const [me, setMe] = useState<any>(undefined);
  const load = () => api('/shop/me').then(setMe).catch(() => setMe(null));
  useEffect(() => { load(); }, []);
  useEffect(() => { document.title = 'School store · Kiswa-Zaibus'; }, []);
  if (me === undefined) return <div className="boot">Loading…</div>;
  return (
    <div className="shop">
      <header className="shop-top"><span className="mark">KZ</span><div><b>Kiswa-Zaibus school store</b><small>Uniforms by Kiswa · Books by Zaibus</small></div>
        {me && <button className="link" onClick={async () => { await api('/shop/auth/logout', { body: {} }); setMe(null); }}>Sign out</button>}</header>
      <div className="shop-body">{me ? <Store me={me} /> : <ParentSignIn onDone={load} />}</div>
    </div>
  );
}

function ParentSignIn({ onDone }: { onDone: () => void }) {
  const [adm, setAdm] = useState('');
  const [mobile, setMobile] = useState('');
  const [ch, setCh] = useState<any>(null);
  const [otp, setOtp] = useState('');
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  const go = async (e: React.FormEvent) => {
    e.preventDefault(); setErr(''); setBusy(true);
    try {
      if (!ch) setCh(await api('/shop/auth/start', { body: { adm_no: adm, mobile } }));
      else { await api('/shop/auth/verify', { body: { challenge: ch.challenge, otp } }); onDone(); }
    } catch (x) { setErr(errText(x)); if (x instanceof ApiError && x.code === 'challenge_expired') setCh(null); } finally { setBusy(false); }
  };
  return (
    <form className="panel narrow" onSubmit={go}>
      <h2>Sign in</h2>
      {!ch ? <>
        <Field label="Student admission number"><input value={adm} onChange={e => setAdm(e.target.value.toUpperCase())} autoComplete="off" required /></Field>
        <Field label="Parent mobile registered with the school"><input inputMode="tel" value={mobile} onChange={e => setMobile(e.target.value)} required /></Field>
      </> : <>
        <p>We sent a 6-digit code to {ch.mobile_hint}.{ch.dev_otp && <> Test code: <b className="mono">{ch.dev_otp}</b></>}</p>
        <Field label="Code"><input inputMode="numeric" autoComplete="one-time-code" value={otp} onChange={e => setOtp(e.target.value)} required autoFocus /></Field>
      </>}
      <ErrorNote err={err} />
      <button className="pri wide" disabled={busy}>{busy ? 'Please wait…' : ch ? 'Verify' : 'Send code'}</button>
      {ch && <button type="button" className="link" onClick={() => { setCh(null); setOtp(''); }}>Use a different number</button>}
    </form>
  );
}

function Store({ me }: { me: any }) {
  const [tab, setTab] = useState<'shop' | 'orders'>('shop');
  const [adm, setAdm] = useState(me.children[0]?.adm_no || '');
  return <>
    <Tabs value={tab} onChange={setTab} tabs={[['shop', 'Shop'], ['orders', 'My orders']]} />
    {tab === 'shop' ? <>
      {me.children.length > 1 && <div className="kids">{me.children.map((c: any) => <button key={c.adm_no} className={c.adm_no === adm ? 'on' : ''} onClick={() => setAdm(c.adm_no)}><b>{c.name}</b><small>Class {c.class_name}{c.section ? '-' + c.section : ''}</small></button>)}</div>}
      {adm ? <Catalogue key={adm} adm={adm} child={me.children.find((c: any) => c.adm_no === adm)} onOrdered={() => setTab('orders')} /> : <p className="empty">No active student is linked to this mobile number.</p>}
    </> : <Orders />}
  </>;
}

function Catalogue({ adm, child, onOrdered }: { adm: string; child: any; onOrdered: () => void }) {
  const { data, err } = useLoad<any>(() => api(`/shop/catalog/${adm}`), [adm]);
  const [cart, setCart] = useState<Record<string, number>>({});
  const [cat, setCat] = useState('');
  const [checkout, setCheckout] = useState(false);
  const items: any[] = data?.items || [];
  const cats = useMemo(() => [...new Set(items.map(i => i.category))], [items]);
  const lines = items.filter(i => cart[i.item_id]);
  const net = (i: any) => i.net_price ?? i.price;
  const total = Math.round(lines.reduce((s, i) => s + net(i) * cart[i.item_id], 0));
  if (err) return <ErrorNote err={err} />;
  if (!data) return <p className="muted">Loading…</p>;
  if (data.sponsored) return <div className="note info">{data.message}</div>;
  const set = (id: string, q: number, max: number) => setCart(c => ({ ...c, [id]: Math.max(0, Math.min(max, q)) }));
  return <>
    <p className="muted">Items for <b>{child?.name}</b> · Class {child?.class_name}. Collect at the school counter you choose; returns within {data.return_days} days of collection.</p>
    <div className="seg">{['', ...cats].map(c => <button key={c} className={c === cat ? 'on' : ''} onClick={() => setCat(c)}>{c || 'All'}</button>)}</div>
    <div className="shop-grid">{items.filter(i => !cat || i.category === cat).map(i => (
      <div key={i.item_id} className="shop-card">
        <span className={'co ' + i.company}>{co(i.company)}</span>
        <b>{i.name}</b><span className="muted">{[i.size, i.category].filter(Boolean).join(' · ')}</span>
        <span className="price">{i.disc_pct > 0 && <s className="muted">{inr(i.price)}</s>} {inr(net(i))}</span>
        {i.disc_pct > 0 && <Pill s={`${i.scheme} · ${i.disc_pct}% off`} tone="ok" />}
        {i.max_qty <= 0 ? <Pill s={i.in_stock <= 0 ? 'Out of stock' : 'Limit reached'} tone="warn" /> : (
          <div className="qty"><button onClick={() => set(i.item_id, (cart[i.item_id] || 0) - 1, i.max_qty)} aria-label="Less">−</button><span>{cart[i.item_id] || 0}</span>
            <button onClick={() => set(i.item_id, (cart[i.item_id] || 0) + 1, i.max_qty)} aria-label="More">+</button></div>)}
      </div>))}</div>
    {lines.length > 0 && <div className="paybar"><span>{lines.reduce((s, i) => s + cart[i.item_id], 0)} items · <b>{inr(total)}</b></span><Btn kind="pri" onClick={() => setCheckout(true)}>Checkout</Btn></div>}
    {checkout && <Checkout adm={adm} points={data.collection_points} lines={lines.map(i => ({ ...i, qty: cart[i.item_id] }))} total={total} onClose={() => setCheckout(false)} onDone={() => { setCart({}); setCheckout(false); onOrdered(); }} />}
  </>;
}

async function loadRazorpay() {
  if (window.Razorpay) return;
  await new Promise<void>((ok, bad) => { const s = document.createElement('script'); s.src = 'https://checkout.razorpay.com/v1/checkout.js'; s.onload = () => ok(); s.onerror = () => bad(new Error('Could not load the payment window. Check the connection.')); document.head.appendChild(s); });
}

export async function payOrder(o: { id: number; no: string }, toast: (t: string, tone?: string) => void) {
  const info = await api(`/shop/orders/${o.id}/payment`);
  if (info.mode === 'simulated') { await api(`/shop/orders/${o.id}/pay`, { body: { simulate: true, method: 'upi' } }); return; }
  await loadRazorpay();
  await new Promise<void>((ok, bad) => {
    const rz = new window.Razorpay({ key: info.key_id, order_id: info.rzp_order_id, amount: info.amount_paise, currency: 'INR', name: info.company === 'KG' ? 'Kiswa Garments' : 'Zaibus Enterprises',
      description: `Order ${info.no}`, handler: async (r: any) => { try { await api(`/shop/orders/${o.id}/pay`, { body: r }); ok(); } catch (e) { bad(e); } },
      modal: { ondismiss: () => bad(new Error('Payment not completed. You can pay later from My orders.')) } });
    rz.on('payment.failed', (r: any) => toast(r.error?.description || 'Payment failed.', 'bad'));
    rz.open();
  });
}

function Checkout({ adm, points, lines, total, onClose, onDone }: { adm: string; points: any[]; lines: any[]; total: number; onClose: () => void; onDone: () => void }) {
  const toast = useToast();
  const [point, setPoint] = useState(points[0]?.code || '');
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  const place = async () => {
    setBusy(true); setErr('');
    try {
      const r = await api('/shop/orders', { body: { adm_no: adm, collect_at: point, lines: lines.map(l => ({ item_id: l.item_id, qty: l.qty })) } });
      for (const o of r.orders) await payOrder(o, toast);
      toast(r.orders.length > 1 ? 'Paid. Uniforms and books are separate orders (two companies).' : 'Paid. We will message you when it is ready to collect.', 'ok');
      onDone();
    } catch (e) { setErr(errText(e)); } finally { setBusy(false); }
  };
  return <Modal title="Checkout" onClose={onClose} footer={<><Btn onClick={onClose}>Back</Btn><Btn kind="pri" busy={busy} disabled={!point} onClick={place}>Pay {inr(total)}</Btn></>}>
    <ul className="cart">{lines.map(l => <li key={l.item_id}><div className="cart-name">{l.name} <span className="muted">{l.size}</span></div><div className="cart-ctrl"><span>× {l.qty}</span><span className="amt">{inr((l.net_price ?? l.price) * l.qty)}</span></div></li>)}</ul>
    <Field label="Collect at"><select value={point} onChange={e => setPoint(e.target.value)}>{points.map(p => <option key={p.code} value={p.code}>{p.name}{p.address ? ` — ${p.address}` : ''}</option>)}</select></Field>
    <p className="muted">Prices include GST. Stock is held for you for 2 hours until payment.</p>
    <ErrorNote err={err} />
  </Modal>;
}

function Orders() {
  const toast = useToast();
  const { data, reload } = useLoad<any[]>(() => api('/shop/orders'), []);
  const [req, setReq] = useState<any>(null);
  const [reason, setReason] = useState('');
  const [rq, setRq] = useState<Record<number, string>>({});
  const [busy, setBusy] = useState(false);
  if (!data) return <p className="muted">Loading…</p>;
  if (!data.length) return <p className="empty">No orders yet.</p>;
  const send = async () => {
    setBusy(true);
    try { await api(`/shop/orders/${req.o.id}/request`, { body: { type: req.type, reason, lines: req.o.lines.map((l: any) => ({ line_no: l.line_no, qty: Number(rq[l.line_no]) || 0 })) } }); toast('Request sent. The store will reply by SMS.', 'ok'); setReq(null); setReason(''); setRq({}); reload(); }
    catch (e) { toast(errText(e), 'bad'); } finally { setBusy(false); }
  };
  return <>
    {data.map(o => (
      <div key={o.id} className="panel order">
        <div className="panel-head"><h2><span className="mono">{o.no}</span> <Pill s={o.status} /></h2><b>{inr(o.total)}</b></div>
        <p className="muted">{o.student_name} · {co(o.company)} · placed {when(o.placed_at)}{o.invoice_no ? ` · bill ${o.invoice_no}` : ''}</p>
        <ul className="plain">{o.lines.map((l: any) => <li key={l.line_no}>{l.name} {l.size} × {l.qty}{l.returned_qty ? ` (returned ${l.returned_qty})` : ''}</li>)}</ul>
        <ol className="timeline">{(o.events || []).slice(-4).map((e: any, i: number) => <li key={i}><span className="muted">{when(e[0])}</span> {e[2]}</li>)}</ol>
        {o.requests.map((r: any) => <p key={r.no} className="note info">{r.type} {r.no}: {r.status}{r.decision_note ? ` — ${r.decision_note}` : ''}</p>)}
        <div className="row">
          {o.status === 'Order Placed' && <Btn kind="pri" onClick={async () => { try { await payOrder(o, toast); reload(); } catch (e) { toast(errText(e), 'bad'); } }}>Pay now</Btn>}
          {['Payment Confirmed', 'Picking', 'Quality Check'].includes(o.status) && <Btn onClick={() => setReq({ o, type: 'Cancellation' })}>Cancel order</Btn>}
          {o.status === 'Collected' && <Btn onClick={() => setReq({ o, type: 'Return' })}>Return items</Btn>}
        </div>
        {o.status === 'Ready for Collection' && <p className="note ok">Ready at the counter. Show the 6-digit code we sent by SMS.</p>}
      </div>))}
    {req && <Modal title={`${req.type} · ${req.o.no}`} onClose={() => setReq(null)} footer={<><Btn onClick={() => setReq(null)}>Back</Btn><Btn kind="pri" busy={busy} onClick={send}>Send request</Btn></>}>
      {req.type === 'Return' && req.o.lines.map((l: any) => l.collected_qty - l.returned_qty > 0 && <Field key={l.line_no} label={`${l.name} ${l.size || ''} · up to ${l.collected_qty - l.returned_qty}`}>
        <input inputMode="numeric" placeholder="0" value={rq[l.line_no] || ''} onChange={e => setRq(x => ({ ...x, [l.line_no]: e.target.value }))} /></Field>)}
      <Field label="Reason" wide><input value={reason} onChange={e => setReason(e.target.value)} placeholder={req.type === 'Return' ? 'e.g. wrong size' : 'e.g. bought at the counter'} /></Field>
      <p className="muted">Approved refunds go back to the original payment method.</p>
    </Modal>}
  </>;
}
