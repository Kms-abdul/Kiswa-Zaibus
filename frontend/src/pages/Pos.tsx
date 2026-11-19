/* Counter billing. One bill is for one company at the working location; a student (or an MSEA voucher) is required.
   Desktop: cart and payment side by side. Phone: stacked, with the total and the Bill button pinned to the bottom. */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { api, ApiError, errText } from '../api';
import { useAuth } from '../auth';
import { co as coName, inr } from '../format';
import { Btn, ConfirmBtn, ErrorNote, Field, ItemSearch, Kv, Modal, Page, Panel, Pill, StudentSearch, Tabs, useAction, useLoad, useToast } from '../ui/kit';
import InvoiceView from './InvoiceView';

type Line = { item_id: string; name: string; size?: string; price: number; qty: number; disc_pct: number; later: boolean; package_name?: string; unpriced?: boolean };
type Pay = { mode: string; amount: string; ref: string };
const MODES = [['cash', 'Cash'], ['upi', 'UPI'], ['card', 'Card'], ['bank', 'Bank transfer'], ['wallet', 'Wallet']];

export default function Pos() {
  const { org, location, myBranches } = useAuth();
  const toast = useToast();
  const companies = useMemo(() => [...new Set(myBranches.filter(b => b.location === location).map(b => b.company))], [myBranches, location]);
  const [company, setCompany] = useState<string>('');
  useEffect(() => { if (!companies.includes(company)) setCompany(companies[0] || ''); }, [companies, company]);
  const branch = myBranches.find(b => b.location === location && b.company === company);
  const shift = useLoad<any>(() => api('/pos/shift'), []);
  const [student, setStudent] = useState<any>(null);
  const [voucher, setVoucher] = useState<any>(null);
  const [voucherNo, setVoucherNo] = useState('');
  const [lines, setLines] = useState<Line[]>([]);
  const [quote, setQuote] = useState<any>(null);
  const [pays, setPays] = useState<Pay[]>([{ mode: 'cash', amount: '', ref: '' }]);
  const [tendered, setTendered] = useState('');
  const [pendingReason, setPendingReason] = useState('');
  const [overrideReason, setOverrideReason] = useState('');
  const [needOverride, setNeedOverride] = useState(false);
  const [err, setErr] = useState('');
  const [done, setDone] = useState<any>(null);
  const [pkgOpen, setPkgOpen] = useState(false);
  const [run, busy] = useAction();

  useEffect(() => { setLines([]); setQuote(null); setVoucher(null); }, [company]);
  useEffect(() => {
    const priced = lines.filter(l => !l.unpriced);
    if (!priced.length) { setQuote(null); return; }
    const t = setTimeout(() => {
      api('/pos/quote', { body: { company, sponsored: !!voucher, lines: priced.map(l => ({ item_id: l.item_id, qty: l.qty, disc_pct: l.disc_pct, package_name: l.package_name })) } })
        .then(q => { setQuote(q); setErr(''); }).catch(e => setErr(errText(e)));
    }, 200);
    return () => clearTimeout(t);
  }, [lines, company, voucher]);
  const total = quote?.totals?.total || 0;
  useEffect(() => { if (pays.length === 1 && !voucher) setPays([{ ...pays[0], amount: total ? String(total) : '' }]); }, [total]); // eslint-disable-line react-hooks/exhaustive-deps

  const add = (it: any, qty = 1, package_name?: string, disc = 0) => {
    if (it.company && it.company !== company) { toast(`${it.name} belongs to ${coName(it.company)}. Switch company to bill it.`, 'bad'); return; }
    if (it.unpriced || !it.price) { toast(`${it.name} has no selling price yet.`, 'bad'); return; }
    setLines(ls => {
      const i = ls.findIndex(l => l.item_id === it.id && !l.package_name && !package_name);
      if (i >= 0) return ls.map((l, k) => (k === i ? { ...l, qty: l.qty + qty } : l));
      return [...ls, { item_id: it.id, name: it.name, size: it.size, price: it.price, qty, disc_pct: disc, later: false, package_name }];
    });
  };
  const upd = (i: number, p: Partial<Line>) => setLines(ls => ls.map((l, k) => (k === i ? { ...l, ...p } : l)));
  const loadVoucher = async () => {
    try {
      const v = await api(`/msea/vouchers/${encodeURIComponent(voucherNo.trim())}`);
      if (v.company !== company) { toast(`Voucher ${v.no} is for ${coName(v.company)}.`, 'bad'); return; }
      setVoucher(v); setStudent(v.student);
    } catch (e) { toast(errText(e), 'bad'); }
  };
  const paid = pays.reduce((s, p) => s + (Number(p.amount) || 0), 0);
  const cashPart = pays.filter(p => p.mode === 'cash').reduce((s, p) => s + (Number(p.amount) || 0), 0);
  const change = Number(tendered) > 0 ? Number(tendered) - cashPart : 0;
  // what stops the bill, said plainly next to the button
  const blocker = !branch ? 'Choose a working location (top bar)' : !student && !voucher ? 'Choose the student first (F2)' : !lines.length ? 'Add items (F3)'
    : !voucher && total > 0 && Math.abs(paid - total) > 0.009 ? `Payments ${inr(paid)} do not match the total ${inr(total)}` : '';
  // focus now (so a fast scanner or typist loses nothing), and again after React re-renders the field
  const focus = (label: string) => { const f = () => (document.querySelector(`[aria-label="${label}"]`) as HTMLInputElement | null)?.focus(); f(); setTimeout(f, 30); };
  const pickStudent = (st: any) => { setStudent(st); focus('Find item'); };
  // F2 student · F3 item · F8 amount received · F9 or Ctrl+Enter bill
  const keys = useCallback((e: KeyboardEvent) => {
    if (document.body.classList.contains('modal-open')) return;
    if (e.key === 'F2') { e.preventDefault(); focus(student ? 'Find item' : 'Find student'); }
    else if (e.key === 'F3') { e.preventDefault(); focus('Find item'); }
    else if (e.key === 'F8') { e.preventDefault(); focus(cashPart > 0 ? 'Cash received' : 'Amount'); }
    else if (e.key === 'F9' || (e.key === 'Enter' && (e.ctrlKey || e.metaKey))) { e.preventDefault(); if (!blocker && !busy) run(submit); }
  }, [student, cashPart, blocker, busy]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { document.addEventListener('keydown', keys); return () => document.removeEventListener('keydown', keys); }, [keys]);
  // do not lose a bill in progress by closing or reloading the tab
  useEffect(() => {
    if (!lines.length) return;
    const w = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ''; };
    window.addEventListener('beforeunload', w);
    return () => window.removeEventListener('beforeunload', w);
  }, [lines.length]);
  const notes = useMemo(() => { const c = cashPart; return c > 0 ? [...new Set([c, ...[100, 200, 500, 1000, 2000].map(n => Math.ceil(c / n) * n)])].filter(v => v >= c).sort((a, b) => a - b).slice(0, 4) : []; }, [cashPart]);

  const submit = async () => {
    setErr('');
    if (!branch) { setErr('Choose a working location where this company sells.'); return; }
    const body: any = {
      branch_id: branch.id, lines: lines.map(l => ({ item_id: l.item_id, qty: l.qty, disc_pct: l.disc_pct, delivered_qty: l.later ? 0 : l.qty, package_name: l.package_name })),
      pending_reason: pendingReason || undefined, stock_override_reason: needOverride ? overrideReason : undefined,
    };
    if (voucher) body.voucher_no = voucher.no;
    else { body.adm_no = student?.adm_no; body.payments = pays.filter(p => Number(p.amount) > 0).map(p => ({ mode: p.mode, amount: Number(p.amount), ref: p.ref || undefined })); }
    try {
      const r: any = await api('/pos/invoices', { body });
      if (r.held) { toast(r.message, 'info'); reset(); return; }
      setDone(r); reset(); shift.reload();
    } catch (e) {
      if (e instanceof ApiError && e.code === 'negative_stock') setNeedOverride(true);
      if (e instanceof ApiError && e.code === 'shift_required') shift.reload();
      setErr(errText(e));
    }
  };
  const reset = () => { setLines([]); setQuote(null); setStudent(null); setVoucher(null); setVoucherNo(''); setPays([{ mode: 'cash', amount: '', ref: '' }]);
    setTendered(''); setPendingReason(''); setOverrideReason(''); setNeedOverride(false); };

  if (shift.data === null && !shift.loading) return <OpenShift onOpened={shift.reload} />;
  const anyLater = lines.some(l => l.later);
  return (
    <Page title="New bill" sub={<>{branch?.name || 'No location'} · shift {shift.data?.no || '…'} {shift.data?.status === 'Pending approval' && <Pill s="Close pending approval" />}</>}
      actions={<><Btn onClick={() => setPkgOpen(true)} disabled={!company}>Add package</Btn><CloseShift shift={shift.data} onClosed={shift.reload} /></>}>
      {companies.length > 1 && <Tabs value={company} onChange={setCompany} tabs={companies.map(c => [c, org?.companies.find(x => x.code === c)?.name || c] as [string, string])} />}
      <div className="pos">
        <div className="pos-main">
          <Panel title="Student">
            {voucher ? <div className="note info">MSEA voucher <b>{voucher.no}</b> · {voucher.scope} · up to {inr(voucher.max_value)} (used {inr(voucher.used_value)}) <Btn kind="link" onClick={() => { setVoucher(null); setStudent(null); }}>Remove</Btn></div>
              : student ? <div className="stu"><b>{student.name}</b> <span className="mono">{student.adm_no}</span> · Class {student.class_name}{student.section ? '-' + student.section : ''} · <Pill s={student.category} tone="info" /> {student.status !== 'Active' && <Pill s={student.status} />}
                  <Btn kind="link" onClick={() => { setStudent(null); focus('Find student'); }}>Change</Btn></div>
                : <StudentSearch onPick={pickStudent} placeholder="Admission no., name or parent mobile — or scan the ID card (F2)" />}
            {!voucher && !student && <div className="row"><input placeholder="or MSEA voucher no." value={voucherNo} onChange={e => setVoucherNo(e.target.value)} aria-label="Voucher number" /><Btn onClick={loadVoucher} disabled={!voucherNo}>Use voucher</Btn></div>}
          </Panel>
          <Panel title="Items">
            <ItemSearch company={company} onPick={it => { add(it); focus('Find item'); }} placeholder="Scan barcode / ISBN or type the item name (F3)" />
            {lines.length === 0 ? <p className="empty">Scan or search items to add them. A package adds a full uniform or book set at once.</p> : (
              <ul className="cart">{lines.map((l, i) => {
                const q = quote?.lines?.length === lines.length ? quote.lines[i] : null;
                return <li key={i}>
                  <div className="cart-name"><b>{l.name}</b> <span className="muted">{l.size}{l.package_name ? ' · ' + l.package_name : ''}</span>
                    {q?.scheme && Number(q.disc_pct) > 0 && <Pill s={`${q.scheme} · ${Number(q.disc_pct)}% off`} tone="ok" />}</div>
                  <div className="cart-ctrl">
                    <div className="qty"><button onClick={() => upd(i, { qty: Math.max(1, l.qty - 1) })} aria-label="Less">−</button><input inputMode="numeric" value={l.qty} onChange={e => upd(i, { qty: Math.max(1, Number(e.target.value) || 1) })} aria-label="Quantity" /><button onClick={() => upd(i, { qty: l.qty + 1 })} aria-label="More">+</button></div>
                    <label className="disc">Disc %<input inputMode="decimal" value={l.disc_pct || ''} onChange={e => upd(i, { disc_pct: Math.min(100, Number(e.target.value) || 0) })} /></label>
                    <label className="check"><input type="checkbox" checked={l.later} onChange={e => upd(i, { later: e.target.checked })} />Deliver later</label>
                    <span className="amt">{inr(q?.amount ?? l.price * l.qty)}</span>
                    <button className="x" onClick={() => setLines(ls => ls.filter((_, k) => k !== i))} aria-label="Remove">×</button>
                  </div>
                </li>;
              })}</ul>
            )}
            {anyLater && <Field label="Why are some items delivered later?"><input value={pendingReason} onChange={e => setPendingReason(e.target.value)} placeholder="e.g. size 32 not at the counter, from warehouse on Monday" /></Field>}
          </Panel>
        </div>
        <div className="pos-side">
          <Panel title="Payment">
            {quote && <Kv items={[['Items', inr(quote.totals.gross)], ['Discount', quote.totals.discount ? '− ' + inr(quote.totals.discount) : undefined], ['Taxable', inr(quote.totals.taxable)], ['GST', inr(quote.totals.tax)], ['Round off', quote.totals.round_off ? inr(quote.totals.round_off) : undefined]]} />}
            <div className="grand">Total <b>{inr(total)}</b></div>
            {voucher ? <p className="note info">Billed to MS Education Academy against the voucher. No cash is collected from the student.</p> : <>
              {pays.map((p, i) => (
                <div key={i} className="payrow">
                  <select value={p.mode} onChange={e => setPays(ps => ps.map((x, k) => (k === i ? { ...x, mode: e.target.value } : x)))} aria-label="Mode">{MODES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
                  <input inputMode="decimal" placeholder="Amount" value={p.amount} onChange={e => setPays(ps => ps.map((x, k) => (k === i ? { ...x, amount: e.target.value } : x)))} aria-label="Amount" />
                  {p.mode !== 'cash' && p.mode !== 'wallet' && <input placeholder="Ref / UTR" value={p.ref} onChange={e => setPays(ps => ps.map((x, k) => (k === i ? { ...x, ref: e.target.value } : x)))} aria-label="Reference" />}
                  {pays.length > 1 && <button className="x" onClick={() => setPays(ps => ps.filter((_, k) => k !== i))} aria-label="Remove payment">×</button>}
                </div>
              ))}
              <div className="row"><Btn kind="link" onClick={() => setPays(ps => [...ps, { mode: 'upi', amount: String(Math.max(0, total - paid) || ''), ref: '' }])}>+ Split payment</Btn>
                {Math.abs(paid - total) > 0.009 && total > 0 && <span className="warn-text">Payments {inr(paid)} ≠ total</span>}</div>
              {cashPart > 0 && <Field label="Cash received (for change) · F8">
                <input inputMode="decimal" value={tendered} onChange={e => setTendered(e.target.value)} aria-label="Cash received" placeholder={String(cashPart)} />
                <div className="notes" aria-label="Quick amounts">{notes.map(n => <button key={n} type="button" className={Number(tendered) === n ? 'on' : ''} onClick={() => setTendered(String(n))}>{n === cashPart ? 'Exact' : inr(n, 0)}</button>)}</div>
                {change > 0 && <b className="change">Give back {inr(change)}</b>}{Number(tendered) > 0 && change < -0.009 && <b className="bad-text">Short by {inr(-change)}</b>}</Field>}
            </>}
            {needOverride && <Field label="Stock override reason (a supervisor approves)"><input value={overrideReason} onChange={e => setOverrideReason(e.target.value)} placeholder="e.g. stock arrived, GRN pending" /></Field>}
            <ErrorNote err={err} />
          </Panel>
        </div>
      </div>
      <div className="paybar">
        <span className="pb-sum"><span>{lines.reduce((q, l) => q + l.qty, 0)} pcs · <b>{inr(total)}</b></span>{blocker && <small className="pb-why">{blocker}</small>}</span>
        <ConfirmBtn label="Clear" confirm="Clear bill?" onConfirm={reset} />
        <Btn kind="pri" busy={busy} disabled={!!blocker} onClick={() => run(submit)} title="F9 or Ctrl+Enter">Bill {inr(total, 0)}</Btn>
      </div>
      {done && <Modal wide title={`Bill ${done.no} posted`} onClose={() => setDone(null)} footer={<><Btn onClick={() => window.print()}>Print</Btn><Btn kind="pri" autoFocus onClick={() => { setDone(null); focus('Find student'); }}>Next customer</Btn></>}><InvoiceView inv={done} /></Modal>}
      {pkgOpen && <PackagePicker company={company} student={student} onClose={() => setPkgOpen(false)} onAdd={(items, name, disc) => { items.forEach(([it, q]) => add(it, q, name, disc)); setPkgOpen(false); }} />}
    </Page>
  );
}

function PackagePicker({ company, student, onClose, onAdd }: { company: string; student: any; onClose: () => void; onAdd: (items: [any, number][], name: string, disc: number) => void }) {
  const { data: pkgs } = useLoad<any[]>(() => api(`/packages?company=${company}`), [company]);
  const [pk, setPk] = useState<any>(null);
  const [opts, setOpts] = useState<Record<number, any[]>>({});
  const [pick, setPick] = useState<Record<number, string>>({});
  const toast = useToast();
  const choose = async (p: any) => {
    setPk(p);
    const o: Record<number, any[]> = {};
    for (const l of p.lines) {
      if (l.item_id) { o[l.line_no] = [await api(`/items/${l.item_id}`)]; continue; }
      const r = await api(`/items?company=${company}&category=${encodeURIComponent(l.category)}${p.school_id ? `&school_id=${p.school_id}` : ''}&size=200`);
      o[l.line_no] = r.rows.filter((it: any) => !it.unpriced && (!it.gender || it.gender === 'Unisex' || it.gender === p.gender || (student && ((student.gender === 'F') === (it.gender === 'Girls')))));
    }
    setOpts(o);
    setPick(Object.fromEntries(Object.entries(o).map(([k, v]) => [k, v.length === 1 ? v[0].id : ''])));
  };
  const add = () => {
    if (!pk.lines.every((l: any) => pick[l.line_no])) { toast('Choose a size for every line.', 'bad'); return; }
    onAdd(pk.lines.map((l: any) => [opts[l.line_no].find(x => x.id === pick[l.line_no]), l.qty]), pk.name, Number(pk.disc_pct) || 0);
  };
  return (
    <Modal title={pk ? pk.name : 'Add a package'} onClose={onClose} footer={pk && <><Btn onClick={() => setPk(null)}>Back</Btn><Btn kind="pri" onClick={add}>Add to bill</Btn></>}>
      {!pk ? (pkgs?.length ? <ul className="opt-list">{pkgs.map(p => <li key={p.id}><button onClick={() => choose(p)}><b>{p.name}</b><span className="muted">{p.lines.length} lines{p.disc_pct ? ` · ${p.disc_pct}% off` : ''}</span></button></li>)}</ul> : <p className="empty">No packages for this company.</p>)
        : pk.lines.map((l: any) => (
          <Field key={l.line_no} label={`${l.category || 'Item'} × ${l.qty}`}>
            <select value={pick[l.line_no] || ''} onChange={e => setPick(p => ({ ...p, [l.line_no]: e.target.value }))}>
              <option value="">Choose size…</option>{(opts[l.line_no] || []).map(it => <option key={it.id} value={it.id}>{it.name} {it.size} · {inr(it.price)}</option>)}
            </select>
          </Field>))}
    </Modal>
  );
}

function OpenShift({ onOpened }: { onOpened: () => void }) {
  const { location, myBranches } = useAuth();
  const cos = [...new Set(myBranches.filter(b => b.location === location).map(b => b.company))];
  const [open, setOpen] = useState<Record<string, string>>({});
  const [run, busy] = useAction();
  return (
    <Page title="Open your cash shift" sub="Count the float in the drawer for each company before the first bill.">
      <Panel>
        <div className="form narrow">
          {cos.map(c => <Field key={c} label={`${coName(c)} opening cash (₹)`}><input inputMode="decimal" value={open[c] || ''} onChange={e => setOpen(o => ({ ...o, [c]: e.target.value }))} /></Field>)}
          <Btn kind="pri" busy={busy} onClick={async () => { if (await run(() => api('/pos/shift/open', { body: { location, opening: Object.fromEntries(cos.map(c => [c, Number(open[c]) || 0])) } }), () => 'Shift opened.')) onOpened(); }}>Open shift</Btn>
        </div>
      </Panel>
    </Page>
  );
}

const DENOMS = [2000, 500, 200, 100, 50, 20, 10, 5, 2, 1];
function CloseShift({ shift, onClosed }: { shift: any; onClosed: () => void }) {
  const [open, setOpen] = useState(false);
  const [den, setDen] = useState<Record<string, Record<string, string>>>({});
  const [reason, setReason] = useState('');
  const [run, busy] = useAction();
  if (!shift || shift.status !== 'Open') return null;
  const live = shift.live || {};
  const counted = (c: string) => DENOMS.reduce((s, d) => s + d * (Number(den[c]?.[d]) || 0), 0);
  const close = async () => {
    const r = await run(() => api(`/pos/shift/${shift.id}/close`, { body: { denominations: Object.fromEntries(Object.keys(live).map(c => [c, Object.fromEntries(DENOMS.map(d => [d, Number(den[c]?.[d]) || 0]))])), reason } }),
      (x: any) => (x.approval ? '' : 'Shift closed.'));
    if (r) { setOpen(false); onClosed(); }
  };
  return <>
    <Btn onClick={() => setOpen(true)} title="End of day: count the drawer and close">Close shift…</Btn>
    {open && <Modal wide title={`Close shift ${shift.no}`} onClose={() => setOpen(false)} footer={<><Btn onClick={() => setOpen(false)}>Cancel</Btn><Btn kind="pri" busy={busy} onClick={close}>Close shift</Btn></>}>
      <div className="grid2">{Object.entries(live).map(([c, f]: [string, any]) => (
        <div key={c}>
          <h4>{coName(c)}</h4>
          <Kv items={[['Opening', inr(f.opening)], ['Cash sales', inr(f.cash_sales)], ['Cash receipts', inr(f.cash_receipts)], ['Cash refunds', '− ' + inr(f.cash_refunds)], ['Handed to clerk', '− ' + inr(f.handed_over)], ['Expected in drawer', <b>{inr(f.expected)}</b>]]} />
          <div className="denoms">{DENOMS.map(d => <label key={d}>₹{d}<input inputMode="numeric" value={den[c]?.[d] || ''} onChange={e => setDen(x => ({ ...x, [c]: { ...(x[c] || {}), [d]: e.target.value } }))} /></label>)}</div>
          <p>Counted <b>{inr(counted(c))}</b> · difference <b className={Math.abs(counted(c) - f.expected) > 0.009 ? 'warn-text' : ''}>{inr(counted(c) - f.expected)}</b></p>
        </div>))}
      </div>
      <Field label="Reason for any difference"><input value={reason} onChange={e => setReason(e.target.value)} /></Field>
    </Modal>}
  </>;
}
