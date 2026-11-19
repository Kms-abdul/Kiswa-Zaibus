/* Barcode labels for items: choose items and how many labels each, pick the label size, print.
   Each label carries the item name, size, price (incl. GST) and a Code 128 barcode of the item's barcode (or its item code),
   which the counter's scanner reads straight into the bill. */
import JsBarcode from 'jsbarcode';
import { useEffect, useRef, useState } from 'react';
import { api } from '../api';
import { useAuth } from '../auth';
import { inr } from '../format';
import { Btn, Field, ItemSearch, Page, Panel, useToast } from '../ui/kit';

type Row = { id: string; name: string; size?: string; price: number; code: string; copies: number; company: string };
const SIZES: [string, string, string][] = [['50x25', '50 × 25 mm roll', 'lbl-50x25'], ['38x25', '38 × 25 mm sheet (A4, 65 per page)', 'lbl-38x25'], ['70x35', '70 × 35 mm sheet (A4, 24 per page)', 'lbl-70x35']];

function Barcode({ value }: { value: string }) {
  const ref = useRef<SVGSVGElement>(null);
  useEffect(() => {
    if (!ref.current) return;
    try { JsBarcode(ref.current, value, { format: 'CODE128', displayValue: true, fontSize: 11, height: 34, margin: 0, width: 1.4, textMargin: 1 }); }
    catch { ref.current.innerHTML = ''; }
  }, [value]);
  return <svg ref={ref} role="img" aria-label={`Barcode ${value}`} />;
}

export default function Labels() {
  const { org } = useAuth();
  const toast = useToast();
  const [rows, setRows] = useState<Row[]>([]);
  const [size, setSize] = useState('50x25');
  const [showPrice, setShowPrice] = useState(true);
  const add = (it: any) => setRows(r => (r.some(x => x.id === it.id) ? r : [...r, { id: it.id, name: it.name, size: it.size, price: it.price, code: it.barcode || it.id, copies: 1, company: it.company }]));
  const fromGrn = async (no: string) => {
    // labels for everything received on a goods receipt: one label per unit accepted
    const po = await api<any[]>('/purchasing/orders').catch(() => []);
    const hit = po.find(p => p.no === no.trim());
    if (!hit) { toast('Purchase order not found.', 'bad'); return; }
    const full = await api<any>(`/purchasing/orders/${hit.id}`);
    for (const ln of full.lines) {
      const it = await api<any>(`/items/${ln.item_id}`);
      const n = ln.received_qty - ln.rejected_qty;
      if (n > 0) setRows(r => [...r.filter(x => x.id !== it.id), { id: it.id, name: it.name, size: it.size, price: it.price, code: it.barcode || it.id, copies: n, company: it.company }]);
    }
  };
  const [po, setPo] = useState('');
  const cls = SIZES.find(s => s[0] === size)![2];
  const all = rows.flatMap(r => Array.from({ length: Math.max(0, r.copies) }, () => r));
  const coName = (c: string) => org?.companies.find(x => x.code === c)?.short_name || c;
  return (
    <Page title="Barcode labels" sub="Print price and barcode labels for items. The counter scanner reads them into the bill." actions={<Btn kind="pri" disabled={!all.length} onClick={() => window.print()}>Print {all.length} label{all.length === 1 ? '' : 's'}</Btn>}>
      <Panel title="Items">
        <ItemSearch onPick={add} placeholder="Find an item to add labels for" />
        <div className="row"><input placeholder="or PO number, e.g. KG/PO/2627/0004 — one label per unit received" value={po} onChange={e => setPo(e.target.value)} aria-label="Purchase order number" />
          <Btn onClick={() => fromGrn(po)} disabled={!po}>Add received items</Btn></div>
        <div className="table-wrap"><table className="rtable"><thead><tr><th>Item</th><th>Barcode</th><th className="r">Price</th><th>Labels</th><th /></tr></thead><tbody>
          {rows.map((r, i) => <tr key={r.id}><td data-label="Item" className="primary">{r.name} <span className="muted">{r.size}</span></td><td data-label="Barcode" className="mono">{r.code}</td>
            <td data-label="Price" className="r">{inr(r.price)}</td>
            <td data-label="Labels"><input className="w-s" inputMode="numeric" value={r.copies} onChange={e => setRows(x => x.map((y, j) => (j === i ? { ...y, copies: Math.min(500, Number(e.target.value) || 0) } : y)))} aria-label="Number of labels" /></td>
            <td data-label=""><button className="x" onClick={() => setRows(x => x.filter((_, j) => j !== i))} aria-label="Remove">×</button></td></tr>)}
        </tbody></table></div>
        {!rows.length && <p className="empty">Search above to add items.</p>}
        <div className="form grid2">
          <Field label="Label size"><select value={size} onChange={e => setSize(e.target.value)}>{SIZES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></Field>
          <label className="check"><input type="checkbox" checked={showPrice} onChange={e => setShowPrice(e.target.checked)} />Print the price</label>
        </div>
      </Panel>
      {all.length > 0 && <Panel title="Preview">
        <div className={'labels print-area ' + cls}>{all.map((r, i) => (
          <div className="label" key={i}><b>{r.name}{r.size && r.size !== 'Std' ? ` · ${r.size}` : ''}</b>
            <div className="lbl-row"><span>{coName(r.company)}</span>{showPrice && <span className="lbl-price">{inr(r.price, 0)}</span>}</div>
            <Barcode value={r.code} /></div>))}
        </div>
      </Panel>}
    </Page>
  );
}
