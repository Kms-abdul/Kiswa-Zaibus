/* Shared UI building blocks. Every screen is built from these so desktop and phone behave the same way:
   tables turn into stacked cards under 720px, dialogs become bottom sheets, actions wrap. */
import { Component, createContext, useCallback, useContext, useEffect, useRef, useState, type ErrorInfo, type ReactNode } from 'react';
import { api, errText } from '../api';
import { useAuth } from '../auth';
import { friendly, inr, when } from '../format';
import { useSearchParams } from 'react-router-dom';

// ---------------------------------------------------------------- data loading
export function useLoad<T>(fn: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [err, setErr] = useState('');
  const [loading, setLoading] = useState(true);
  const seq = useRef(0);
  const reload = useCallback(() => {
    const n = ++seq.current;
    setLoading(true);
    fn().then(d => { if (n === seq.current) { setData(d); setErr(''); } })
      .catch(e => { if (n === seq.current) setErr(errText(e)); })
      .finally(() => { if (n === seq.current) setLoading(false); });
  }, deps); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { reload(); }, [reload]);
  return { data, err, loading, reload, setData };
}

/** A tab or filter kept in the address (?tab=…), so Back, refresh and shared links return to the same view. */
export function useUrlState<T extends string>(key: string, initial: T): [T, (v: T) => void] {
  const [sp, setSp] = useSearchParams();
  const v = (sp.get(key) as T) || initial;
  const set = useCallback((x: T) => {
    setSp(p => { const n = new URLSearchParams(p); if (x === initial) n.delete(key); else n.set(key, x); return n; }, { replace: true });
  }, [key, initial, setSp]);
  return [v, set];
}

// ---------------------------------------------------------------- layout
export function Page({ title, sub, actions, children }: { title: string; sub?: ReactNode; actions?: ReactNode; children: ReactNode }) {
  useEffect(() => { document.title = `${title} · Kiswa-Zaibus`; }, [title]);
  return (
    <>
      <div className="page-head"><div><h1>{title}</h1>{sub && <p className="muted">{sub}</p>}</div>{actions && <div className="actions">{actions}</div>}</div>
      {children}
    </>
  );
}

export function Panel({ title, actions, children, className = '' }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={'panel ' + className}>
      {(title || actions) && <div className="panel-head">{title && <h2>{title}</h2>}{actions && <div className="actions">{actions}</div>}</div>}
      {children}
    </section>
  );
}

export function Tabs<T extends string>({ tabs, value, onChange }: { tabs: [T, string][]; value: T; onChange: (v: T) => void }) {
  return <div className="seg" role="tablist">{tabs.map(([k, l]) => <button key={k} role="tab" aria-selected={k === value} className={k === value ? 'on' : ''} onClick={() => onChange(k)}>{l}</button>)}</div>;
}

export function Kv({ items }: { items: [string, ReactNode][] }) {
  return <dl className="kv">{items.filter(([, v]) => v !== undefined).map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v === '' || v == null ? '—' : (friendly(v) as ReactNode)}</dd></div>)}</dl>;
}

// ---------------------------------------------------------------- table that becomes cards on phones
export type Col<T> = { key: string; label: string; render?: (r: T) => ReactNode; align?: 'r'; primary?: boolean; hideSmall?: boolean };
export function DataTable<T extends Record<string, any>>({ rows, cols, onRow, empty = 'Nothing here yet.', keyOf, rowClass }:
  { rows: T[] | null | undefined; cols: Col<T>[]; onRow?: (r: T) => void; empty?: ReactNode; keyOf?: (r: T) => string | number; rowClass?: (r: T) => string }) {
  if (!rows) return <Skeleton />;
  if (!rows.length) return <div className="empty">{empty}</div>;
  return (
    <div className="table-wrap">
      <table className="rtable">
        <thead><tr>{cols.map(c => <th key={c.key} className={c.align === 'r' ? 'r' : ''}>{c.label}</th>)}</tr></thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={keyOf ? keyOf(r) : (r.id ?? r.no ?? i)} className={[onRow ? 'click' : '', rowClass ? rowClass(r) : ''].join(' ')} onClick={onRow ? () => onRow(r) : undefined}
                tabIndex={onRow ? 0 : undefined} onKeyDown={onRow ? e => { if (e.key === 'Enter') onRow(r); } : undefined}>
              {cols.map(c => (
                <td key={c.key} data-label={c.label} className={[c.align === 'r' ? 'r' : '', c.primary ? 'primary' : '', c.hideSmall ? 'hide-sm' : ''].join(' ')}>
                  {c.render ? c.render(r) : r[c.key] === '' || r[c.key] == null ? '—' : (friendly(r[c.key]) as ReactNode)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** An empty list that says what to do next. */
export function Empty({ title, hint, action }: { title: string; hint?: ReactNode; action?: ReactNode }) {
  return <div className="empty-state"><b>{title}</b>{hint && <p className="muted">{hint}</p>}{action && <div className="row center">{action}</div>}</div>;
}

export const Skeleton = () => <div className="skeleton" aria-busy="true"><span /><span /><span /></div>;

// ---------------------------------------------------------------- status and money
const TONES: Record<string, string> = {
  Posted: 'ok', Approved: 'ok', Active: 'ok', Received: 'ok', Closed: 'ok', Collected: 'ok', Released: 'ok', Refunded: 'ok', Invoiced: 'ok', 'Fully used': 'ok',
  Sent: 'ok', Verified: 'ok', Resolved: 'ok', Completed: 'ok', Applied: 'ok', 'Goods received': 'ok', Paid: 'ok',
  Pending: 'warn', 'Pending approval': 'warn', Held: 'warn', 'In transit': 'warn', Open: 'warn', Requested: 'warn', 'Awaiting clerk': 'warn', 'Partly used': 'warn',
  'Partly returned': 'warn', 'Partly received': 'warn', Returned: 'warn', Queued: 'warn', 'Owner Approved': 'info', Executed: 'info', 'PO approved': 'info',
  Requisition: 'info', Quotes: 'info', 'Payment Confirmed': 'info', Picking: 'info', 'Quality Check': 'info', Packed: 'info', 'Dispatched to Branch': 'info',
  'Ready for Collection': 'info', 'Order Placed': 'info', Draft: '', 'In Progress': 'info', Submitted: 'warn', 'Under Review': 'warn', Matched: 'ok', Shortage: 'bad', Excess: 'ok',
  Rejected: 'bad', Cancelled: 'bad', Locked: 'bad', Suspended: 'bad', Resigned: 'bad', Inactive: 'bad', Failed: 'bad', Disputed: 'bad', 'Payment failed': 'bad',
  Transferred: 'bad', Expired: 'bad',
};
export const Pill = ({ s, tone }: { s: ReactNode; tone?: string }) => <span className={'pill ' + (tone || TONES[String(s)] || '')}>{s}</span>;
export const Money = ({ v, dp }: { v: number | string | null | undefined; dp?: number }) => <span className="money">{inr(v as number, dp)}</span>;

// ---------------------------------------------------------------- forms
export function Field({ label, hint, children, wide }: { label: string; hint?: ReactNode; children: ReactNode; wide?: boolean }) {
  return <label className={'field' + (wide ? ' wide' : '')}><span>{label}</span>{children}{hint && <small className="muted">{hint}</small>}</label>;
}

export function Btn({ kind = 'ghost', busy, children, ...p }: { kind?: 'pri' | 'ghost' | 'danger' | 'link'; busy?: boolean } & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return <button {...p} className={kind + (p.className ? ' ' + p.className : '')} disabled={busy || p.disabled}>{busy ? 'Working…' : children}</button>;
}

/** Runs an action and shows its result; returns [run, busy]. */
export function useAction() {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const run = useCallback(async <T,>(fn: () => Promise<T>, done?: (r: T) => string | void) => {
    setBusy(true);
    try {
      const r = await fn();
      const msg = done ? done(r) : undefined;
      const appr = (r as any)?.approval || ((r as any)?.held ? (r as any).approval : null);
      if (appr?.no) toast(`Sent for approval (${appr.no}, needs ${appr.required_role}). It applies once approved.`, 'info');
      else if (msg) toast(msg, 'ok');
      return r;
    } catch (e) {
      toast(errText(e), 'bad');
      return undefined;
    } finally { setBusy(false); }
  }, [toast]);
  return [run, busy] as const;
}

export function ErrorNote({ err }: { err?: string }) { return err ? <div className="note bad" role="alert">{err}</div> : null; }

// ---------------------------------------------------------------- modal / bottom sheet
export function Modal({ title, onClose, children, wide, footer }: { title: ReactNode; onClose: () => void; children: ReactNode; wide?: boolean; footer?: ReactNode }) {
  useEffect(() => {
    const k = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', k);
    document.body.classList.add('modal-open');
    return () => { document.removeEventListener('keydown', k); document.body.classList.remove('modal-open'); };
  }, [onClose]);
  return (
    <div className="modal-bg" onMouseDown={e => { if (e.target === e.currentTarget) onClose(); }}>
      <div className={'modal' + (wide ? ' wide' : '')} role="dialog" aria-modal="true">
        <div className="modal-head"><h3>{title}</h3><button className="x" onClick={onClose} aria-label="Close">×</button></div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- toasts
type Toast = { id: number; text: string; tone: string };
const ToastCtx = createContext<(t: string, tone?: string) => void>(() => undefined);
export function ToastProvider({ children }: { children: ReactNode }) {
  const [list, setList] = useState<Toast[]>([]);
  const push = useCallback((text: string, tone = 'ok') => {
    const id = Date.now() + Math.random();
    setList(l => [...l, { id, text, tone }]);
    setTimeout(() => setList(l => l.filter(x => x.id !== id)), tone === 'bad' ? 12000 : 5000);
  }, []);
  const close = (id: number) => setList(l => l.filter(x => x.id !== id));
  return <ToastCtx.Provider value={push}>{children}<div className="toasts" aria-live="polite">{list.map(t =>
    <div key={t.id} className={'toast ' + t.tone} role={t.tone === 'bad' ? 'alert' : 'status'}><span>{t.text}</span>
      <button className="toast-x" onClick={() => close(t.id)} aria-label="Dismiss">×</button></div>)}</div></ToastCtx.Provider>;
}
export const useToast = () => useContext(ToastCtx);

// ---------------------------------------------------------------- pickers
export function BranchSelect({ value, onChange, company, all, label = 'Location', placeholder = 'Choose…' }: { value: string; onChange: (v: string) => void; company?: string; all?: boolean; label?: string; placeholder?: string }) {
  const { myBranches, org } = useAuth();
  const list = (all ? (org?.branches || []).filter(b => b.active) : myBranches).filter(b => !company || b.company === company);
  return (
    <Field label={label}>
      <select value={value} onChange={e => onChange(e.target.value)}>
        <option value="">{placeholder}</option>
        {list.map(b => <option key={b.id} value={b.id}>{b.company} · {b.name}</option>)}
      </select>
    </Field>
  );
}

export function CompanySelect({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const { org } = useAuth();
  return (
    <Field label="Company">
      <select value={value} onChange={e => onChange(e.target.value)}>
        {(org?.companies || []).map(c => <option key={c.code} value={c.code}>{c.name}</option>)}
      </select>
    </Field>
  );
}

/** Arrow keys move through a picker's results, Enter picks the highlighted one, Esc clears. */
function usePickerKeys<T>(list: T[], pick: (x: T) => void, clear: () => void, fallback: () => void) {
  const [i, setI] = useState(-1);
  useEffect(() => { setI(list.length === 1 ? 0 : -1); }, [list]);
  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown' && list.length) { e.preventDefault(); setI(x => Math.min(list.length - 1, x + 1)); }
    else if (e.key === 'ArrowUp' && list.length) { e.preventDefault(); setI(x => Math.max(0, x - 1)); }
    else if (e.key === 'Escape' && list.length) { e.preventDefault(); e.stopPropagation(); clear(); }
    else if (e.key === 'Enter') { e.preventDefault(); if (i >= 0 && list[i]) pick(list[i]); else fallback(); }
  };
  return { i, onKeyDown };
}

export function ItemSearch({ company, onPick, placeholder = 'Scan barcode / ISBN or type item name' }: { company?: string; onPick: (item: any) => void; placeholder?: string }) {
  const [q, setQ] = useState('');
  const [list, setList] = useState<any[]>([]);
  const toast = useToast();
  useEffect(() => {
    if (q.trim().length < 2) { setList([]); return; }
    const t = setTimeout(() => {
      api<any>(`/items?size=12${company ? `&company=${company}` : ''}&q=${encodeURIComponent(q)}`).then(r => setList(r.rows)).catch(() => setList([]));
    }, 250);
    return () => clearTimeout(t);
  }, [q, company]);
  const scan = async () => {
    if (!q.trim()) return;
    try { const it = await api(`/items/lookup?code=${encodeURIComponent(q)}${company ? `&company=${company}` : ''}`); onPick(it); setQ(''); setList([]); }
    catch { if (list.length === 1) { onPick(list[0]); setQ(''); setList([]); } else if (!list.length) toast(`No item matches “${q}”. Check the code, or type part of the name.`, 'bad'); else toast('Several items match: use ↑ ↓ and Enter, or click one.', 'info'); }
  };
  const take = (it: any) => { onPick(it); setQ(''); setList([]); };
  const k = usePickerKeys(list, take, () => setList([]), scan);
  return (
    <div className="picker">
      <input value={q} onChange={e => setQ(e.target.value)} placeholder={placeholder} inputMode="search" autoComplete="off"
             onKeyDown={k.onKeyDown} aria-label="Find item" />
      {list.length > 0 && (
        <ul className="picker-list">
          {list.map((it, n) => (
            <li key={it.id} className={n === k.i ? 'on' : ''}><button type="button" tabIndex={-1} onMouseDown={e => { e.preventDefault(); take(it); }}>
              <b>{it.name}</b> <span className="muted">{[it.size, it.class_name, it.category].filter(Boolean).join(' · ')}</span>
              <span className="r">{it.unpriced ? <Pill s="No price" tone="warn" /> : inr(it.price)}</span>
            </button></li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function StudentSearch({ onPick, placeholder = 'Admission no., name or parent mobile (scan the ID card)' }: { onPick: (s: any) => void; placeholder?: string }) {
  const [q, setQ] = useState('');
  const [list, setList] = useState<any[]>([]);
  useEffect(() => {
    if (q.trim().length < 3) { setList([]); return; }
    const t = setTimeout(() => { api<any[]>(`/students?q=${encodeURIComponent(q)}`).then(r => { setList(r); setNone(!r.length); }).catch(() => setList([])); }, 250);
    return () => clearTimeout(t);
  }, [q]);
  const [none, setNone] = useState(false);
  useEffect(() => { setNone(false); }, [q]);
  const take = (st: any) => { onPick(st); setQ(''); setList([]); };
  const k = usePickerKeys(list, take, () => setList([]), () => undefined);
  return (
    <div className="picker">
      <input value={q} onChange={e => setQ(e.target.value)} placeholder={placeholder} autoComplete="off" aria-label="Find student"
             onKeyDown={k.onKeyDown} />
      {none && q.trim().length >= 3 && <small className="muted">No student matches “{q}”. Try the admission number (e.g. MSA240123) or the parent’s mobile.</small>}
      {list.length > 0 && (
        <ul className="picker-list">
          {list.map((s, n) => (
            <li key={s.adm_no} className={n === k.i ? 'on' : ''}><button type="button" tabIndex={-1} onMouseDown={e => { e.preventDefault(); take(s); }}>
              <b>{s.name}</b> <span className="mono">{s.adm_no}</span> <span className="muted">Class {s.class_name}{s.section ? '-' + s.section : ''} · {s.category}</span>
              {s.status !== 'Active' && <Pill s={s.status} />}
            </button></li>
          ))}
        </ul>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- evidence
export function EvidencePanel({ refNo, company }: { refNo: string; company?: string }) {
  const { data, reload } = useLoad<any[]>(() => api(`/evidence?ref=${encodeURIComponent(refNo)}`), [refNo]);
  const [run, busy] = useAction();
  const fileRef = useRef<HTMLInputElement>(null);
  const [kind, setKind] = useState('Invoice and supplier bill');
  const upload = async () => {
    const f = fileRef.current?.files?.[0];
    if (!f) return;
    const fd = new FormData();
    fd.append('file', f); fd.append('ref', refNo); fd.append('kind', kind); if (company) fd.append('company', company);
    await run(() => api('/evidence', { form: fd }), () => 'File attached.');
    if (fileRef.current) fileRef.current.value = '';
    reload();
  };
  return (
    <div className="evidence">
      <h4>Evidence</h4>
      {data && !data.length && <p className="muted">No files attached.</p>}
      <ul className="files">{(data || []).map(e => (
        <li key={e.id} className={e.status !== 'Active' ? 'muted' : ''}>
          <a href={`/api/evidence/${e.id}/download`}>{e.file_name}</a> <span className="muted">{e.kind} · {when(e.uploaded_at)} · {e.uploaded_by_name}</span>
          {e.status !== 'Active' && <Pill s={e.status} />} {e.verify_status !== 'Submitted' && <Pill s={e.verify_status} />}
        </li>))}
      </ul>
      <div className="row">
        <select value={kind} onChange={e => setKind(e.target.value)} aria-label="Evidence type">
          {['Invoice and supplier bill', 'Voucher', 'Receipt', 'Approval email / letter', 'Quotation', 'Delivery challan', 'Signed acknowledgement',
            'Bank deposit slip', 'Payment proof', 'Stock photo', 'Owner instruction', 'Other'].map(k => <option key={k}>{k}</option>)}
        </select>
        <input ref={fileRef} type="file" accept=".pdf,image/*,.xlsx,.xls,.csv,.docx,.doc,.txt" capture="environment" aria-label="File" />
        <Btn onClick={upload} busy={busy}>Attach</Btn>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- in-page confirmation
export function ConfirmBtn({ label, confirm = 'Confirm', kind = 'danger', onConfirm, busy }: { label: string; confirm?: string; kind?: 'danger' | 'pri'; onConfirm: () => void; busy?: boolean }) {
  const [armed, setArmed] = useState(false);
  useEffect(() => { if (!armed) return; const t = setTimeout(() => setArmed(false), 4000); return () => clearTimeout(t); }, [armed]);
  return armed ? <Btn kind={kind} busy={busy} onClick={() => { setArmed(false); onConfirm(); }}>{confirm}</Btn> : <Btn onClick={() => setArmed(true)}>{label}</Btn>;
}

// ---------------------------------------------------------------- crash guard
/** Keeps one broken screen from blanking the whole app. Shows the error and a way back; resets when the route changes. */
export class ErrorBoundary extends Component<{ children: ReactNode; resetKey?: string }, { err: Error | null }> {
  state = { err: null as Error | null };
  static getDerivedStateFromError(err: Error) { return { err }; }
  componentDidCatch(err: Error, info: ErrorInfo) { console.error('Screen error', err, info.componentStack); }
  componentDidUpdate(prev: { resetKey?: string }) { if (prev.resetKey !== this.props.resetKey && this.state.err) this.setState({ err: null }); }
  render() {
    if (!this.state.err) return this.props.children;
    return (
      <div className="panel">
        <h2>This screen hit an error</h2>
        <p className="muted">Nothing was saved by the failed screen. Reload to try again; if it repeats, send the text below to IT (docs/TROUBLESHOOTING.md → "Screen hit an error").</p>
        <pre className="code">{String(this.state.err.message || this.state.err)}</pre>
        <div className="row"><Btn kind="pri" onClick={() => window.location.reload()}>Reload</Btn><Btn onClick={() => { window.location.href = '/'; }}>Go to dashboard</Btn></div>
      </div>
    );
  }
}
