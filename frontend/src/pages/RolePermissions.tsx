/* Role Permissions: pick a role (or add one), then give each screen a mode — None / Read / Write / Full / Custom — with the
   sensitive extras (see cost, finance figures, masters …) ticked separately. Modules and screens come from the permission
   catalogue (GET /api/setup/catalog → permissions), so a screen added in code shows up here without any change to this file.
   Saving goes through the same API as before (reason required; maker-checker when it is switched on). */
import { useEffect, useMemo, useState } from 'react';
import { api } from '../api';
import { Btn, Field, Panel, Pill, useAction, useLoad, useToast } from '../ui/kit';

type Screen = { key: string; module: string; label: string; sort: number; actions: string[]; extras: string[] };
type Catalog = { modules: { key: string; label: string }[]; screens: Screen[]; modes: { key: string; label: string; actions: string[] }[];
  extras: { key: string; label: string }[]; limits: { key: string; label: string }[] };
type Role = { name: string; display_name?: string | null; description?: string | null; approval_rank: number; read_only: number | boolean;
  active: number | boolean; users: number; permissions: Record<string, string[]>; limits: Record<string, number>; all_rights?: boolean };
type Draft = Omit<Role, 'users'> & { isNew?: boolean; copy_from?: string };

const NEW = '__new__';
const ACTION_HELP: Record<string, string> = {
  view: 'Open and see the screen', print: 'Print', pdf: 'Download PDF', export: 'Export to Excel / CSV', add: 'Create new records',
  edit: 'Change records', submit: 'Send for approval', cancel: 'Cancel a record', upload: 'Attach files', import: 'Import from a file',
  approve: 'Approve requests', reject: 'Reject requests', return: 'Send back for changes', reverse: 'Reverse a posted entry',
  delete: 'Delete records', restore: 'Reopen or restore', verify: 'Verify (checker)', review: 'Review', post: 'Post to the books / stock',
};
const same = (a: string[], b: string[]) => a.length === b.length && a.every(x => b.includes(x));

/** The modes a screen can really have: mode actions limited to what the screen checks, duplicates (same set) removed. */
function screenModes(s: Screen, cat: Catalog) {
  const out: { key: string; label: string; actions: string[] }[] = [];
  for (const m of cat.modes) {
    const acts = m.actions.filter(a => s.actions.includes(a));
    if (m.key !== 'none' && !acts.length) continue;
    if (out.some(o => same(o.actions, acts))) continue;
    out.push({ key: m.key, label: m.label, actions: acts });
  }
  return out;
}

function modeOf(s: Screen, cat: Catalog, granted: string[]) {
  const g = granted.filter(a => s.actions.includes(a));
  const m = screenModes(s, cat).find(x => same(x.actions, g));
  return m ? m.key : 'custom';
}

export default function RolePermissions({ edit }: { edit: boolean }) {
  const toast = useToast();
  const roles = useLoad<Role[]>(() => api('/setup/roles'), []);
  const cat = useLoad<any>(() => api('/setup/catalog'), []);
  const c: Catalog | null = cat.data?.permissions || null;
  const [sel, setSel] = useState<string>('');
  const [d, setD] = useState<Draft | null>(null);
  const [custom, setCustom] = useState<Set<string>>(new Set());
  const [q, setQ] = useState('');
  const [reason, setReason] = useState('');
  const [run, busy] = useAction();

  const list = roles.data || [];
  useEffect(() => { if (!sel && list.length) setSel(list.find(r => r.name !== 'Super Admin')?.name || list[0].name); }, [list, sel]);
  useEffect(() => {
    if (sel === NEW) {
      setD({ name: '', display_name: '', description: '', approval_rank: 0, read_only: false, active: true, permissions: {}, limits: {}, isNew: true, copy_from: '' });
    } else {
      const r = list.find(x => x.name === sel);
      setD(r ? JSON.parse(JSON.stringify(r)) : null);
    }
    setCustom(new Set()); setReason('');
  }, [sel, roles.data]);  // eslint-disable-line react-hooks/exhaustive-deps

  const locked = !edit || !d || d.name === 'Super Admin';
  const grouped = useMemo(() => {
    if (!c) return [];
    const needle = q.trim().toLowerCase();
    return c.modules.map(m => ({ ...m, screens: c.screens.filter(s => s.module === m.key && (!needle || s.label.toLowerCase().includes(needle) || s.key.includes(needle) || m.label.toLowerCase().includes(needle))) }))
      .filter(m => m.screens.length);
  }, [c, q]);

  if (cat.err || roles.err) return <Panel title="Roles and permissions"><p className="err">Could not load the roles: {cat.err || roles.err}</p></Panel>;
  if (cat.data && !c) return <Panel title="Roles and permissions"><p className="err">The server is running an older version that does not send
    the permission catalogue. Restart the backend (and run <code>python -m alembic upgrade head</code> if you have not), then refresh this page.</p></Panel>;
  if (!c || !roles.data) return <Panel title="Roles and permissions"><p className="muted">Loading…</p></Panel>;
  const allActions = c.modes.find(m => m.key === 'full')?.actions || [];
  const extraLabel = (k: string) => c.extras.find(e => e.key === k)?.label || k;
  const granted = (key: string) => d?.permissions[key] || [];
  const setGrant = (key: string, acts: string[]) => setD(x => x && ({ ...x, permissions: { ...x.permissions, [key]: [...new Set(acts)] } }));

  const setMode = (s: Screen, mode: string) => {
    if (mode === 'custom') { setCustom(cs => new Set(cs).add(s.key)); return; }
    setCustom(cs => { const n = new Set(cs); n.delete(s.key); return n; });
    const m = screenModes(s, c).find(x => x.key === mode);
    const keepExtras = granted(s.key).filter(a => s.extras.includes(a));
    setGrant(s.key, [...(m?.actions || []), ...keepExtras]);
  };
  const toggle = (key: string, a: string) => setGrant(key, granted(key).includes(a) ? granted(key).filter(x => x !== a) : [...granted(key), a]);
  const setModule = (screens: Screen[], full: boolean) => screens.forEach(s => setMode(s, full ? screenModes(s, c).slice(-1)[0].key : 'none'));
  const isFull = (s: Screen) => { const ms = screenModes(s, c); return same(granted(s.key).filter(a => s.actions.includes(a)), ms[ms.length - 1].actions); };
  const copyFrom = (name: string) => {
    const src = list.find(r => r.name === name);
    setD(x => x && ({ ...x, copy_from: name, permissions: src ? JSON.parse(JSON.stringify(src.permissions)) : {}, limits: src ? { ...src.limits } : {} }));
  };

  const save = async () => {
    if (!d) return;
    if (d.isNew && !d.name.trim()) { toast('Give the role a name.', 'bad'); return; }
    if (!reason.trim()) { toast('Give the reason for this change.', 'bad'); return; }
    const body = { name: d.name.trim(), display_name: d.display_name || null, description: d.description || null, approval_rank: Number(d.approval_rank) || 0,
      read_only: !!d.read_only, active: !!d.active, permissions: d.permissions, limits: d.limits, reason };
    const r = await run(() => (d.isNew ? api('/setup/roles', { body }) : api(`/setup/roles/${encodeURIComponent(d.name)}`, { method: 'PUT', body })),
      (x: any) => (x?.status === 'Applied' ? (d.isNew ? 'Role created.' : 'Role saved.') : x?.no ? `Sent for approval (${x.no}). It applies once approved.` : ''));
    if (r) { await roles.reload(); if (d.isNew) setSel(body.name); setReason(''); }
  };
  const deactivate = async () => {
    if (!d || d.isNew) return;
    if (!reason.trim()) { toast('Give the reason for switching the role off.', 'bad'); return; }
    const r = await run(() => api(`/setup/roles/${encodeURIComponent(d.name)}`, { method: 'DELETE', body: { reason } }),
      (x: any) => (x?.status === 'Applied' ? 'Role switched off.' : x?.no ? `Sent for approval (${x.no}).` : ''));
    if (r) { await roles.reload(); setReason(''); }
  };

  return <>
    <Panel title="Roles and permissions">
      <p className="muted">Choose a role, give each screen a mode, then save. Read = view and print; Write = also add and edit; Full = also approve,
        reverse and post. Use Custom for anything in between. Extras such as seeing cost are ticked on their own.</p>
      <div className="form grid2">
        <Field label="Role">
          <select value={sel} onChange={e => setSel(e.target.value)}>
            {list.map(r => <option key={r.name} value={r.name}>{(r.display_name || r.name) + (r.active ? '' : ' (off)')}</option>)}
            {edit && <option value={NEW}>+ Add a new role</option>}
          </select>
        </Field>
        {d && <>
          <Field label="Role name *" hint={d.isNew ? 'The key used everywhere; it cannot be changed later.' : undefined}>
            <input value={d.name} readOnly={!d.isNew} onChange={e => setD({ ...d, name: e.target.value })} />
          </Field>
          <Field label="Name shown to people (optional)"><input value={d.display_name || ''} readOnly={locked && !d.isNew} onChange={e => setD({ ...d, display_name: e.target.value })} /></Field>
          <Field label="Description"><input value={d.description || ''} readOnly={locked && !d.isNew} onChange={e => setD({ ...d, description: e.target.value })} /></Field>
          <Field label="Approval rank (0 = cannot approve)"><input inputMode="decimal" value={d.approval_rank} readOnly={locked && !d.isNew} onChange={e => setD({ ...d, approval_rank: e.target.value as any })} /></Field>
          {d.isNew && <Field label="Start from the permissions of">
            <select value={d.copy_from || ''} onChange={e => copyFrom(e.target.value)}><option value="">Nothing (empty)</option>
              {list.filter(r => r.name !== 'Super Admin').map(r => <option key={r.name} value={r.name}>{r.display_name || r.name}</option>)}</select>
          </Field>}
          <div className="row">
            <label className="check"><input type="checkbox" checked={!!d.active} disabled={locked && !d.isNew} onChange={e => setD({ ...d, active: e.target.checked })} />Active</label>
            <label className="check"><input type="checkbox" checked={!!d.read_only} disabled={locked && !d.isNew} onChange={e => setD({ ...d, read_only: e.target.checked })} />Read-only (auditors)</label>
            {!d.isNew && <Pill s={`${(d as any).users ?? 0} user(s)`} tone="info" />}
          </div>
        </>}
      </div>
      {d?.name === 'Super Admin' && <p className="note info">The Super Admin role is fixed and always has every right on every screen and the highest limits.
        Only two rules still apply: nobody approves their own request, and each user sees only the companies and locations assigned to them.</p>}
      {!!d?.read_only && <p className="note">Read-only roles can only view, print, export and audit, whatever is ticked below.</p>}
    </Panel>

    {d && d.name !== 'Super Admin' && <Panel title="Screens" actions={!locked || d.isNew ? <div className="row">
      <input placeholder="Search screens" value={q} onChange={e => setQ(e.target.value)} aria-label="Search screens" />
      <Btn onClick={() => c.screens.forEach(s => setMode(s, screenModes(s, c).slice(-1)[0].key))}>Full on all</Btn>
      <Btn onClick={() => c.screens.forEach(s => setMode(s, 'none'))}>Clear all</Btn></div> : undefined}>
      {grouped.map(m => {
        const allFull = m.screens.every(isFull);
        return <section key={m.key} className="rp-mod">
          <label className="check rp-mod-h"><input type="checkbox" checked={allFull} disabled={locked && !d.isNew} onChange={e => setModule(m.screens, e.target.checked)} />
            <b>{m.label}</b><span className="muted"> — tick for Full on every screen in this section</span></label>
          {m.screens.map(s => {
            const modes = screenModes(s, c);
            const mode = custom.has(s.key) ? 'custom' : modeOf(s, c, granted(s.key));
            return <div key={s.key} className="rp-row">
              <div className="rp-name">{s.label} <small className="mono muted">{s.key}</small></div>
              <select value={mode} disabled={locked && !d.isNew} onChange={e => setMode(s, e.target.value)} aria-label={`Mode for ${s.label}`}>
                {modes.map(x => <option key={x.key} value={x.key}>{x.label}</option>)}
                {s.actions.length > 1 && <option value="custom">Custom…</option>}
              </select>
              {mode === 'custom' && <div className="rp-acts">{allActions.map(a => {
                const has = s.actions.includes(a);
                return <label key={a} className={'check' + (has ? '' : ' rp-na')} title={has ? ACTION_HELP[a] : 'This screen has no such action yet, so it cannot be given'}>
                  <input type="checkbox" checked={has && granted(s.key).includes(a)} disabled={!has || (locked && !d.isNew)} onChange={() => toggle(s.key, a)} />{a}</label>;
              })}<small className="muted rp-legend">Greyed = this screen has no such action yet.</small></div>}
              {s.extras.length > 0 && <div className="rp-acts rp-extras">{s.extras.map(a =>
                <label key={a} className="check"><input type="checkbox" checked={granted(s.key).includes(a)} disabled={locked && !d.isNew} onChange={() => toggle(s.key, a)} />{extraLabel(a)}</label>)}</div>}
            </div>;
          })}
        </section>;
      })}
    </Panel>}

    {d && d.name !== 'Super Admin' && <Panel title="Approval limits">
      <p className="muted">The most this role may approve or do without asking. 0 = must ask someone above.</p>
      <div className="form grid2">{c.limits.map(l => <Field key={l.key} label={l.label}>
        <input inputMode="decimal" value={d.limits[l.key] ?? ''} readOnly={locked && !d.isNew} placeholder="0"
          onChange={e => setD({ ...d, limits: { ...d.limits, [l.key]: e.target.value as any } })} /></Field>)}</div>
    </Panel>}

    {d && (!locked || d.isNew) && <Panel>
      <div className="row">
        <input placeholder="Reason for the change (required)" value={reason} onChange={e => setReason(e.target.value)} aria-label="Reason" />
        {!d.isNew && !!d.active && <Btn kind="danger" busy={busy} onClick={deactivate}>Switch role off</Btn>}
        <Btn kind="pri" busy={busy} onClick={save}>{d.isNew ? 'Create role' : 'Save role'}</Btn>
      </div>
    </Panel>}
  </>;
}
