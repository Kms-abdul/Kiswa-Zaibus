/* Setup: the master data the business runs on. Every tab is gated by the same permission the API checks, and every save
   is written to the audit trail. Changes to roles go through maker-checker approval when it is switched on. */
import { useEffect, useMemo, useState } from 'react';
import { api } from '../api';
import { useAuth } from '../auth';
import { co, day, inr } from '../format';
import { RecordForm, useCustomFields, type Spec } from '../ui/forms';
import { Btn, ConfirmBtn, DataTable, Field, ItemSearch, Kv, Modal, Page, Panel, Pill, StudentSearch, useAction, useLoad, useToast, useUrlState } from '../ui/kit';

type Tab = { k: string; label: string; ok: boolean; el: JSX.Element };

export default function Setup() {
  const { can } = useAuth();
  const set = can('edit', 'set'), inv = can('master', 'inv'), acc = can('master', 'acc'), ctl = can('view', 'ctl') || can('users', 'ctl');
  const tabs: Tab[] = [
    { k: 'companies', label: 'Companies', ok: can('view', 'set'), el: <Companies edit={set} /> },
    { k: 'locations', label: 'Locations', ok: can('view', 'set'), el: <Locations edit={set} /> },
    { k: 'zones', label: 'Zones & regions', ok: can('view', 'set'), el: <Zones edit={set} /> },
    { k: 'counters', label: 'Counters', ok: can('view', 'set'), el: <Counters edit={set} /> },
    { k: 'receipt', label: 'Receipt & printer', ok: can('view', 'set'), el: <Receipt edit={set} /> },
    { k: 'schools', label: 'Schools', ok: can('view', 'inv'), el: <Schools edit={inv} /> },
    { k: 'categories', label: 'Item categories', ok: can('view', 'inv'), el: <Categories edit={inv} /> },
    { k: 'packages', label: 'Packages', ok: can('view', 'inv'), el: <Packages edit={inv} /> },
    { k: 'schemes', label: 'Discount schemes', ok: can('view', 'set'), el: <Schemes edit={set} /> },
    { k: 'suppliers', label: 'Suppliers', ok: can('view', 'pur') || can('view', 'inv'), el: <Suppliers edit={inv} /> },
    { k: 'customers', label: 'Customers', ok: can('view', 'acc') || can('view', 'pos'), el: <Customers edit={inv || acc} add={can('add', 'acc')} /> },
    { k: 'students', label: 'Students', ok: can('view', 'pos') || can('view', 'msea') || can('view', 'set'), el: <Students edit={inv || acc || set} /> },
    { k: 'accounts', label: 'Chart of accounts', ok: can('view', 'acc'), el: <Accounts edit={acc} /> },
    { k: 'roles', label: 'Roles & permissions', ok: ctl, el: <Roles edit={can('users', 'ctl')} /> },
    { k: 'fields', label: 'Custom fields', ok: can('view', 'set'), el: <CustomFields edit={set} /> },
  ].filter(t => t.ok);
  const [tab, setTab] = useUrlState<string>('tab', tabs[0]?.k || '');
  const cur = tabs.find(t => t.k === tab);
  return (
    <Page title="Setup" sub="Companies, locations, masters, roles and pricing rules. Every change needs the right permission and is recorded in the audit trail.">
      <div className="report-pick" role="tablist">{tabs.map(t => <button key={t.k} className={t.k === tab ? 'on' : ''} onClick={() => setTab(t.k)}>{t.label}</button>)}</div>
      {cur?.el}
    </Page>
  );
}

// ------------------------------------------------------------ generic list + edit dialog
function Master<T extends Record<string, any>>({ title, rows, cols, specs, blank, edit, save, keyOf, extra, footer, addLabel = 'Add' }: {
  title: string; rows: T[] | null; cols: any[]; specs: Spec[]; blank: any; edit: boolean; keyOf?: (r: T) => string | number;
  save: (v: any, isNew: boolean) => Promise<any>; extra?: (v: any, isNew: boolean) => JSX.Element | null; footer?: (v: any, close: () => void) => JSX.Element | null; addLabel?: string;
}) {
  const [cur, setCur] = useState<any>(null);
  const [isNew, setIsNew] = useState(false);
  const [run, busy] = useAction();
  const close = () => setCur(null);
  return <Panel title={title} actions={edit && addLabel && <Btn kind="pri" onClick={() => { setIsNew(true); setCur({ ...blank }); }}>{addLabel}</Btn>}>
    <DataTable rows={rows} cols={cols} keyOf={keyOf} onRow={r => { setIsNew(false); setCur({ ...r }); }} />
    {rows && rows.length > 0 && <p className="muted small">Click a row to {edit ? 'view or change it' : 'view it'}.</p>}
    {cur && <Modal wide title={isNew ? `${addLabel} · ${title}` : title} onClose={close} footer={<>
      {!isNew && footer?.(cur, close)}
      <Btn onClick={close}>{edit ? 'Cancel' : 'Close'}</Btn>{edit && <Btn kind="pri" busy={busy} onClick={async () => { if (await run(() => save(cur, isNew), () => 'Saved.')) close(); }}>Save</Btn>}</>}>
      <RecordForm specs={specs} value={cur} onChange={setCur} editing={!isNew} />
      {extra?.(cur, isNew)}
    </Modal>}
  </Panel>;
}
const yes = (b: any) => (b ? <Pill s="Active" /> : <Pill s="Inactive" />);
const reload = <T,>(l: { reload: () => void }, p: Promise<T>) => p.then(r => { l.reload(); return r; });

// ------------------------------------------------------------ companies
function Companies({ edit }: { edit: boolean }) {
  const l = useLoad<any[]>(() => api('/setup/companies'), []);
  return <Master title="Companies" rows={l.data} edit={edit} blank={{}} keyOf={r => r.code} addLabel="" cols={[
    { key: 'code', label: 'Code', primary: true }, { key: 'name', label: 'Registered name' }, { key: 'gstin', label: 'GSTIN' },
    { key: 'books_locked_to', label: 'Books locked to', hideSmall: true, render: (r: any) => day(r.books_locked_to) }]}
    specs={[{ k: 'code', label: 'Code', lockOnEdit: true }, { k: 'name', label: 'Registered name', required: true }, { k: 'short_name', label: 'Short name' },
      { k: 'gstin', label: 'GSTIN', hint: 'State code is taken from the first two digits' }, { k: 'phone', label: 'Phone' }, { k: 'email', label: 'E-mail', type: 'email' },
      { k: 'address', label: 'Registered address (printed on bills)', type: 'textarea' }]}
    save={v => reload(l, api(`/setup/companies/${v.code}`, { method: 'PUT', body: { name: v.name, short_name: v.short_name, gstin: v.gstin, phone: v.phone, email: v.email, address: v.address } }))} />;
}

// ------------------------------------------------------------ locations
function Locations({ edit }: { edit: boolean }) {
  const { org, refreshOrg } = useAuth();
  const l = useLoad<any[]>(() => api('/setup/locations'), []);
  const zones = useLoad<any>(() => api('/setup/regions'), []);
  const [run, busy] = useAction();
  const [reason, setReason] = useState('');
  const save = async (v: any, isNew: boolean) => {
    const companies = Object.fromEntries((org?.companies || []).filter(c => v['co_' + c.code]).map(c => [c.code, v['series_' + c.code] || v.code]));
    const body = { code: v.code, name: v.name, public_name: v.public_name, type: v.type, zone_id: v.zone_id || null, address: v.address, contact: v.contact, companies };
    const r = await (isNew ? api('/setup/locations', { body }) : api(`/setup/locations/${v.code}`, { method: 'PUT', body }));
    l.reload(); refreshOrg(); return r;
  };
  const rows = (l.data || []).map(x => ({ ...x, ...Object.fromEntries((x.branches || []).filter((b: any) => b.active).flatMap((b: any) => [['co_' + b.company, true], ['series_' + b.company, b.code]])) }));
  const specs: Spec[] = [
    { k: 'code', label: 'Code (in bill numbers, e.g. TLC)', required: true, lockOnEdit: true }, { k: 'name', label: 'Name', required: true },
    { k: 'public_name', label: 'Name shown to parents' }, { k: 'type', label: 'Type', type: 'select', required: true, options: [['Store', 'Store'], ['Counter', 'School counter / branch'], ['Warehouse', 'Warehouse'], ['Godown', 'Godown'], ['Office', 'Office']] },
    { k: 'zone_id', label: 'Zone', type: 'select', options: (zones.data?.zones || []).map((z: any) => [z.id, z.name]) }, { k: 'contact', label: 'Contact person and phone' },
    { k: 'address', label: 'Address', type: 'textarea' },
    ...(org?.companies || []).flatMap(c => [{ k: 'co_' + c.code, label: `${c.name} sells / stocks here`, type: 'check' as const },
      { k: 'series_' + c.code, label: `${c.short_name || c.code} invoice series code`, show: (v: any) => !!v['co_' + c.code], placeholder: 'defaults to the location code' }]),
  ];
  return <Master title="Locations" rows={rows} edit={edit} keyOf={r => r.code} blank={{ type: 'Store' }} specs={specs} save={save} addLabel="Open location" cols={[
    { key: 'code', label: 'Code' }, { key: 'name', label: 'Location', primary: true }, { key: 'type', label: 'Type' }, { key: 'zone_id', label: 'Zone', hideSmall: true },
    { key: 'branches', label: 'Companies', render: (r: any) => (r.branches || []).filter((b: any) => b.active).map((b: any) => `${b.company} (${b.code})`).join(', ') || '—' },
    { key: 'active', label: 'Status', render: (r: any) => (r.active ? <Pill s="Open" tone="ok" /> : <Pill s={`Closed ${day(r.closed_on)}`} tone="bad" />) }]}
    extra={(v, isNew) => (!isNew && !v.active ? <p className="note">Closed {day(v.closed_on)}: {v.closed_reason}</p> : null)}
    footer={(v, close) => (edit ? <><input placeholder="Reason" value={reason} onChange={e => setReason(e.target.value)} aria-label="Reason" />
      {v.active ? <ConfirmBtn label="Close location" busy={busy} onConfirm={async () => { if (await run(() => api(`/setup/locations/${v.code}/close`, { body: { reason } }), () => 'Location closed.')) { l.reload(); refreshOrg(); close(); } }} />
        : <Btn busy={busy} onClick={async () => { if (await run(() => api(`/setup/locations/${v.code}/reopen`, { body: { reason } }), () => 'Location reopened.')) { l.reload(); refreshOrg(); close(); } }}>Reopen</Btn>}</> : null)} />;
}

function Zones({ edit }: { edit: boolean }) {
  const l = useLoad<any>(() => api('/setup/regions'), []);
  return <div className="grid2">
    <Master title="Regions" rows={l.data?.regions} edit={edit} keyOf={r => r.id} blank={{}} cols={[{ key: 'id', label: 'Code' }, { key: 'name', label: 'Region', primary: true }]}
      specs={[{ k: 'id', label: 'Code', required: true, lockOnEdit: true }, { k: 'name', label: 'Name', required: true }]}
      save={(v, n) => reload(l, n ? api('/setup/regions', { body: v }) : api(`/setup/regions/${v.id}`, { method: 'PUT', body: { name: v.name } }))} />
    <Master title="Zones" rows={l.data?.zones} edit={edit} keyOf={r => r.id} blank={{}} cols={[{ key: 'id', label: 'Code' }, { key: 'name', label: 'Zone', primary: true }, { key: 'region_id', label: 'Region' }]}
      specs={[{ k: 'id', label: 'Code', required: true, lockOnEdit: true }, { k: 'name', label: 'Name', required: true },
        { k: 'region_id', label: 'Region', type: 'select', required: true, options: (l.data?.regions || []).map((r: any) => [r.id, r.name]) }]}
      save={(v, n) => reload(l, n ? api('/setup/zones', { body: v }) : api(`/setup/zones/${v.id}`, { method: 'PUT', body: { name: v.name, region_id: v.region_id } }))} />
  </div>;
}

// ------------------------------------------------------------ counters and receipt settings (stored in settings)
function Counters({ edit }: { edit: boolean }) {
  const { org } = useAuth();
  const { data, reload: rl } = useLoad<any>(() => api('/settings/public'), []);
  const [rows, setRows] = useState<any[] | null>(null);
  const [reason, setReason] = useState('');
  const [run, busy] = useAction();
  const list = rows ?? data?.counters ?? [];
  const upd = (i: number, p: any) => setRows(list.map((r: any, j: number) => (j === i ? { ...r, ...p } : r)));
  const locs = (org?.locations || []).filter((x: any) => x.active && x.type !== 'Warehouse');
  return <Panel title="Billing counters" actions={edit && <Btn onClick={() => setRows([...list, { id: 'CT' + String(list.length + 1).padStart(3, '0'), loc: locs[0]?.code, name: 'Counter ' + (list.length + 1), device: '', active: true }])}>Add counter</Btn>}>
    <div className="table-wrap"><table className="rtable"><thead><tr><th>Code</th><th>Location</th><th>Name</th><th>Device / printer</th><th>Active</th></tr></thead><tbody>
      {list.map((r: any, i: number) => <tr key={r.id}><td data-label="Code" className="mono">{r.id}</td>
        <td data-label="Location"><select value={r.loc} disabled={!edit} onChange={e => upd(i, { loc: e.target.value })}>{locs.map((x: any) => <option key={x.code} value={x.code}>{x.name}</option>)}</select></td>
        <td data-label="Name"><input value={r.name} readOnly={!edit} onChange={e => upd(i, { name: e.target.value })} /></td>
        <td data-label="Device"><input value={r.device || ''} readOnly={!edit} onChange={e => upd(i, { device: e.target.value })} /></td>
        <td data-label="Active"><input type="checkbox" checked={r.active !== false} disabled={!edit} onChange={e => upd(i, { active: e.target.checked })} /></td></tr>)}
    </tbody></table></div>
    {edit && rows && <div className="row"><input placeholder="Reason for the change" value={reason} onChange={e => setReason(e.target.value)} aria-label="Reason" />
      <Btn kind="pri" busy={busy} onClick={async () => { if (await run(() => api('/settings/counters', { method: 'PUT', body: { value: rows, reason } }), () => 'Counters saved.')) { setRows(null); rl(); } }}>Save counters</Btn></div>}
  </Panel>;
}

function Receipt({ edit }: { edit: boolean }) {
  const { data, reload: rl } = useLoad<any>(() => api('/settings/public'), []);
  const [v, setV] = useState<any>(null);
  const [reason, setReason] = useState('');
  const [run, busy] = useAction();
  if (!data) return null;
  const cur = v ?? { printer: '80', ...(data.pos || {}) };
  return <Panel title="Receipt and printer">
    <RecordForm value={cur} onChange={setV} specs={[
      { k: 'printer', label: 'Bill format', type: 'select', options: [['80', 'Thermal receipt 80 mm'], ['58', 'Thermal receipt 58 mm'], ['A4', 'A4 / A5 tax invoice']] },
      { k: 'thanks', label: 'Thank-you line', wide: true }, { k: 'policyKG', label: 'Return / exchange policy · Kiswa', type: 'textarea' },
      { k: 'policyZE', label: 'Return / exchange policy · Zaibus', type: 'textarea' }]} />
    <p className="muted">The bill uses this format when printed from the counter. Thermal printers must be set to the same paper width in the device's print settings.</p>
    {edit && v && <div className="row"><input placeholder="Reason for the change" value={reason} onChange={e => setReason(e.target.value)} aria-label="Reason" />
      <Btn kind="pri" busy={busy} onClick={async () => { if (await run(() => api('/settings/pos', { method: 'PUT', body: { value: v, reason } }), () => 'Saved.')) { setV(null); rl(); } }}>Save</Btn></div>}
  </Panel>;
}

// ------------------------------------------------------------ schools, categories, packages, schemes
function Schools({ edit }: { edit: boolean }) {
  const l = useLoad<any[]>(() => api('/schools'), []);
  return <Master title="Schools" rows={l.data} edit={edit} keyOf={r => r.id} blank={{ active: true }} cols={[{ key: 'id', label: 'Code' }, { key: 'name', label: 'School', primary: true },
    { key: 'board', label: 'Board' }, { key: 'area', label: 'Area', hideSmall: true }, { key: 'active', label: 'Status', render: (r: any) => yes(r.active) }]}
    specs={[{ k: 'id', label: 'School code', required: true, lockOnEdit: true }, { k: 'name', label: 'Name', required: true }, { k: 'board', label: 'Board' }, { k: 'area', label: 'Area' }, { k: 'active', label: 'Active', type: 'check' }]}
    save={(v, n) => reload(l, n ? api('/setup/schools', { body: v }) : api(`/setup/schools/${v.id}`, { method: 'PUT', body: { name: v.name, board: v.board, area: v.area, active: !!v.active } }))} />;
}

const GST: [string, string][] = ['0', '5', '12', '18', '28', '40'].map(g => [g, g + '%']);
function Categories({ edit }: { edit: boolean }) {
  const { org } = useAuth();
  const l = useLoad<any[]>(() => api('/categories'), []);
  return <Master title="Item categories" rows={l.data} edit={edit} keyOf={r => r.company + r.name} blank={{ company: org?.companies[0]?.code, gst_rate: '5', active: true }} cols={[
    { key: 'name', label: 'Category', primary: true }, { key: 'company', label: 'Co', render: (r: any) => co(r.company) }, { key: 'hsn', label: 'HSN' },
    { key: 'gst_rate', label: 'GST', align: 'r', render: (r: any) => r.gst_rate + '%' }, { key: 'max_disc', label: 'Max disc', align: 'r', hideSmall: true, render: (r: any) => (r.max_disc != null ? r.max_disc + '%' : '') },
    { key: 'active', label: 'Status', render: (r: any) => yes(r.active) }]}
    specs={[{ k: 'company', label: 'Company', type: 'select', required: true, lockOnEdit: true, options: (org?.companies || []).map(c => [c.code, c.name]) },
      { k: 'name', label: 'Category name', required: true, lockOnEdit: true }, { k: 'hsn', label: 'HSN code' }, { k: 'gst_rate', label: 'GST rate', type: 'select', options: GST },
      { k: 'max_disc', label: 'Maximum discount % (blank = role limit)', type: 'number' }, { k: 'isbn_required', label: 'ISBN required (books)', type: 'check' }, { k: 'active', label: 'Active', type: 'check' }]}
    save={(v, n) => reload(l, n ? api('/categories', { body: v }) : api(`/setup/categories/${v.company}/${encodeURIComponent(v.name)}`, { method: 'PUT',
      body: { hsn: v.hsn, gst_rate: v.gst_rate, max_disc: v.max_disc, isbn_required: !!v.isbn_required, active: !!v.active } }))} />;
}

function Packages({ edit }: { edit: boolean }) {
  const { org } = useAuth();
  const l = useLoad<any[]>(() => api('/setup/packages'), []);
  const schools = useLoad<any[]>(() => api('/schools'), []);
  const [cur, setCur] = useState<any>(null);
  const cats = useLoad<any[]>(() => (cur ? api(`/categories?company=${cur.company}`) : Promise.resolve([])), [cur?.company]);
  const [run, busy] = useAction();
  const save = async () => {
    const body = { company: cur.company, name: cur.name, school_id: cur.school_id || null, class_name: cur.class_name, gender: cur.gender || null, disc_pct: Number(cur.disc_pct) || 0,
      active: cur.active !== false, lines: cur.lines.map((x: any) => (x.item_id ? { item_id: x.item_id, qty: Number(x.qty) } : { category: x.category, qty: Number(x.qty) })) };
    if (await run(() => (cur.id ? api(`/setup/packages/${cur.id}`, { method: 'PUT', body }) : api('/setup/packages', { body })), () => 'Package saved.')) { setCur(null); l.reload(); }
  };
  const setLine = (i: number, p: any) => setCur((c: any) => ({ ...c, lines: c.lines.map((x: any, j: number) => (j === i ? { ...x, ...p } : x)) }));
  return <Panel title="Packages (sets sold together)" actions={edit && <Btn kind="pri" onClick={() => setCur({ company: org?.companies[0]?.code, disc_pct: 0, lines: [], active: true })}>Add package</Btn>}>
    <DataTable rows={l.data} onRow={r => setCur({ ...r })} cols={[{ key: 'name', label: 'Package', primary: true }, { key: 'company', label: 'Co', render: (r: any) => co(r.company) },
      { key: 'gender', label: 'For', render: (r: any) => [r.gender, r.class_name && 'Class ' + r.class_name].filter(Boolean).join(' · ') || 'All' },
      { key: 'lines', label: 'Lines', align: 'r', render: (r: any) => r.lines.length }, { key: 'disc_pct', label: 'Discount', align: 'r', render: (r: any) => r.disc_pct + '%' },
      { key: 'active', label: 'Status', render: (r: any) => yes(r.active) }]} />
    {cur && <Modal wide title={cur.id ? `Package ${cur.id}` : 'New package'} onClose={() => setCur(null)} footer={<><Btn onClick={() => setCur(null)}>Cancel</Btn>{edit && <Btn kind="pri" busy={busy} onClick={save}>Save</Btn>}</>}>
      <RecordForm value={cur} onChange={setCur} editing={!!cur.id} specs={[
        { k: 'company', label: 'Company', type: 'select', required: true, lockOnEdit: true, options: (org?.companies || []).map(c => [c.code, c.name]) }, { k: 'name', label: 'Name', required: true },
        { k: 'school_id', label: 'School', type: 'select', options: (schools.data || []).map(s => [s.id, s.name]) }, { k: 'class_name', label: 'Class' },
        { k: 'gender', label: 'For', type: 'select', options: [['Boys', 'Boys'], ['Girls', 'Girls'], ['Unisex', 'Everyone']] }, { k: 'disc_pct', label: 'Package discount %', type: 'number' },
        { k: 'active', label: 'Active', type: 'check' }]} />
      <h4>Lines — a category (size chosen at the counter) or a fixed item</h4>
      {cur.lines.map((x: any, i: number) => <div className="jv-line" key={i}>
        {x.item_id ? <span className="mono">{x.item_id}</span> : <select value={x.category || ''} onChange={e => setLine(i, { category: e.target.value })} aria-label="Category"><option value="">Category…</option>{(cats.data || []).map(c => <option key={c.name}>{c.name}</option>)}</select>}
        <input inputMode="numeric" value={x.qty} onChange={e => setLine(i, { qty: e.target.value })} aria-label="Quantity" />
        <span />
        <button className="x" onClick={() => setCur((c: any) => ({ ...c, lines: c.lines.filter((_: any, j: number) => j !== i) }))} aria-label="Remove line">×</button></div>)}
      <div className="row"><Btn kind="link" onClick={() => setCur((c: any) => ({ ...c, lines: [...c.lines, { category: '', qty: 1 }] }))}>+ Category line</Btn></div>
      <ItemSearch company={cur.company} placeholder="+ Fixed item (e.g. a textbook)" onPick={it => setCur((c: any) => ({ ...c, lines: [...c.lines, { item_id: it.id, qty: 1 }] }))} />
    </Modal>}
  </Panel>;
}

function Schemes({ edit }: { edit: boolean }) {
  const { org } = useAuth();
  const l = useLoad<any[]>(() => api('/setup/schemes'), []);
  const schools = useLoad<any[]>(() => api('/schools'), []);
  const cats = useLoad<any[]>(() => api('/categories'), []);
  return <Master title="Discount schemes" rows={l.data} edit={edit} blank={{ company: org?.companies[0]?.code, scope: 'category', online: true, active: true }} addLabel="Add scheme" cols={[
    { key: 'name', label: 'Scheme', primary: true }, { key: 'company', label: 'Co', render: (r: any) => co(r.company) },
    { key: 'scope', label: 'Applies to', render: (r: any) => ({ all: 'All items', category: r.category, school: 'School ' + r.school_id, item: r.item_id } as any)[r.scope] },
    { key: 'pct', label: 'Discount', align: 'r', render: (r: any) => r.pct + '%' }, { key: 'valid', label: 'Period', hideSmall: true, render: (r: any) => `${day(r.valid_from)} – ${day(r.valid_to)}` },
    { key: 'active', label: 'Status', render: (r: any) => yes(r.active) }]}
    specs={[{ k: 'company', label: 'Company', type: 'select', required: true, lockOnEdit: true, options: (org?.companies || []).map(c => [c.code, c.name]) }, { k: 'name', label: 'Scheme name', required: true },
      { k: 'scope', label: 'Applies to', type: 'select', required: true, options: [['all', 'All items'], ['category', 'One category'], ['school', 'One school'], ['item', 'One item']] },
      { k: 'category', label: 'Category', type: 'select', show: v => v.scope === 'category', options: (cats.data || []).map(c => [c.name, `${c.company} · ${c.name}`]) },
      { k: 'school_id', label: 'School', type: 'select', show: v => v.scope === 'school', options: (schools.data || []).map(s => [s.id, s.name]) },
      { k: 'item_id', label: 'Item code', show: v => v.scope === 'item', placeholder: 'e.g. KG-MSA-TIE-Std' }, { k: 'pct', label: 'Discount %', type: 'number', required: true },
      { k: 'valid_from', label: 'From', type: 'date' }, { k: 'valid_to', label: 'To', type: 'date' }, { k: 'online', label: 'Also in the parent online store', type: 'check' },
      { k: 'active', label: 'Active', type: 'check' }]}
    extra={() => <p className="muted">The best matching scheme is applied automatically at the counter (not on MSEA-sponsored issues). A cashier may give more only within their discount limit.</p>}
    save={(v, n) => reload(l, n ? api('/setup/schemes', { body: v }) : api(`/setup/schemes/${v.id}`, { method: 'PUT', body: v }))} />;
}

// ------------------------------------------------------------ suppliers, customers, students (with custom fields)
function Suppliers({ edit }: { edit: boolean }) {
  const { org } = useAuth();
  const l = useLoad<any[]>(() => api('/suppliers'), []);
  return <Master title="Suppliers" rows={l.data} edit={edit} blank={{ company: org?.companies[0]?.code, terms_days: 30, active: true }} addLabel="Add supplier" cols={[
    { key: 'name', label: 'Supplier', primary: true }, { key: 'company', label: 'Co', render: (r: any) => co(r.company) }, { key: 'gstin', label: 'GSTIN', hideSmall: true },
    { key: 'city', label: 'City' }, { key: 'terms_days', label: 'Terms', align: 'r', render: (r: any) => r.terms_days + ' d' }, { key: 'active', label: 'Status', render: (r: any) => yes(r.active) }]}
    specs={[{ k: 'company', label: 'Company', type: 'select', required: true, lockOnEdit: true, options: (org?.companies || []).map(c => [c.code, c.name]) }, { k: 'name', label: 'Supplier name', required: true },
      { k: 'gstin', label: 'GSTIN' }, { k: 'city', label: 'City' }, { k: 'contact', label: 'Contact person and phone' }, { k: 'terms_days', label: 'Payment terms (days)', type: 'number' },
      { k: 'bank_name', label: 'Bank' }, { k: 'bank_account', label: 'Account number' }, { k: 'bank_ifsc', label: 'IFSC' }, { k: 'active', label: 'Active', type: 'check' }]}
    extra={(v, n) => <CustomBlock entity="supplier" refId={n ? undefined : v.id} />}
    save={async (v, n) => { const r = await (n ? api('/suppliers', { body: v }) : api(`/setup/suppliers/${v.id}`, { method: 'PUT', body: v })); await saveCustom('supplier', r.id || v.id); l.reload(); return r; }} />;
}

// custom-field values ride along with the record form; the latest block registers its save function here
let pendingCustom: ((ref: string) => Promise<any>) | null = null;
const saveCustom = async (_e: string, ref: string) => { if (pendingCustom && ref) await pendingCustom(ref); };
function CustomBlock({ entity, refId }: { entity: 'item' | 'customer' | 'supplier' | 'student'; refId?: string }) {
  const { block, save } = useCustomFields(entity, refId);
  pendingCustom = save;
  useEffect(() => () => { pendingCustom = null; }, []);
  return block;
}

function Customers({ edit, add }: { edit: boolean; add: boolean }) {
  const { org } = useAuth();
  const [q, setQ] = useState('');
  const l = useLoad<any[]>(() => api(`/customers?q=${encodeURIComponent(q)}`), [q]);
  return <>
    <div className="filters"><input placeholder="Name, mobile or admission no." value={q} onChange={e => setQ(e.target.value)} aria-label="Search customers" /></div>
    <Master title="Customers" rows={(l.data || []).filter(c => c.type !== 'Student' || q)} edit={edit || add} blank={{ company: org?.companies[0]?.code, type: 'Institution' }} addLabel="Add customer" cols={[
      { key: 'name', label: 'Customer', primary: true }, { key: 'id', label: 'Code', hideSmall: true }, { key: 'type', label: 'Type' }, { key: 'company', label: 'Co', render: (r: any) => co(r.company) },
      { key: 'mobile', label: 'Mobile', hideSmall: true }, { key: 'credit_limit', label: 'Credit limit', align: 'r', render: (r: any) => (r.credit_limit ? inr(r.credit_limit) : '') }]}
      specs={[{ k: 'company', label: 'Company', type: 'select', required: true, lockOnEdit: true, options: (org?.companies || []).map(c => [c.code, c.name]) },
        { k: 'type', label: 'Type', type: 'select', lockOnEdit: true, options: [['Institution', 'Institution'], ['Walk-in', 'Walk-in'], ['Staff', 'Staff']] }, { k: 'name', label: 'Name', required: true },
        { k: 'mobile', label: 'Mobile', type: 'tel' }, { k: 'gstin', label: 'GSTIN' }, { k: 'credit_limit', label: 'Credit limit (₹)', type: 'number' }]}
      extra={(v, n) => <><CustomBlock entity="customer" refId={n ? undefined : v.id} />{v.type === 'Student' && <p className="muted">Student customers follow the ERP record; only the mobile can be changed here.</p>}</>}
      save={async (v, n) => { const r = await (n ? api('/customers', { body: v }) : api(`/setup/customers/${v.id}`, { method: 'PUT', body: v.type === 'Student' ? { mobile: v.mobile } : v })); await saveCustom('customer', r.id || v.id); l.reload(); return r; }} />
  </>;
}

function Students({ edit }: { edit: boolean }) {
  const [st, setSt] = useState<any>(null);
  const [run, busy] = useAction();
  return <Panel title="Students (from the MSEA ERP)">
    <p className="muted">Student records come from the ERP import (Settings → ERP import). Here you can look a student up and keep extra details in custom fields.</p>
    <StudentSearch onPick={setSt} />
    {st && <Modal title={`${st.name} · ${st.adm_no}`} onClose={() => setSt(null)} footer={<><Btn onClick={() => setSt(null)}>Close</Btn>
      {edit && <Btn kind="pri" busy={busy} onClick={async () => { if (await run(() => saveCustom('student', st.adm_no), () => 'Saved.')) setSt(null); }}>Save details</Btn>}</>}>
      <Kv items={[['Class', `${st.class_name}${st.section ? '-' + st.section : ''}`], ['Category', st.category], ['Status', st.status], ['School', st.school_id], ['Parent mobile', st.parent_mobile]]} />
      <CustomBlock entity="student" refId={st.adm_no} />
    </Modal>}
  </Panel>;
}

// ------------------------------------------------------------ chart of accounts
function Accounts({ edit }: { edit: boolean }) {
  const { org } = useAuth();
  const [company, setCompany] = useState(org?.companies[0]?.code || 'KG');
  const l = useLoad<any[]>(() => api(`/accounts?company=${company}`), [company]);
  const cat = useLoad<any>(() => api('/setup/catalog'), []);
  const control = new Set<string>(cat.data?.control_accounts || []);
  const G: Record<string, string> = { A: 'Asset', L: 'Liability', E: 'Equity', I: 'Income', X: 'Expense' };
  return <>
    <div className="filters"><select value={company} onChange={e => setCompany(e.target.value)} aria-label="Company">{(org?.companies || []).map(c => <option key={c.code} value={c.code}>{c.name}</option>)}</select></div>
    <Master title="Chart of accounts" rows={l.data} edit={edit} keyOf={r => r.code} blank={{ company }} addLabel="Add account" cols={[
      { key: 'code', label: 'Code', render: (r: any) => <span className="mono">{r.code}</span> }, { key: 'name', label: 'Account', primary: true }, { key: 'grp', label: 'Group', render: (r: any) => G[r.grp] },
      { key: 'type', label: 'Type', render: (r: any) => (control.has(r.code) ? <Pill s="System" tone="info" /> : '') }, { key: 'active', label: 'Status', render: (r: any) => yes(r.active) }]}
      specs={[{ k: 'code', label: 'Code (4 digits: 1 asset, 2 liability, 3 equity, 4 income, 5-6 expense)', required: true, lockOnEdit: true }, { k: 'name', label: 'Account name', required: true },
        { k: 'active', label: 'Active', type: 'check', show: v => !!v.grp && !control.has(v.code) }]}
      extra={v => (control.has(v.code) ? <p className="muted">System account: bills, receipts, stock and GST post to it automatically. It can be renamed but not switched off; sub-ledger accounts (receivables, payables, stock) cannot be used in manual journals.</p> : null)}
      save={(v, n) => reload(l, n ? api('/setup/accounts', { body: { company, code: v.code, name: v.name } }) : api(`/setup/accounts/${company}/${v.code}`, { method: 'PUT', body: { name: v.name, active: !!v.active } }))} />
  </>;
}

// ------------------------------------------------------------ roles & permissions
function Roles({ edit }: { edit: boolean }) {
  const toast = useToast();
  const l = useLoad<any[]>(() => api('/setup/roles'), []);
  const cat = useLoad<any>(() => api('/setup/catalog'), []);
  const [sel, setSel] = useState<any>(null);
  const [nw, setNw] = useState<any>(null);
  const [reason, setReason] = useState('');
  const [run, busy] = useAction();
  const save = async () => {
    if (!reason.trim()) { toast('Give the reason for this change.', 'bad'); return; }
    const body = { description: sel.description, approval_rank: Number(sel.approval_rank) || 0, read_only: !!sel.read_only, active: sel.active !== 0 && sel.active !== false,
      permissions: sel.permissions, limits: sel.limits, reason };
    if (await run(() => api(`/setup/roles/${encodeURIComponent(sel.name)}`, { method: 'PUT', body }), (x: any) => (x.status === 'Applied' ? 'Role saved.' : ''))) { setSel(null); setReason(''); l.reload(); }
  };
  const toggle = (key: string, a: string) => setSel((s: any) => { const cur = new Set<string>(s.permissions[key] || []); if (cur.has(a)) cur.delete(a); else cur.add(a); return { ...s, permissions: { ...s.permissions, [key]: [...cur] } }; });
  const screens: [string, string][] = cat.data?.screens || [];
  const actions: string[] = cat.data?.actions || [];
  return <Panel title="Roles" actions={edit && <Btn kind="pri" onClick={() => setNw({ name: '', copy_from: '' })}>New role</Btn>}>
    <DataTable rows={l.data} onRow={r => setSel(JSON.parse(JSON.stringify(r)))} keyOf={r => r.name} cols={[{ key: 'name', label: 'Role', primary: true }, { key: 'description', label: 'Description', hideSmall: true },
      { key: 'approval_rank', label: 'Rank', align: 'r' }, { key: 'users', label: 'Users', align: 'r' }, { key: 'read_only', label: '', render: (r: any) => (r.read_only ? <Pill s="Read-only" tone="info" /> : '') },
      { key: 'active', label: 'Status', render: (r: any) => yes(r.active) }]} />
    {sel && <Modal wide title={`Role · ${sel.name}`} onClose={() => setSel(null)} footer={edit && sel.name !== 'Super Admin' ? <>
      <input placeholder="Reason for the change" value={reason} onChange={e => setReason(e.target.value)} aria-label="Reason" /><Btn kind="pri" busy={busy} onClick={save}>Submit</Btn></> : undefined}>
      {sel.name === 'Super Admin' && <p className="note info">The Super Admin role is fixed and always has every right on every screen and the highest approval and transaction limits. Only two rules still apply: nobody approves their own request, and each user sees only the companies and locations assigned to them.</p>}
      <div className="form grid2">
        <Field label="Description"><input value={sel.description || ''} onChange={e => setSel({ ...sel, description: e.target.value })} /></Field>
        <Field label="Approval rank (0 = cannot approve)"><input inputMode="decimal" value={sel.approval_rank} onChange={e => setSel({ ...sel, approval_rank: e.target.value })} /></Field>
        <label className="check"><input type="checkbox" checked={!!sel.read_only} onChange={e => setSel({ ...sel, read_only: e.target.checked })} />Read-only (auditors)</label>
      </div>
      <h4>Permissions</h4>
      <div className="table-wrap perm"><table><thead><tr><th>Screen</th>{actions.map(a => <th key={a} className="vert"><span>{a}</span></th>)}</tr></thead><tbody>
        {screens.map(([k, label]) => <tr key={k}><th>{label}</th>{actions.map(a => <td key={a}><input type="checkbox" aria-label={`${label} ${a}`} checked={(sel.permissions[k] || []).includes(a)} onChange={() => toggle(k, a)} /></td>)}</tr>)}
      </tbody></table></div>
      <h4>Approval limits</h4>
      <div className="form grid2">{(cat.data?.limits || []).map(([k, label]: [string, string]) => <Field key={k} label={label}>
        <input inputMode="decimal" value={sel.limits[k] ?? ''} onChange={e => setSel({ ...sel, limits: { ...sel.limits, [k]: e.target.value } })} placeholder="0 = must ask" /></Field>)}</div>
      <p className="muted">A user may approve up to their highest role limit. Changes go to a second person for approval when maker-checker is on.</p>
    </Modal>}
    {nw && <Modal title="New role" onClose={() => setNw(null)} footer={<><Btn onClick={() => setNw(null)}>Cancel</Btn><Btn kind="pri" busy={busy} onClick={async () => {
      if (await run(() => api('/setup/roles', { body: nw }), (x: any) => (x.status === 'Applied' ? 'Role created.' : ''))) { setNw(null); l.reload(); } }}>Create</Btn></>}>
      <div className="form">
        <Field label="Role name"><input value={nw.name} onChange={e => setNw({ ...nw, name: e.target.value })} /></Field>
        <Field label="Start from the permissions of"><select value={nw.copy_from} onChange={e => setNw({ ...nw, copy_from: e.target.value })}><option value="">Nothing (empty)</option>{(l.data || []).map(r => <option key={r.name}>{r.name}</option>)}</select></Field>
        <Field label="Description"><input value={nw.description || ''} onChange={e => setNw({ ...nw, description: e.target.value })} /></Field>
        <Field label="Reason"><input value={nw.reason || ''} onChange={e => setNw({ ...nw, reason: e.target.value })} /></Field>
      </div>
    </Modal>}
  </Panel>;
}

// ------------------------------------------------------------ custom fields
function CustomFields({ edit }: { edit: boolean }) {
  const l = useLoad<any[]>(() => api('/setup/custom-fields'), []);
  const rows = useMemo(() => (l.data || []).map(f => ({ ...f, options: Array.isArray(f.options) ? f.options.join(', ') : f.options || '' })), [l.data]);
  return <Master title="Custom fields" rows={l.data ? rows : null} edit={edit} blank={{ entity: 'item', type: 'text', active: true }} addLabel="Add field" cols={[
    { key: 'label', label: 'Field', primary: true }, { key: 'entity', label: 'On' }, { key: 'type', label: 'Type' }, { key: 'options', label: 'Options', hideSmall: true },
    { key: 'required', label: 'Required', render: (r: any) => (r.required ? 'Yes' : '') }, { key: 'active', label: 'Status', render: (r: any) => yes(r.active) }]}
    specs={[{ k: 'entity', label: 'Shown on', type: 'select', required: true, lockOnEdit: true, options: [['item', 'Items'], ['customer', 'Customers'], ['supplier', 'Suppliers'], ['student', 'Students']] },
      { k: 'label', label: 'Label', required: true }, { k: 'type', label: 'Type', type: 'select', options: [['text', 'Text'], ['number', 'Number'], ['date', 'Date'], ['select', 'Drop-down']] },
      { k: 'options', label: 'Drop-down options (comma separated)', show: v => v.type === 'select', wide: true }, { k: 'sort', label: 'Order', type: 'number' },
      { k: 'required', label: 'Required', type: 'check' }, { k: 'active', label: 'Active', type: 'check' }]}
    save={(v, n) => reload(l, n ? api('/setup/custom-fields', { body: v }) : api(`/setup/custom-fields/${v.id}`, { method: 'PUT', body: v }))} />;
}

