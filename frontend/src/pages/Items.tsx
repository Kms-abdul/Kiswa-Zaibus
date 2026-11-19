/* Item master: search and page through items, add or edit an item, and request price changes (approved before they apply). */
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../api';
import { useAuth } from '../auth';
import { co, inr } from '../format';
import { useCustomFields } from '../ui/forms';
import { Btn, CompanySelect, DataTable, Field, Modal, Page, Panel, Pill, useAction, useLoad } from '../ui/kit';

const GST = [0, 5, 12, 18, 28];

export default function Items() {
  const { can, org } = useAuth();
  const nav = useNavigate();
  const [company, setCompany] = useState('');
  const [q, setQ] = useState('');
  const [page, setPage] = useState(1);
  const [edit, setEdit] = useState<any>(null);
  const [price, setPrice] = useState<any[] | null>(null);
  const { data, reload } = useLoad<any>(() => api(`/items?company=${company}&q=${encodeURIComponent(q)}&page=${page}&size=50`), [company, q, page]);
  const pages = data ? Math.max(1, Math.ceil(data.total / data.size)) : 1;
  return (
    <Page title="Items" sub={data ? `${data.total} items` : ''} actions={can('master', 'inv') && <>
      <Btn onClick={() => nav('/labels')}>Print labels</Btn><Btn onClick={() => setPrice([])}>Price change</Btn><Btn kind="pri" onClick={() => setEdit({ company: company || org?.companies[0]?.code })}>New item</Btn></>}>
      <div className="filters">
        <select value={company} onChange={e => { setCompany(e.target.value); setPage(1); }} aria-label="Company"><option value="">All companies</option>
          {(org?.companies || []).map(c => <option key={c.code} value={c.code}>{c.name}</option>)}</select>
        <input placeholder="Name, code, barcode, ISBN, category or class" value={q} onChange={e => { setQ(e.target.value); setPage(1); }} aria-label="Search items" />
      </div>
      <Panel>
        <DataTable rows={data?.rows} onRow={r => can('master', 'inv') && setEdit(r)} cols={[
          { key: 'id', label: 'Code', render: r => <span className="mono">{r.id}</span> },
          { key: 'name', label: 'Item', primary: true, render: r => <>{r.name} <span className="muted">{[r.size, r.class_name].filter(Boolean).join(' · ')}</span></> },
          { key: 'category', label: 'Category' }, { key: 'company', label: 'Co', hideSmall: true, render: r => co(r.company) },
          { key: 'gst_rate', label: 'GST', align: 'r', hideSmall: true, render: r => r.gst_rate + '%' },
          { key: 'price', label: 'Price', align: 'r', render: r => (r.unpriced ? <Pill s="No price" tone="warn" /> : inr(r.price)) },
          { key: 'sel', label: '', render: r => can('master', 'inv') ? <Btn kind="link" onClick={e => { e.stopPropagation(); setPrice(p => [...(p || []), r]); }}>+ price</Btn> : null },
        ]} />
        <div className="pager"><Btn disabled={page <= 1} onClick={() => setPage(p => p - 1)}>‹ Prev</Btn><span>Page {page} of {pages}</span><Btn disabled={page >= pages} onClick={() => setPage(p => p + 1)}>Next ›</Btn></div>
      </Panel>
      {edit && <ItemForm item={edit} onClose={() => { setEdit(null); reload(); }} />}
      {price && <PriceChange rows={price} setRows={setPrice} onClose={() => { setPrice(null); reload(); }} />}
    </Page>
  );
}

function ItemForm({ item, onClose }: { item: any; onClose: () => void }) {
  const isNew = !item.id;
  const [f, setF] = useState<any>({ gst_rate: 5, reorder_level: 0, ...item });
  const { data: cats } = useLoad<any[]>(() => api(`/categories?company=${f.company}`), [f.company]);
  const { data: sups } = useLoad<any[]>(() => api(`/suppliers?company=${f.company}`), [f.company]);
  const { data: schools } = useLoad<any[]>(() => api('/schools'), []);
  const [run, busy] = useAction();
  const cf = useCustomFields('item', item.id);
  const set = (k: string) => (e: any) => setF((x: any) => ({ ...x, [k]: e.target.type === 'checkbox' ? e.target.checked : e.target.value }));
  const cat = cats?.find(c => c.name === f.category);
  const save = async () => {
    const keys = ['name', 'category', 'size', 'colour', 'gender', 'class_name', 'term', 'academic_year', 'school_id', 'hsn', 'gst_rate', 'barcode', 'supplier_id', 'reorder_level', 'bin_location', 'uom', 'isbn', 'isbn_na_reason'];
    const body: any = Object.fromEntries(keys.filter(k => f[k] !== undefined).map(k => [k, f[k]]));
    if (isNew) { body.company = f.company; body.price = Number(f.price) || 0; } else body.active = f.active ? 1 : 0;
    const r = await run(async () => { const x: any = await api(isNew ? '/items' : `/items/${item.id}`, { method: isNew ? 'POST' : 'PUT', body }); await cf.save(x.id); return x; },
      (x: any) => `Item ${x.id} saved.`);
    if (r) onClose();
  };
  return (
    <Modal wide title={isNew ? 'New item' : `Edit ${item.id}`} onClose={onClose} footer={<><Btn onClick={onClose}>Cancel</Btn><Btn kind="pri" busy={busy} onClick={save}>Save</Btn></>}>
      <div className="form grid2">
        {isNew && <CompanySelect value={f.company} onChange={v => setF((x: any) => ({ ...x, company: v }))} />}
        <Field label="Name" wide><input value={f.name || ''} onChange={set('name')} /></Field>
        <Field label="Category"><select value={f.category || ''} onChange={e => { const c = cats?.find(x => x.name === e.target.value); setF((x: any) => ({ ...x, category: e.target.value, hsn: x.hsn || c?.hsn, gst_rate: c?.gst_rate ?? x.gst_rate })); }}>
          <option value="">Choose…</option>{(cats || []).map(c => <option key={c.name}>{c.name}</option>)}</select></Field>
        <Field label="Size"><input value={f.size || ''} onChange={set('size')} /></Field>
        <Field label="Gender"><select value={f.gender || ''} onChange={set('gender')}><option value="">—</option><option>Boys</option><option>Girls</option><option>Unisex</option></select></Field>
        <Field label="Class"><input value={f.class_name || ''} onChange={set('class_name')} /></Field>
        <Field label="School"><select value={f.school_id || ''} onChange={set('school_id')}><option value="">—</option>{(schools || []).map(s => <option key={s.id} value={s.id}>{s.name}</option>)}</select></Field>
        <Field label="HSN"><input value={f.hsn || ''} onChange={set('hsn')} /></Field>
        <Field label="GST %"><select value={f.gst_rate} onChange={set('gst_rate')}>{GST.map(g => <option key={g} value={g}>{g}</option>)}</select></Field>
        <Field label="Barcode"><input value={f.barcode || ''} onChange={set('barcode')} /></Field>
        {(cat?.isbn_required || f.isbn13) && <>
          <Field label="ISBN"><input value={f.isbn ?? f.isbn13 ?? ''} onChange={set('isbn')} /></Field>
          <Field label="No ISBN — reason"><input value={f.isbn_na_reason || ''} onChange={set('isbn_na_reason')} /></Field></>}
        <Field label="Supplier"><select value={f.supplier_id || ''} onChange={set('supplier_id')}><option value="">—</option>{(sups || []).map(s => <option key={s.id} value={s.id}>{s.name}</option>)}</select></Field>
        <Field label="Reorder level"><input inputMode="numeric" value={f.reorder_level ?? ''} onChange={set('reorder_level')} /></Field>
        <Field label="Bin location"><input value={f.bin_location || ''} onChange={set('bin_location')} /></Field>
        <Field label="Unit of measure"><select value={f.uom || 'Nos'} onChange={set('uom')}>{['Nos', 'Pair', 'Set', 'Pack', 'Book', 'Dozen', 'Metre'].map(x => <option key={x}>{x}</option>)}</select></Field>
        {isNew ? <Field label="Selling price (₹, incl. GST)" hint="0 = not for sale yet"><input inputMode="decimal" value={f.price ?? ''} onChange={set('price')} /></Field>
          : <label className="check"><input type="checkbox" checked={!!f.active} onChange={set('active')} />Active</label>}
      </div>
      {cf.block}
      {!isNew && <p className="muted">Price changes go through “Price change” (approval). Cost changes only through purchases.</p>}
    </Modal>
  );
}

function PriceChange({ rows, setRows, onClose }: { rows: any[]; setRows: (r: any[]) => void; onClose: () => void }) {
  const [p, setP] = useState<Record<string, string>>({});
  const [reason, setReason] = useState('');
  const [run, busy] = useAction();
  const save = async () => {
    const r = await run(() => api('/items/price-change', { body: { reason, rows: rows.map(x => ({ id: x.id, price: Number(p[x.id] ?? x.price) })) } }), () => 'Price change applied.');
    if (r) onClose();
  };
  return <Modal title="Price change" onClose={onClose} footer={<><Btn onClick={onClose}>Cancel</Btn><Btn kind="pri" busy={busy} disabled={!rows.length} onClick={save}>Submit</Btn></>}>
    {!rows.length ? <p className="muted">Close this, then tap “+ price” on the items to change.</p> : rows.map(r => (
      <Field key={r.id} label={`${r.id} ${r.name} ${r.size || ''} · now ${inr(r.price)}`}>
        <div className="row"><input inputMode="decimal" value={p[r.id] ?? r.price} onChange={e => setP(x => ({ ...x, [r.id]: e.target.value }))} />
          <button className="x" onClick={() => setRows(rows.filter(x => x.id !== r.id))} aria-label="Remove">×</button></div></Field>))}
    <Field label="Reason" wide><input value={reason} onChange={e => setReason(e.target.value)} placeholder="e.g. new academic-year price list" /></Field>
  </Modal>;
}
