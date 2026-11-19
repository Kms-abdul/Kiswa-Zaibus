/* Display formats (2.5: friendlier). Dates read "10 Nov 2026", times "10 Nov 2026, 8:37 pm"; money is Indian-grouped rupees with a
   proper minus sign; dates for forms and the API stay ISO (YYYY-MM-DD) in the user's own time zone. */
const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const ISO = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?/;

export const inr = (n: number | string | null | undefined, dp = 2) => {
  if (n == null || n === '' || Number.isNaN(Number(n))) return '—';
  const v = Number(n);
  const s = '₹' + Math.abs(v).toLocaleString('en-IN', { minimumFractionDigits: dp, maximumFractionDigits: dp });
  return v < 0 && Math.abs(v) >= 0.5 * 10 ** -dp ? '−' + s : s;
};
export const num = (n: number | null | undefined) => (n == null ? '—' : Number(n).toLocaleString('en-IN'));

/** "10 Nov 2026" (adds the year only when it is not this year, unless `year` is set). */
export const day = (s: string | null | undefined, year = true) => {
  const m = s ? ISO.exec(String(s)) : null;
  if (!m) return s ? String(s) : '—';
  const y = Number(m[1]);
  return `${Number(m[3])} ${MON[Number(m[2]) - 1]}${year || y !== new Date().getFullYear() ? ' ' + y : ''}`;
};
/** "10 Nov 2026, 8:37 pm"; just the date when there is no time. */
export const when = (s: string | null | undefined) => {
  const m = s ? ISO.exec(String(s)) : null;
  if (!m) return s ? String(s) : '—';
  if (m[4] == null) return day(s);
  const h = Number(m[4]);
  return `${day(s)}, ${h % 12 || 12}:${m[5]} ${h < 12 ? 'am' : 'pm'}`;
};
/** "5 min ago", "3 h ago", "yesterday", else the date. */
export const ago = (s: string | null | undefined) => {
  const m = s ? ISO.exec(String(s)) : null;
  if (!m) return '—';
  const t = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]), Number(m[4] || 0), Number(m[5] || 0)).getTime();
  const min = Math.round((Date.now() - t) / 60000);
  if (min < 1) return 'just now';
  if (min < 60) return `${min} min ago`;
  if (min < 24 * 60 && new Date(t).getDate() === new Date().getDate()) return `${Math.round(min / 60)} h ago`;
  if (min < 48 * 60) return 'yesterday';
  return day(s);
};
/** A value that looks like an ISO date or date-time, shown friendly; anything else unchanged. */
export const friendly = (v: unknown) => (typeof v === 'string' && /^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?)?$/.test(v) ? when(v) : v);

/** "1 bill", "3 bills", "1 entry", "2 entries". */
export const plural = (n: number, word: string, many?: string) => `${num(n)} ${n === 1 ? word : many || (word.endsWith('y') && !/[aeiou]y$/.test(word) ? word.slice(0, -1) + 'ies' : word + 's')}`;

/** Today's date as YYYY-MM-DD in the user's own time zone (not UTC: in India UTC is still yesterday until 5:30 am). */
export const today = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; };
export const monthStart = () => today().slice(0, 8) + '01';
export const co = (c: string) => (c === 'KG' ? 'Kiswa' : c === 'ZE' ? 'Zaibus' : c);
