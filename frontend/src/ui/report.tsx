/* Shared report view (release 2.4). Every report in the app — the report library, the finance reports and the stock-audit reports —
   uses one table shape {title, columns: [{h, t}], rows, drill?, totals?, note?, period?, company?}, so the screen, Print, PDF, Excel
   and CSV always show the same table.
   Print: the API is called with format=print first (it checks the Reports → print right and logs the print), then the browser prints
   only the report sheet: a header with the organisation, title, company, period and who printed it, the table with its header row
   repeated on every page, totals, note and page footer. Wide reports print on A4 landscape. */
import { type ReactNode, useEffect, useState } from 'react';
import { api, download, errText } from '../api';
import { useAuth } from '../auth';
import { day, inr } from '../format';
import { Btn, useToast } from './kit';

export type RCol = { h: string; t: 'text' | 'date' | 'num' | 'qty' | 'pct' };
export type Report = { title: string; columns: RCol[]; rows: any[][]; drill?: any[]; totals?: any[] | null; note?: string | null;
  period?: { from: string; to: string }; company?: string; balanced?: boolean };
export type Fmt = 'print' | 'pdf' | 'xlsx' | 'csv';

const RIGHT = new Set(['num', 'qty', 'pct']);
// codes and numbers such as KG-MSA-BLZ-28 or SH-2627-0001 never break at their hyphens
const isCode = (v: any) => typeof v === 'string' && v.length <= 28 && !/\s/.test(v);
export const cell = (v: any, t: string) => (v == null || v === '' ? '' : t === 'num' && typeof v === 'number' ? inr(v)
  : t === 'qty' && typeof v === 'number' ? v.toLocaleString('en-IN') : t === 'pct' && typeof v === 'number' ? `${v}%` : t === 'date' ? day(String(v)) : String(v));

export function ReportTable({ r, onDrill, empty = 'No rows for this selection.', rowClass }: { r: Report; onDrill?: (d: any) => void; empty?: string; rowClass?: (i: number) => string }) {
  const cls = (t: string) => (RIGHT.has(t) ? 'r' : t === 'date' ? 'd' : '');
  return <div className="table-wrap scroll-x"><table className="grid report">
    <thead><tr>{r.columns.map((c, i) => <th key={i} className={RIGHT.has(c.t) ? 'r' : ''}>{c.h}</th>)}</tr></thead>
    <tbody>{r.rows.map((row, i) => { const d = r.drill?.[i]; return <tr key={i} className={[d && onDrill ? 'clickable' : '', rowClass?.(i) || ''].join(' ').trim()} onClick={d && onDrill ? () => onDrill(d) : undefined}
      tabIndex={d && onDrill ? 0 : undefined} onKeyDown={d && onDrill ? e => { if (e.key === 'Enter') onDrill(d); } : undefined}>
      {row.map((v, k) => <td key={k} className={cls(r.columns[k]?.t) + (isCode(v) ? ' nw' : '')}>{cell(v, r.columns[k]?.t)}</td>)}</tr>; })}
      {!r.rows.length && <tr><td colSpan={Math.max(1, r.columns.length)} className="empty">{empty}</td></tr>}</tbody>
    {r.totals && <tfoot><tr>{r.totals.map((v, k) => <td key={k} className={cls(r.columns[k]?.t)}><b>{cell(v, r.columns[k]?.t)}</b></td>)}</tr></tfoot>}
  </table></div>;
}

/** Rows (list of objects, as the stock-audit API returns them) to the common table shape. */
const MONEY = /value|cost$|amount|price/;
export function fromObjects(title: string, rows: Record<string, any>[]): Report {
  const keys = rows.length ? Object.keys(rows[0]) : [];
  const kind = (k: string): RCol['t'] => {
    const vs = rows.map(r => r[k]).filter(v => v != null && v !== '');
    if (k.endsWith('_pct') || k.endsWith('accuracy')) return 'pct';
    if (vs.length && vs.every(v => typeof v === 'number')) return MONEY.test(k) ? 'num' : 'qty';
    if (k.endsWith('date') || k.endsWith('_on')) return 'date';
    return 'text';
  };
  const cols = keys.map(k => ({ h: k.replace(/_/g, ' ').replace(/^./, c => c.toUpperCase()), t: kind(k) }));
  return { title, columns: cols, rows: rows.map(r => keys.map(k => (typeof r[k] === 'boolean' ? (r[k] ? 'Yes' : '') : r[k]))) };
}

/** The printable sheet: on screen it is the panel content; when printing, only this is printed. */
export function ReportSheet({ r, subtitle, children }: { r: Report; subtitle?: string; children: ReactNode }) {
  const { me, org } = useAuth();
  const period = r.period ? (r.period.from ? `${day(r.period.from)} to ${day(r.period.to)}` : `As of ${day(r.period.to)}`) : '';
  const coName = (org?.companies || []).filter(c => (r.company || '').split(', ').includes(c.code)).map(c => c.name).join(', ') || r.company || '';
  return <div className={'report-sheet' + (r.columns.length > 8 ? ' wide' : '')}>
    <div className="print-only rs-head">
      <div className="rs-org">Kiswa-Zaibus Operations</div>
      <h2>{r.title}</h2>
      <div className="rs-meta">{[coName, period, subtitle].filter(Boolean).join(' · ')}</div>
      <div className="rs-meta small">Printed {new Date().toLocaleString('en-IN')} by {me?.name} ({me?.emp_id}) · {r.rows.length} rows</div>
    </div>
    {children}
    {r.note && <p className="muted small rs-note">{r.note}</p>}
    <div className="print-only rs-foot">Generated from the posted records of Kiswa-Zaibus Operations. Figures as at the time of printing.</div>
  </div>;
}

/** Print / PDF / Excel / CSV buttons. `url` is the report's API path with its query string (without format). */
export function ReportActions({ url, name, formats, r }: { url: string; name: string; formats: Fmt[]; r: Report | null }) {
  const toast = useToast();
  const [busy, setBusy] = useState<Fmt | null>(null);
  const sep = url.includes('?') ? '&' : '?';
  const go = async (f: Fmt) => {
    setBusy(f);
    try {
      if (f === 'print') { await api(`${url}${sep}format=print`); printSheet(!!r && r.columns.length > 8); }
      else await download(`${url}${sep}format=${f}`, `${name}.${f}`);
    } catch (e) { toast(errText(e), 'bad'); } finally { setBusy(null); }
  };
  const label: Record<Fmt, string> = { print: 'Print', pdf: 'PDF', xlsx: 'Excel', csv: 'CSV' };
  return <div className="rep-actions">{(['print', 'pdf', 'xlsx', 'csv'] as Fmt[]).filter(f => formats.includes(f)).map(f =>
    <Btn key={f} busy={busy === f} disabled={!r || !!busy} onClick={() => go(f)} aria-label={`${label[f]} ${name}`}>{label[f]}</Btn>)}</div>;
}

/** Print only the report sheet; wide reports on A4 landscape. */
export function printSheet(wide: boolean) {
  const st = document.createElement('style');
  st.textContent = `@page{size:A4 ${wide ? 'landscape' : 'portrait'};margin:10mm}`;
  document.head.appendChild(st);
  document.body.classList.add('printing-report');
  const done = () => { document.body.classList.remove('printing-report'); st.remove(); window.removeEventListener('afterprint', done); };
  window.addEventListener('afterprint', done);
  window.print();
  setTimeout(done, 1000);   // browsers where print() returns before printing still get cleaned up after the dialog
}

/** Rights for the shared buttons, from the user's permissions on a screen key (rep, fin, saudit). */
export function useFormats(screen: string, map: Partial<Record<Fmt, string>> = {}): Fmt[] {
  const { can } = useAuth();
  const [f, setF] = useState<Fmt[]>([]);
  useEffect(() => {
    const need: Record<Fmt, string> = { print: 'print', pdf: 'pdf', xlsx: 'export', csv: 'export', ...map };
    setF((Object.keys(need) as Fmt[]).filter(k => can(need[k], screen)));
  }, [screen, can]);  // eslint-disable-line react-hooks/exhaustive-deps
  return f;
}
