/* Declarative record form used by the setup screens: a list of field specs renders the right inputs in a two-column grid
   (one column on phones). Also the custom-fields block that any item / customer / supplier / student form can include. */
import { useEffect, useState, type ReactNode } from 'react';
import { api } from '../api';
import { Field, useLoad } from './kit';

export type Spec = {
  k: string; label: string; type?: 'text' | 'number' | 'date' | 'select' | 'check' | 'textarea' | 'email' | 'tel';
  options?: [string, string][]; required?: boolean; hint?: ReactNode; wide?: boolean; lockOnEdit?: boolean; show?: (v: any) => boolean; placeholder?: string;
};

export function RecordForm({ specs, value, onChange, editing }: { specs: Spec[]; value: any; onChange: (v: any) => void; editing?: boolean }) {
  const set = (k: string, v: any) => onChange({ ...value, [k]: v });
  return (
    <div className="form grid2">
      {specs.filter(s => !s.show || s.show(value)).map(s => {
        const ro = editing && s.lockOnEdit;
        const label = s.label + (s.required ? ' *' : '');
        if (s.type === 'check') return <label key={s.k} className="check"><input type="checkbox" checked={!!value[s.k]} disabled={ro} onChange={e => set(s.k, e.target.checked)} />{s.label}</label>;
        if (s.type === 'select') return <Field key={s.k} label={label} hint={s.hint} wide={s.wide}>
          <select value={value[s.k] ?? ''} disabled={ro} onChange={e => set(s.k, e.target.value)}><option value="">Choose…</option>{(s.options || []).map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></Field>;
        if (s.type === 'textarea') return <Field key={s.k} label={label} hint={s.hint} wide><textarea rows={3} value={value[s.k] ?? ''} readOnly={ro} onChange={e => set(s.k, e.target.value)} /></Field>;
        return <Field key={s.k} label={label} hint={s.hint} wide={s.wide}>
          <input type={s.type === 'number' ? 'text' : s.type || 'text'} inputMode={s.type === 'number' ? 'decimal' : undefined} value={value[s.k] ?? ''} readOnly={ro}
                 placeholder={s.placeholder} onChange={e => set(s.k, e.target.value)} /></Field>;
      })}
    </div>
  );
}

/** Values of the active custom fields for one record; call save() after the record itself is saved. */
export function useCustomFields(entity: 'item' | 'customer' | 'supplier' | 'student', ref: string | undefined) {
  const { data: fields } = useLoad<any[]>(() => api(`/setup/custom-fields?entity=${entity}`), [entity]);
  const [vals, setVals] = useState<Record<string, string>>({});
  useEffect(() => { if (ref) api(`/setup/custom-values/${entity}/${encodeURIComponent(ref)}`).then(setVals).catch(() => setVals({})); else setVals({}); }, [entity, ref]);
  const active = (fields || []).filter(f => f.active);
  const block = active.length ? (
    <>
      <h4>More details</h4>
      <div className="form grid2">{active.map(f => (
        <Field key={f.id} label={f.label + (f.required ? ' *' : '')}>
          {f.type === 'select' ? <select value={vals[f.id] || ''} onChange={e => setVals(v => ({ ...v, [f.id]: e.target.value }))}><option value="">–</option>{(f.options || []).map((o: string) => <option key={o}>{o}</option>)}</select>
            : <input type={f.type === 'date' ? 'date' : 'text'} inputMode={f.type === 'number' ? 'decimal' : undefined} value={vals[f.id] || ''} onChange={e => setVals(v => ({ ...v, [f.id]: e.target.value }))} />}
        </Field>))}
      </div>
    </>
  ) : null;
  const save = (r: string) => (active.length ? api(`/setup/custom-values/${entity}/${encodeURIComponent(r)}`, { method: 'PUT', body: vals }) : Promise.resolve(null));
  return { block, save };
}
