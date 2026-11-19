/* Quick search (2.5). Ctrl+K, "/" or the search box in the top bar opens one box that finds:
   screens and common actions (only those the user may use), bills (number or admission no.), students and items.
   Arrow keys move, Enter opens, Esc closes. Results only ever come from APIs the user can already call. */
import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../api';
import { useAuth } from '../auth';
import { inr, when } from '../format';
import { NAV } from '../Shell';

type Hit = { kind: string; label: string; hint?: string; to: string };

const ACTIONS: { label: string; hint: string; to: string; show: (c: (a: string, k: string) => boolean) => boolean; words?: string }[] = [
  { label: 'New bill', hint: 'Start billing at the counter', to: '/pos', show: c => c('add', 'pos'), words: 'sale billing invoice counter' },
  { label: 'Return or exchange a bill', hint: 'Find the bill, then Return', to: '/invoices?tab=all', show: c => c('view', 'pos'), words: 'refund credit note' },
  { label: 'Hand over cash to the clerk', hint: 'Cash custody', to: '/cash', show: c => c('view', 'cash'), words: 'handover deposit' },
  { label: 'New stock transfer', hint: 'Send stock to another location', to: '/transfers?new=1', show: c => c('add', 'inv'), words: 'move send' },
  { label: 'Receive goods (GRN)', hint: 'Purchasing → open the PO → Receive', to: '/purchasing', show: c => c('view', 'pur'), words: 'grn inward purchase' },
  { label: 'Count stock (stock audit)', hint: 'Stock audits', to: '/stock-audits', show: c => c('view', 'saudit'), words: 'physical verification audit count' },
  { label: 'Low stock / reorder', hint: 'Report with a suggested order', to: '/reports?r=low-stock', show: c => c('view', 'rep'), words: 'reorder shortage' },
  { label: 'Daily sales and collection', hint: 'Report', to: '/reports?r=daily-sales', show: c => c('view', 'rep'), words: 'today collection' },
  { label: 'Shift closing (short / excess)', hint: 'Report', to: '/reports?r=shift-closing', show: c => c('view', 'rep'), words: 'cash short' },
  { label: 'Print barcode labels', hint: 'Barcode labels', to: '/labels', show: c => c('view', 'inv'), words: 'sticker tag' },
  { label: 'New finance voucher', hint: 'Finance & tax → Vouchers', to: '/finance?tab=vch', show: c => c('add', 'fin'), words: 'journal payment expense receipt' },
  { label: 'Close the financial year', hint: 'Finance & tax → Year-end: checklist, profit to capital, lock', to: '/finance?tab=ye', show: c => c('view', 'fin'), words: 'year end closing march books lock reopen' },
  { label: 'Approvals waiting for me', hint: 'Approvals', to: '/approvals', show: c => c('view', 'appr'), words: 'approve pending inbox' },
  { label: 'Change my password', hint: 'My account', to: '/profile', show: () => true, words: 'password account profile' },
];

export function Palette({ onClose }: { onClose: () => void }) {
  const { can } = useAuth();
  const nav = useNavigate();
  const [q, setQ] = useState('');
  const [i, setI] = useState(0);
  const [remote, setRemote] = useState<Hit[]>([]);
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => { input.current?.focus(); }, []);

  const local = useMemo(() => {
    const t = q.trim().toLowerCase();
    const screens = NAV.filter(n => n.show(can)).map(n => ({ kind: 'Screen', label: n.label, hint: n.group, to: n.to, words: '' }));
    const acts = ACTIONS.filter(a => a.show(can)).map(a => ({ kind: 'Action', label: a.label, hint: a.hint, to: a.to, words: a.words || '' }));
    const all = [...acts, ...screens];
    if (!t) return all.slice(0, 8);
    return all.filter(h => (h.label + ' ' + h.hint + ' ' + h.words).toLowerCase().includes(t)).slice(0, 8);
  }, [q, can]);

  useEffect(() => {
    const t = q.trim();
    if (t.length < 2) { setRemote([]); return; }
    let live = true;
    const h = setTimeout(async () => {
      setBusy(true);
      const jobs: Promise<Hit[]>[] = [];
      if (can('view', 'pos')) jobs.push(api<any[]>(`/pos/invoices?q=${encodeURIComponent(t)}`).then(r => r.slice(0, 5).map(b => ({
        kind: 'Bill', label: b.no, hint: `${b.adm_no || 'walk-in'} · ${inr(b.total)} · ${when(b.inv_date)} · ${b.status}`, to: `/invoices?tab=all&open=${b.id}` }))).catch(() => []));
      if (t.length >= 3 && (can('view', 'pos') || can('view', 'msea') || can('view', 'onl'))) jobs.push(api<any[]>(`/students?q=${encodeURIComponent(t)}`).then(r => r.slice(0, 5).map(s => ({
        kind: 'Student', label: `${s.name} · ${s.adm_no}`, hint: `Class ${s.class_name}${s.section ? '-' + s.section : ''} · open their bills`, to: `/invoices?tab=all&q=${encodeURIComponent(s.adm_no)}` }))).catch(() => []));
      if (can('view', 'inv')) jobs.push(api<any>(`/items?size=5&q=${encodeURIComponent(t)}`).then(r => (r.rows || []).map((it: any) => ({
        kind: 'Item', label: `${it.name}${it.size ? ' ' + it.size : ''}${it.class_name ? ' · ' + it.class_name : ''}`, hint: `${it.id} · ${it.unpriced ? 'no price' : inr(it.price)} · see stock`,
        to: `/stock?q=${encodeURIComponent(it.id)}` }))).catch(() => []));
      const res = (await Promise.all(jobs)).flat();
      if (live) { setRemote(res); setBusy(false); }
    }, 250);
    return () => { live = false; clearTimeout(h); };
  }, [q, can]);

  const hits = [...local, ...remote];
  useEffect(() => { setI(0); }, [q]);
  const go = (h?: Hit) => { if (!h) return; onClose(); nav(h.to); };
  const key = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); setI(x => Math.min(hits.length - 1, x + 1)); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setI(x => Math.max(0, x - 1)); }
    else if (e.key === 'Enter') { e.preventDefault(); go(hits[i]); }
    else if (e.key === 'Escape') onClose();
  };
  let last = '';
  return (
    <div className="modal-bg palette-bg" onMouseDown={e => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="palette" role="dialog" aria-modal="true" aria-label="Quick search">
        <input ref={input} value={q} onChange={e => setQ(e.target.value)} onKeyDown={key} placeholder="Search screens, bills, students, items…"
          aria-label="Quick search" role="combobox" aria-expanded="true" aria-controls="palette-list" aria-activedescendant={`ph-${i}`} autoComplete="off" />
        <ul id="palette-list" role="listbox">
          {hits.map((h, k) => {
            const head = h.kind !== last ? (last = h.kind) : null;
            return [head && <li key={'h' + k} className="ph-group" role="presentation">{head === 'Action' ? 'Do something' : head + 's'}</li>,
              <li key={k} id={`ph-${k}`} role="option" aria-selected={k === i} className={k === i ? 'on' : ''} onMouseEnter={() => setI(k)} onMouseDown={e => { e.preventDefault(); go(h); }}>
                <b>{h.label}</b>{h.hint && <span className="muted">{h.hint}</span>}</li>];
          })}
          {!hits.length && <li className="ph-none">{busy ? 'Searching…' : q.trim().length < 2 ? 'Type to search.' : `Nothing found for “${q}”.`}</li>}
        </ul>
        <div className="ph-foot muted">↑ ↓ to move · Enter to open · Esc to close · <kbd>Ctrl</kbd>+<kbd>K</kbd> or <kbd>/</kbd> opens this anywhere</div>
      </div>
    </div>
  );
}

/** Keyboard shortcuts for the whole app. */
export const SHORTCUTS: [string, string][] = [
  ['Ctrl + K  or  /', 'Quick search: screens, bills, students, items'],
  ['?', 'Show this list'],
  ['Esc', 'Close a dialog'],
  ['On New bill: F2', 'Find the student'],
  ['On New bill: F3', 'Scan or find an item'],
  ['On New bill: F8', 'Amount received'],
  ['On New bill: F9  or  Ctrl + Enter', 'Save the bill'],
];
