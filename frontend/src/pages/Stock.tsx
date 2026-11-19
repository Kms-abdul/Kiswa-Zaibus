/* Stock on hand by location, low-stock filter, and the movement ledger of one item at one location. */
import { useState } from 'react';
import { api, download } from '../api';
import { useAuth } from '../auth';
import { day, inr, num } from '../format';
import { BranchSelect, Btn, DataTable, Empty, Modal, Page, Panel, useLoad, useUrlState } from '../ui/kit';

export default function Stock() {
  const { can } = useAuth();
  const [branch, setBranch] = useUrlState<string>('branch', '');
  const [q, setQ] = useUrlState<string>('q', '');
  const [lowS, setLowS] = useUrlState<string>('low', '');
  const low = lowS === '1';
  const setLow = (v: boolean) => setLowS(v ? '1' : '');
  const [sel, setSel] = useState<any>(null);
  const { data } = useLoad<any[]>(() => api(`/inventory/stock?branch=${branch}&q=${encodeURIComponent(q)}${low ? '&low=1' : ''}`), [branch, q, low]);
  const showCost = can('cost', 'inv');
  const total = (data || []).reduce((s, r) => s + (r.value || 0), 0);
  return (
    <Page title="Stock" sub={showCost && data ? <>Value at cost {inr(total)}</> : 'On hand, reserved for online orders, and free to sell.'}
      actions={can('view', 'rep') && <Btn onClick={() => download(`/reports/stock-valuation?detail=1&format=csv`, 'stock.csv')}>Export CSV</Btn>}>
      <div className="filters">
        <BranchSelect value={branch} onChange={setBranch} placeholder="All my locations" />
        <input placeholder="Item, code, barcode or ISBN" value={q} onChange={e => setQ(e.target.value)} aria-label="Search stock" />
        <label className="check"><input type="checkbox" checked={low} onChange={e => setLow(e.target.checked)} />At or below reorder level</label>
      </div>
      <Panel><DataTable rows={data} onRow={setSel} keyOf={r => r.branch_id + r.item_id} cols={[
        { key: 'name', label: 'Item', primary: true, render: r => <>{r.name} <span className="muted">{[r.size, r.class_name].filter(Boolean).join(' · ')}</span></> },
        { key: 'branch_id', label: 'Location' }, { key: 'category', label: 'Category', hideSmall: true },
        { key: 'qty', label: 'On hand', align: 'r', render: r => <b className={r.reorder_level && r.qty <= r.reorder_level ? 'warn-text' : ''}>{num(r.qty)}</b> },
        { key: 'reserved', label: 'Reserved', align: 'r', render: r => (r.reserved ? num(r.reserved) : '') },
        { key: 'free', label: 'Free', align: 'r', render: r => num(r.free) },
        { key: 'price', label: 'Price', align: 'r', hideSmall: true, render: r => inr(r.price) },
        ...(showCost ? [{ key: 'value', label: 'Value', align: 'r' as const, hideSmall: true, render: (r: any) => inr(r.value) }] : []),
      ]} empty={<Empty title={low ? 'Nothing is at or below its reorder level.' : 'No stock matches.'} hint={q ? 'Try fewer letters, the item code or the barcode.' : undefined}
        action={(q || low || branch) && <Btn onClick={() => { setQ(''); setLow(false); setBranch(''); }}>Clear filters</Btn>} />} /></Panel>
      {data && data.length > 0 && <p className="muted small">Click an item to see its movements at that location.</p>}
      {sel && <LedgerModal branch={sel.branch_id} item={sel.item_id} name={sel.name} onClose={() => setSel(null)} />}
    </Page>
  );
}

function LedgerModal({ branch, item, name, onClose }: { branch: string; item: string; name: string; onClose: () => void }) {
  const { data } = useLoad<any[]>(() => api(`/inventory/ledger?branch=${branch}&item=${item}`), [branch, item]);
  return <Modal wide title={`${name} · ${branch}`} onClose={onClose}>
    <DataTable rows={data ? [...data].reverse() : null} keyOf={r => r.created_at + r.ref} cols={[{ key: 'txn_date', label: 'Date', render: r => day(r.txn_date) }, { key: 'txn_type', label: 'Type' },
      { key: 'ref', label: 'Ref', primary: true }, { key: 'qty', label: 'Qty', align: 'r', render: r => (r.qty > 0 ? '+' : '') + r.qty }, { key: 'balance', label: 'Balance', align: 'r' }]} empty="No movements." />
  </Modal>;
}
