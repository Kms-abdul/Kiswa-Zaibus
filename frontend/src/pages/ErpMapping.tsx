/* ERP import → API connection: pick the MCB view that holds the students, choose which column feeds each field (or a fixed
   value, or "same as" another field, with optional value translations and a pattern to take part of a value), preview 20 rows,
   save, then run a trial pull or the real pull. The API address and key live only in backend/.env; this screen never sees the key. */
import { useEffect, useMemo, useState } from 'react';
import { api } from '../api';
import { useAuth } from '../auth';
import { Btn, DataTable, Field, Panel, Pill, useAction, useLoad, useToast } from '../ui/kit';

type Src = { kind: '' | 'col' | 'const' | 'same_as'; col?: string; const?: string; same_as?: string; regex?: string; map?: string; more?: boolean };
type FieldDef = { key: string; label: string; required: boolean };

const toText = (m?: Record<string, string>) => Object.entries(m || {}).map(([a, b]) => `${a} = ${b}`).join('\n');
const fromText = (t?: string) => Object.fromEntries((t || '').split('\n').map(l => l.split('=')).filter(p => p.length >= 2 && p[0].trim())
  .map(p => [p[0].trim(), p.slice(1).join('=').trim()]));

function toState(saved: any): Record<string, Src> {
  const out: Record<string, Src> = {};
  for (const [k, s] of Object.entries<any>(saved?.fields || {})) {
    const kind = s.col ? 'col' : s.const !== undefined ? 'const' : s.same_as ? 'same_as' : '';
    out[k] = { kind, col: s.col, const: s.const, same_as: s.same_as, regex: s.regex || '', map: toText(s.map), more: !!(s.regex || s.map) };
  }
  return out;
}

function toSpec(view: string, fields: Record<string, Src>, filters: { col: string; val: string }[]) {
  const f: Record<string, any> = {};
  for (const [k, s] of Object.entries(fields)) {
    if (!s.kind) continue;
    const o: any = { [s.kind]: s[s.kind as 'col' | 'const' | 'same_as'] };
    if (s.regex) o.regex = s.regex;
    const m = fromText(s.map); if (Object.keys(m).length) o.map = m;
    f[k] = o;
  }
  return { view, fields: f, filters: Object.fromEntries(filters.filter(x => x.col && x.val).map(x => [x.col, x.val])) };
}

export default function ErpMapping({ onStarted }: { onStarted: () => void }) {
  const { can } = useAuth();
  const toast = useToast();
  const edit = can('import', 'ctl.int');
  const st = useLoad<any>(() => api('/erp/api/status'), []);
  const saved = useLoad<any>(() => api('/erp/api/mapping'), []);
  const [views, setViews] = useState<string[] | null>(null);
  const [view, setView] = useState('');
  const [cols, setCols] = useState<{ name: string; type: string }[]>([]);
  const [fields, setFields] = useState<Record<string, Src>>({});
  const [filters, setFilters] = useState<{ col: string; val: string }[]>([]);
  const [prev, setPrev] = useState<any>(null);
  const [run, busy] = useAction();
  const defs: FieldDef[] = saved.data?.fields || [];
  const ready = st.data?.configured && st.data?.key_set;

  useEffect(() => {                                                    // start from the saved mapping
    const m = saved.data?.mapping;
    if (!m) return;
    setView(m.view); setFields(toState(m)); setFilters(Object.entries<string>(m.filters || {}).map(([col, val]) => ({ col, val })));
  }, [saved.data]);
  useEffect(() => {
    if (!view || !ready) { setCols([]); return; }
    api<any[]>(`/erp/api/views/${encodeURIComponent(view)}/schema`).then(setCols).catch(e => toast(String(e?.message || e), 'bad'));
  }, [view, ready]);  // eslint-disable-line react-hooks/exhaustive-deps

  const loadViews = () => run(() => api<string[]>('/erp/api/views').then(v => { setViews(v); return v; }), (v: string[]) => `${v.length} view(s) found.`);
  const set = (k: string, p: Partial<Src>) => setFields(f => ({ ...f, [k]: { ...(f[k] || { kind: '' }), ...p } }));
  const spec = useMemo(() => toSpec(view, fields, filters), [view, fields, filters]);
  const colNames = cols.map(c => c.name);

  const preview = () => run(() => api('/erp/api/preview', { body: { mapping: spec } }).then(r => { setPrev(r); return r; }),
    (r: any) => `${r.rows.length} row(s) previewed; ${r.rows.filter((x: any) => x.problem).length} with a problem.`);
  const save = async () => { if (await run(() => api('/erp/api/mapping', { method: 'PUT', body: { mapping: spec } }), () => 'Mapping saved.')) { saved.reload(); st.reload(); } };
  const pull = async (dry: boolean) => {
    if (await run(() => api('/erp/sync', { body: { dry_run: dry } }), (x: any) => (x.status === 'Running'
      ? `${x.run} started${dry ? ' (trial run: nothing is saved)' : ''}. Progress shows in Sync runs below.` : `${x.run}: ${x.created} created, ${x.updated} updated.`))) onStarted();
  };

  if (!st.data) return null;
  return <Panel title="API connection and column mapping" actions={<Btn onClick={() => st.reload()}>Test connection</Btn>}>
    <div className="row">
      <Pill s={st.data.configured ? `API: ${st.data.host}` : 'API address not set'} tone={st.data.configured ? 'info' : 'warn'} />
      <Pill s={st.data.key_set ? `Key set (${st.data.header})` : 'Key missing'} tone={st.data.key_set ? 'ok' : 'warn'} />
      {st.data.configured && <Pill s={st.data.reachable ? `Reachable · database ${st.data.database || '?'}` : 'Not reachable'} tone={st.data.reachable ? 'ok' : 'bad'} />}
      <Pill s={st.data.mapping_saved ? 'Mapping saved' : 'No mapping yet'} tone={st.data.mapping_saved ? 'ok' : 'warn'} />
    </div>
    {!ready && <p className="note">Put <code>ERP_API_URL</code> and <code>ERP_API_KEY</code> in <code>backend/.env</code> and restart the backend.
      The key is never shown or stored anywhere else.</p>}
    {st.data.error && <p className="err">{st.data.error}</p>}

    {ready && <>
      <h4>1. The view that holds the students</h4>
      <div className="form grid2">
        <Field label="View">
          <select value={view} disabled={!edit} onChange={e => { setView(e.target.value); setPrev(null); }}>
            <option value="">Choose…</option>
            {[...new Set([...(views || []), ...(view ? [view] : [])])].map(v => <option key={v}>{v}</option>)}
          </select>
        </Field>
        <div className="row"><Btn busy={busy} onClick={loadViews}>Load views from the API</Btn>{cols.length > 0 && <span className="muted">{cols.length} columns</span>}</div>
      </div>

      {cols.length > 0 && <>
        <h4>2. Only these rows (optional filters)</h4>
        {filters.map((x, i) => <div key={i} className="row">
          <select value={x.col} onChange={e => setFilters(fs => fs.map((y, j) => (j === i ? { ...y, col: e.target.value } : y)))} aria-label="Filter column">
            <option value="">Column…</option>{colNames.map(c => <option key={c}>{c}</option>)}</select>
          <input placeholder="equals…" value={x.val} onChange={e => setFilters(fs => fs.map((y, j) => (j === i ? { ...y, val: e.target.value } : y)))} aria-label="Filter value" />
          <Btn kind="link" onClick={() => setFilters(fs => fs.filter((_, j) => j !== i))}>Remove</Btn></div>)}
        {edit && <Btn kind="link" onClick={() => setFilters(fs => [...fs, { col: '', val: '' }])}>+ Add a filter (e.g. academic year = 2026-2027)</Btn>}

        <h4>3. Where each field comes from</h4>
        <div className="erp-map">
          {defs.map(d => {
            const s = fields[d.key] || { kind: '' };
            return <div key={d.key} className="erp-row">
              <div className="erp-name">{d.label}{d.required && <b className="req"> *</b>} <small className="mono muted">{d.key}</small></div>
              <select value={s.kind} disabled={!edit} onChange={e => set(d.key, { kind: e.target.value as Src['kind'] })} aria-label={`Source for ${d.label}`}>
                <option value="">— not imported —</option><option value="col">Column</option><option value="const">Fixed value</option><option value="same_as">Same as field</option>
              </select>
              {s.kind === 'col' && <select value={s.col || ''} disabled={!edit} onChange={e => set(d.key, { col: e.target.value })} aria-label={`Column for ${d.label}`}>
                <option value="">Choose column…</option>{colNames.map(c => <option key={c}>{c}</option>)}</select>}
              {s.kind === 'const' && <input value={s.const || ''} readOnly={!edit} onChange={e => set(d.key, { const: e.target.value })} placeholder="e.g. MSEA" aria-label={`Value for ${d.label}`} />}
              {s.kind === 'same_as' && <select value={s.same_as || ''} disabled={!edit} onChange={e => set(d.key, { same_as: e.target.value })} aria-label={`Same as for ${d.label}`}>
                <option value="">Choose field…</option>{defs.filter(o => o.key !== d.key).map(o => <option key={o.key} value={o.key}>{o.label}</option>)}</select>}
              {(s.kind === 'col' || s.kind === 'const') && <Btn kind="link" onClick={() => set(d.key, { more: !s.more })}>{s.more ? 'Fewer options' : 'Translate / take part'}</Btn>}
              {s.more && (s.kind === 'col' || s.kind === 'const') && <div className="erp-more">
                <Field label="Take part of the value (pattern; the part in brackets is kept)" hint="e.g. ^\d{2}([A-Z]+) takes MRR from 26MRR0017">
                  <input className="mono" value={s.regex || ''} readOnly={!edit} onChange={e => set(d.key, { regex: e.target.value })} /></Field>
                <Field label="Translate values (one per line: ERP value = our value)" hint="e.g. Studying = Active">
                  <textarea rows={3} value={s.map || ''} readOnly={!edit} onChange={e => set(d.key, { map: e.target.value })} /></Field>
              </div>}
            </div>;
          })}
        </div>

        <div className="row">
          <Btn busy={busy} onClick={preview}>Preview 20 rows</Btn>
          {edit && <Btn kind="pri" busy={busy} onClick={save}>Save mapping</Btn>}
        </div>

        {prev && <DataTable rows={prev.rows.map((r: any, i: number) => ({ i, ...r.mapped, problem: r.problem }))} keyOf={(r: any) => r.i}
          cols={[...prev.fields.filter((k: string) => fields[k]?.kind).map((k: string) => ({ key: k, label: k })),
            { key: 'problem', label: 'Problem', render: (r: any) => (r.problem ? <Pill s={r.problem} tone="bad" /> : <Pill s="OK" tone="ok" />) }]} />}

        {edit && st.data.mapping_saved && <>
          <h4>4. Pull the students</h4>
          <p className="muted">A trial run checks every row and shows what would be created or updated, without saving. The real pull adds new
            students and updates changed ones; pulling again never duplicates.</p>
          <div className="row"><Btn busy={busy} onClick={() => pull(true)}>Trial run</Btn><Btn kind="pri" busy={busy} onClick={() => pull(false)}>Pull now</Btn></div>
        </>}
      </>}
    </>}
  </Panel>;
}
