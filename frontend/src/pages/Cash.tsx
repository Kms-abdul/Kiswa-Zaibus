/* Cash custody: cashier shifts, handovers of counter cash to the Outdoor Clerk, and owner instructions the clerk executes
   (bank deposit, cash payment, transfer to owner) which the owner then verifies. */
import { useState } from 'react';
import { api } from '../api';
import { hasRole, useAuth } from '../auth';
import { co, day, inr, when } from '../format';
import { BranchSelect, Btn, CompanySelect, DataTable, Field, Kv, Modal, Page, Panel, Pill, Tabs, useAction, useLoad, useUrlState } from '../ui/kit';

export default function Cash() {
  const { can, me } = useAuth();
  const [tab, setTab] = useUrlState<'handovers' | 'instructions' | 'clerks' | 'shifts'>('tab', 'handovers');
  const hand = useLoad<any[]>(() => (tab === 'handovers' ? api('/cash/handovers') : Promise.resolve([])), [tab]);
  const instr = useLoad<any[]>(() => (tab === 'instructions' ? api('/cash/instructions') : Promise.resolve([])), [tab]);
  const clerks = useLoad<any[]>(() => api('/cash/clerks'), [tab]);
  const shifts = useLoad<any[]>(() => (tab === 'shifts' ? api('/pos/shifts') : Promise.resolve([])), [tab]);
  const [newHand, setNewHand] = useState(false);
  const [newInstr, setNewInstr] = useState(false);
  const [selH, setSelH] = useState<any>(null);
  const [selI, setSelI] = useState<any>(null);
  const owner = hasRole(me, 'Owner', 'Director', 'CFO', 'Super Admin', 'Chairman');
  return (
    <Page title="Cash" sub="Every rupee of counter cash is with the cashier, the Outdoor Clerk, or in the bank — never untracked."
      actions={<>{can('add', 'cash') && <Btn onClick={() => setNewHand(true)}>Hand over to clerk</Btn>}{owner && can('add', 'cash') && <Btn kind="pri" onClick={() => setNewInstr(true)}>New instruction</Btn>}</>}>
      <Tabs value={tab} onChange={setTab} tabs={[['handovers', 'Handovers'], ['instructions', 'Owner instructions'], ['clerks', 'Clerk balances'], ['shifts', 'Shifts']]} />
      {tab === 'handovers' && <Panel><DataTable rows={hand.data} onRow={setSelH} cols={[
        { key: 'no', label: 'Handover', primary: true }, { key: 'branch_id', label: 'From location' }, { key: 'from_name', label: 'Handed by', hideSmall: true },
        { key: 'clerk_name', label: 'Clerk' }, { key: 'amount', label: 'Amount', align: 'r', render: r => inr(r.amount) }, { key: 'status', label: 'Status', render: r => <Pill s={r.status} /> }]} /></Panel>}
      {tab === 'instructions' && <Panel><DataTable rows={instr.data} onRow={setSelI} cols={[
        { key: 'no', label: 'Instruction', primary: true }, { key: 'type', label: 'Type' }, { key: 'company', label: 'Co', render: r => co(r.company) },
        { key: 'clerk_name', label: 'Clerk' }, { key: 'purpose', label: 'Purpose', hideSmall: true }, { key: 'amount', label: 'Amount', align: 'r', render: r => inr(r.amount) },
        { key: 'status', label: 'Status', render: r => <Pill s={r.status} /> }]} /></Panel>}
      {tab === 'clerks' && <Panel><DataTable rows={clerks.data} cols={[{ key: 'name', label: 'Outdoor Clerk', primary: true }, { key: 'mobile', label: 'Mobile' },
        ...['KG', 'ZE'].map(c => ({ key: c, label: `Holding ${co(c)}`, align: 'r' as const, render: (r: any) => inr(r.holding?.[c] || 0) }))]} empty="No Outdoor Clerk set up." /></Panel>}
      {tab === 'shifts' && <Panel><DataTable rows={shifts.data} cols={[{ key: 'no', label: 'Shift', primary: true }, { key: 'user_name', label: 'Cashier' }, { key: 'location', label: 'Location' },
        { key: 'shift_date', label: 'Date', render: r => day(r.shift_date) }, { key: 'closed_at', label: 'Closed', hideSmall: true, render: r => when(r.closed_at) },
        { key: 'diff', label: 'Difference', align: 'r', render: r => inr(Object.values(r.figures || {}).reduce((s: number, f: any) => s + (f.difference || 0), 0)) },
        { key: 'status', label: 'Status', render: r => <Pill s={r.status} /> }]} /></Panel>}
      {newHand && <NewHandover clerks={clerks.data || []} onClose={() => { setNewHand(false); hand.reload(); }} />}
      {newInstr && <NewInstruction clerks={clerks.data || []} onClose={() => { setNewInstr(false); instr.reload(); }} />}
      {selH && <HandoverDetail h={selH} onClose={() => { setSelH(null); hand.reload(); clerks.reload(); }} />}
      {selI && <InstructionDetail i={selI} onClose={() => { setSelI(null); instr.reload(); clerks.reload(); }} />}
    </Page>
  );
}

const DEN = [500, 200, 100, 50, 20, 10];
function NewHandover({ clerks, onClose }: { clerks: any[]; onClose: () => void }) {
  const [branch, setBranch] = useState('');
  const [clerk, setClerk] = useState('');
  const [den, setDen] = useState<Record<number, string>>({});
  const total = DEN.reduce((s, d) => s + d * (Number(den[d]) || 0), 0);
  const [run, busy] = useAction();
  const save = async () => { if (await run(() => api('/cash/handovers', { body: { branch_id: branch, clerk_id: clerk, amount: total, denominations: Object.fromEntries(DEN.map(d => [d, Number(den[d]) || 0])) } }), (x: any) => `Handover ${x.no} created. The clerk accepts after counting.`)) onClose(); };
  return <Modal title="Hand over cash to the Outdoor Clerk" onClose={onClose} footer={<><Btn onClick={onClose}>Cancel</Btn><Btn kind="pri" busy={busy} disabled={!branch || !clerk || !total} onClick={save}>Hand over {inr(total, 0)}</Btn></>}>
    <div className="form"><BranchSelect value={branch} onChange={setBranch} />
      <Field label="Clerk"><select value={clerk} onChange={e => setClerk(e.target.value)}><option value="">Choose…</option>{clerks.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</select></Field>
      <div className="denoms">{DEN.map(d => <label key={d}>₹{d}<input inputMode="numeric" value={den[d] || ''} onChange={e => setDen(x => ({ ...x, [d]: e.target.value }))} /></label>)}</div>
    </div>
  </Modal>;
}

function HandoverDetail({ h, onClose }: { h: any; onClose: () => void }) {
  const { me } = useAuth();
  const [counted, setCounted] = useState(String(h.amount));
  const [reason, setReason] = useState('');
  const [run, busy] = useAction();
  const mine = me?.id === h.clerk_id && h.status === 'Awaiting clerk';
  return <Modal title={<>Handover {h.no} <Pill s={h.status} /></>} onClose={onClose} footer={mine && <>
    <Btn kind="danger" busy={busy} onClick={async () => { if (await run(() => api(`/cash/handovers/${h.id}/reject`, { body: { reason } }), () => 'Rejected.')) onClose(); }}>Reject</Btn>
    <Btn kind="pri" busy={busy} onClick={async () => { if (await run(() => api(`/cash/handovers/${h.id}/accept`, { body: { counted: Number(counted), reason } }), () => 'Accepted. The cash is now in your custody.')) onClose(); }}>Accept</Btn></>}>
    <Kv items={[['From', `${h.from_name} at ${h.branch_id}`], ['Clerk', h.clerk_name], ['Amount', inr(h.amount)], ['Notes', Object.entries(h.denominations || {}).filter(([, n]) => Number(n)).map(([d, n]) => `₹${d}×${n}`).join(', ')], ['Counted', h.counted != null ? inr(h.counted) : undefined]]} />
    {mine && <div className="form grid2"><Field label="I counted (₹)"><input inputMode="decimal" value={counted} onChange={e => setCounted(e.target.value)} /></Field>
      <Field label="Reason (if different / rejecting)"><input value={reason} onChange={e => setReason(e.target.value)} /></Field></div>}
  </Modal>;
}

function NewInstruction({ clerks, onClose }: { clerks: any[]; onClose: () => void }) {
  const { org } = useAuth();
  const [f, setF] = useState({ company: org?.companies[0]?.code || 'KG', type: 'Bank deposit', clerk_id: '', amount: '', purpose: '', recipient: '', expense_account: '' });
  const { data: accts } = useLoad<any[]>(() => api(`/accounts?company=${f.company}`), [f.company]);
  const [run, busy] = useAction();
  const save = async () => { if (await run(() => api('/cash/instructions', { body: { ...f, amount: Number(f.amount) } }), (x: any) => `Instruction ${x.no} created.`)) onClose(); };
  return <Modal title="Instruction to the Outdoor Clerk" onClose={onClose} footer={<><Btn onClick={onClose}>Cancel</Btn><Btn kind="pri" busy={busy} onClick={save}>Issue</Btn></>}>
    <div className="form">
      <CompanySelect value={f.company} onChange={v => setF({ ...f, company: v })} />
      <Field label="Type"><select value={f.type} onChange={e => setF({ ...f, type: e.target.value })}>{['Bank deposit', 'Cash payment', 'Transfer to owner'].map(t => <option key={t}>{t}</option>)}</select></Field>
      <Field label="Clerk"><select value={f.clerk_id} onChange={e => setF({ ...f, clerk_id: e.target.value })}><option value="">Choose…</option>{clerks.map(c => <option key={c.id} value={c.id}>{c.name} (holds {inr(c.holding?.[f.company] || 0)})</option>)}</select></Field>
      <Field label="Amount (₹)"><input inputMode="decimal" value={f.amount} onChange={e => setF({ ...f, amount: e.target.value })} /></Field>
      {f.type === 'Cash payment' && <><Field label="Expense account"><select value={f.expense_account} onChange={e => setF({ ...f, expense_account: e.target.value })}><option value="">Choose…</option>
        {(accts || []).filter(a => a.grp === 'X' && a.active).map(a => <option key={a.code} value={a.code}>{a.code} {a.name}</option>)}</select></Field>
        <Field label="Pay to"><input value={f.recipient} onChange={e => setF({ ...f, recipient: e.target.value })} /></Field></>}
      <Field label="Purpose" wide><input value={f.purpose} onChange={e => setF({ ...f, purpose: e.target.value })} /></Field>
    </div>
  </Modal>;
}

function InstructionDetail({ i, onClose }: { i: any; onClose: () => void }) {
  const { me, can } = useAuth();
  const [x, setX] = useState({ slip_no: '', paid_to: '', notes: '', remarks: '' });
  const [run, busy] = useAction();
  const mine = me?.id === i.clerk_id && i.status === 'Owner Approved';
  const verify = can('approve', 'cash') && i.status === 'Executed' && me?.id !== i.clerk_id && me?.id !== i.created_by;   // Finance verifies
  return <Modal title={<>{i.type} {i.no} <Pill s={i.status} /></>} onClose={onClose} footer={<>
    {mine && <Btn kind="pri" busy={busy} onClick={async () => { if (await run(() => api(`/cash/instructions/${i.id}/execute`, { body: x }), () => 'Marked as done. Attach the slip in Evidence.')) onClose(); }}>Mark done</Btn>}
    {verify && <><Btn kind="danger" busy={busy} onClick={async () => { if (await run(() => api(`/cash/instructions/${i.id}/verify`, { body: { ok: false, remarks: x.remarks } }), () => 'Flagged.')) onClose(); }}>Disputed</Btn>
      <Btn kind="pri" busy={busy} onClick={async () => { if (await run(() => api(`/cash/instructions/${i.id}/verify`, { body: { ok: true, remarks: x.remarks } }), () => 'Verified.')) onClose(); }}>Verified</Btn></>}
  </>}>
    <Kv items={[['Company', co(i.company)], ['Clerk', i.clerk_name], ['Amount', inr(i.amount)], ['Purpose', i.purpose], ['Pay to', i.recipient], ['Journal', i.journal_no],
      ['Execution', i.execution ? Object.entries(i.execution).map(([k, v]) => `${k}: ${v}`).join(', ') : undefined], ['Verified', i.verify_remarks]]} />
    {mine && <div className="form grid2">
      {i.type === 'Bank deposit' && <Field label="Deposit slip no."><input value={x.slip_no} onChange={e => setX({ ...x, slip_no: e.target.value })} /></Field>}
      {i.type !== 'Bank deposit' && <Field label="Paid to"><input value={x.paid_to} onChange={e => setX({ ...x, paid_to: e.target.value })} /></Field>}
      <Field label="Notes"><input value={x.notes} onChange={e => setX({ ...x, notes: e.target.value })} /></Field></div>}
    {verify && <Field label="Remarks" wide><input value={x.remarks} onChange={e => setX({ ...x, remarks: e.target.value })} /></Field>}
  </Modal>;
}
