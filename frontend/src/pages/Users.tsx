/* User access: create users, change role / scope / status, unlock, end sessions. Changes go through maker-checker
   approval when it is switched on; granting Super Admin needs a Super Admin. */
import { useState } from 'react';
import { api } from '../api';
import { useAuth } from '../auth';
import { day, when } from '../format';
import { Btn, DataTable, Field, Kv, Modal, Page, Panel, Pill, useAction, useLoad, useToast } from '../ui/kit';

const applied = (x: any) => (x?.status === 'Applied' ? `${x.result}${x.dev_temp_password ? ` · test temporary password ${x.dev_temp_password}` : ''}` : x?.no ? `Sent for approval (${x.no}).` : 'Saved.');

function AccessChanges({ uid }: { uid: string }) {
  const { org, can } = useAuth();
  const toast = useToast();
  const l = useLoad<any[]>(() => api(`/users/${uid}/schedule`), [uid]);
  const [f, setF] = useState<any>(null);
  const [run, busy] = useAction();
  const save = async () => {
    if (!f.reason?.trim()) { toast('Give the reason.', 'bad'); return; }
    if (await run(() => api(`/users/${uid}/schedule`, { body: f }), (x: any) => (x.status === 'Scheduled' ? 'Change scheduled.' : ''))) { setF(null); l.reload(); }
  };
  return <>
    <h4>Scheduled access changes</h4>
    <DataTable rows={l.data} empty="Nothing scheduled." cols={[{ key: 'change_type', label: 'Change', primary: true }, { key: 'effective_on', label: 'From', render: (r: any) => day(r.effective_on) },
      { key: 'reason', label: 'Reason' }, { key: 'status', label: 'Status', render: (r: any) => <Pill s={r.status} /> },
      { key: 'x', label: '', render: (r: any) => (['Scheduled', 'Pending approval'].includes(r.status) && can('users', 'ctl.users')
        ? <Btn kind="link" busy={busy} onClick={async () => { const reason = f?.reason || 'Withdrawn'; if (await run(() => api(`/users/schedule/${r.id}/cancel`, { body: { reason } }), () => 'Cancelled.')) l.reload(); }}>Cancel</Btn> : null) }]} />
    {can('users', 'ctl.users') && (!f ? <Btn kind="link" onClick={() => setF({ change_type: 'Resign', effective_on: '', reason: '', branches: [] })}>+ Schedule a resignation, transfer or suspension</Btn> : (
      <div className="form grid2">
        <Field label="Change"><select value={f.change_type} onChange={e => setF({ ...f, change_type: e.target.value })}><option value="Resign">Resignation (access ends)</option><option value="Transfer">Transfer to other location(s)</option>
          <option value="Suspend">Suspension</option><option value="Reinstate">Reinstatement</option></select></Field>
        <Field label="Effective from"><input type="date" value={f.effective_on} onChange={e => setF({ ...f, effective_on: e.target.value })} /></Field>
        {f.change_type === 'Transfer' && <div className="chips field wide">{(org?.branches || []).filter(b => b.active).map(b => <label key={b.id} className="chip"><input type="checkbox" checked={f.branches.includes(b.id)}
          onChange={() => setF({ ...f, branches: f.branches.includes(b.id) ? f.branches.filter((x: string) => x !== b.id) : [...f.branches, b.id] })} />{b.company} · {b.name}</label>)}</div>}
        <Field label="Reason (letter / order reference)" wide><input value={f.reason} onChange={e => setF({ ...f, reason: e.target.value })} /></Field>
        <div className="row"><Btn onClick={() => setF(null)}>Back</Btn><Btn kind="pri" busy={busy} onClick={save}>Schedule</Btn></div>
      </div>))}
  </>;
}

export default function Users() {
  const { can } = useAuth();
  const [q, setQ] = useState('');
  const [status, setStatus] = useState('');
  const [open, setOpen] = useState<any>(null);
  const list = useLoad<any[]>(() => api(`/users?q=${encodeURIComponent(q)}&status=${status}`), [q, status]);
  return (
    <Page title="Users & access" sub="Every change needs a reason and is recorded in the audit trail." actions={can('users', 'ctl.users') && <Btn kind="pri" onClick={() => setOpen({})}>New user</Btn>}>
      <div className="filters"><input placeholder="Name, employee ID, mobile or role" value={q} onChange={e => setQ(e.target.value)} aria-label="Search users" />
        <select value={status} onChange={e => setStatus(e.target.value)} aria-label="Status"><option value="">All statuses</option>{['Active', 'Inactive', 'Locked', 'Suspended', 'Resigned'].map(s => <option key={s}>{s}</option>)}</select></div>
      <Panel><DataTable rows={list.data} onRow={setOpen} cols={[
        { key: 'name', label: 'Name', primary: true }, { key: 'emp_id', label: 'Employee ID' }, { key: 'main_role', label: 'Role' },
        { key: 'companies', label: 'Companies', hideSmall: true, render: r => r.companies.join(', ') },
        { key: 'branches', label: 'Locations', hideSmall: true, render: r => (r.all_branches ? 'All' : `${r.branches.length}`) },
        { key: 'last_login_at', label: 'Last sign-in', hideSmall: true, render: r => when(r.last_login_at) }, { key: 'status', label: 'Status', render: r => <Pill s={r.status} /> },
      ]} /></Panel>
      {open && <UserForm u={open} onClose={() => { setOpen(null); list.reload(); }} />}
    </Page>
  );
}

/** A random password that meets the default policy (12+ characters, upper, lower, digit, special). */
function genPassword() {
  const sets = ['ABCDEFGHJKLMNPQRSTUVWXYZ', 'abcdefghijkmnpqrstuvwxyz', '23456789', '#@$%&*!?'];
  const all = sets.join('');
  const rnd = (n: number) => { const a = new Uint32Array(1); crypto.getRandomValues(a); return a[0] % n; };
  const chars = sets.map(s => s[rnd(s.length)]);
  while (chars.length < 12) chars.push(all[rnd(all.length)]);
  for (let i = chars.length - 1; i > 0; i--) { const j = rnd(i + 1); [chars[i], chars[j]] = [chars[j], chars[i]]; }
  return chars.join('');
}

/** Password + confirm with Generate and Show; reports the value when both match (or '' when empty). */
function PasswordPair({ label, value, onChange }: { label: string; value: { pw: string; again: string }; onChange: (v: { pw: string; again: string }) => void }) {
  const [show, setShow] = useState(false);
  return <>
    <Field label={label} hint="Upper and lower case, a digit and a special character; not the person's name, employee ID or mobile.">
      <input type={show ? 'text' : 'password'} autoComplete="new-password" value={value.pw} onChange={e => onChange({ ...value, pw: e.target.value })} />
    </Field>
    <Field label="Type it again" hint={value.again && value.again !== value.pw ? 'Does not match.' : undefined}>
      <input type={show ? 'text' : 'password'} autoComplete="new-password" value={value.again} onChange={e => onChange({ ...value, again: e.target.value })} />
    </Field>
    <div className="row">
      <Btn type="button" onClick={() => { const p = genPassword(); onChange({ pw: p, again: p }); setShow(true); }}>Generate</Btn>
      <label className="check"><input type="checkbox" checked={show} onChange={e => setShow(e.target.checked)} />Show</label>
    </div>
  </>;
}

function ResetPassword({ u, onDone }: { u: any; onDone: () => void }) {
  const toast = useToast();
  const [pw, setPw] = useState({ pw: '', again: '' });
  const [must, setMust] = useState(true);
  const [reason, setReason] = useState('');
  const [run, busy] = useAction();
  const go = async () => {
    if (!pw.pw) { toast('Enter or generate the new password.', 'bad'); return; }
    if (pw.pw !== pw.again) { toast('The two passwords do not match.', 'bad'); return; }
    if (!reason.trim()) { toast('Give the reason for the reset.', 'bad'); return; }
    if (await run(() => api(`/users/${u.id}/password`, { body: { password: pw.pw, must_change: must, reason } }), (x: any) => x.result)) onDone();
  };
  return <Modal title={`Reset password · ${u.name} (${u.emp_id})`} onClose={onDone} footer={<><Btn onClick={onDone}>Cancel</Btn><Btn kind="pri" busy={busy} onClick={go}>Reset password</Btn></>}>
    <div className="form">
      <PasswordPair label="New password" value={pw} onChange={setPw} />
      <label className="check"><input type="checkbox" checked={must} onChange={e => setMust(e.target.checked)} />User must change it at the next sign-in (recommended)</label>
      <Field label="Reason"><input value={reason} onChange={e => setReason(e.target.value)} placeholder="e.g. forgot password, asked by phone" /></Field>
      <p className="muted">Give the password to the user in person or by phone. Their open sessions are ended and a locked account is unlocked.
        The reset is recorded in the audit trail; the password itself is never stored or shown again.</p>
    </div>
  </Modal>;
}

function UserForm({ u, onClose }: { u: any; onClose: () => void }) {
  const { org, can, me } = useAuth();
  const superAdmin = !!me?.roles.includes('Super Admin');
  const [firstPw, setFirstPw] = useState({ pw: '', again: '' });
  const [resetting, setResetting] = useState(false);
  const toast = useToast();
  const isNew = !u.id;
  const { data: full } = useLoad<any>(() => (isNew ? Promise.resolve(null) : api(`/users/${u.id}`)), [u.id]);
  const { data: roles } = useLoad<any[]>(() => api('/roles').catch(() => []), []);
  const [f, setF] = useState<any>({ status: 'Active', companies: [], branches: [], roles: [], ...u });
  const [reason, setReason] = useState('');
  const [run, busy] = useAction();
  const set = (k: string) => (e: any) => setF((x: any) => ({ ...x, [k]: e.target.type === 'checkbox' ? e.target.checked : e.target.value }));
  const toggle = (k: string, v: string) => setF((x: any) => ({ ...x, [k]: x[k].includes(v) ? x[k].filter((y: string) => y !== v) : [...x[k], v] }));
  const save = async () => {
    if (!reason.trim()) { toast('Give the reason for this change.', 'bad'); return; }
    if (isNew && firstPw.pw && firstPw.pw !== firstPw.again) { toast('The two passwords do not match.', 'bad'); return; }
    const keys =['emp_id', 'name', 'mobile', 'email', 'department', 'designation', 'main_role', 'manager_id', 'status', 'join_date', 'valid_from', 'valid_to', 'relieve_date'];
    const body = { ...Object.fromEntries(keys.filter(k => f[k] !== undefined).map(k => [k, f[k]])), roles: f.roles.filter((r: string) => r !== f.main_role),
      companies: f.companies, branches: f.all_branches ? [] : f.branches, all_branches: !!f.all_branches, reason,
      ...(isNew && firstPw.pw ? { password: firstPw.pw } : {}) };
    const r = await run(() => api(isNew ? '/users' : `/users/${u.id}`, { method: isNew ? 'POST' : 'PUT', body }), applied);
    if (r) onClose();
  };
  const act = async (path: string) => { if (!reason.trim()) { toast('Give the reason first.', 'bad'); return; } if (await run(() => api(`/users/${u.id}/${path}`, { body: { reason } }), (x: any) => (x.ended != null ? `${x.ended} session(s) ended.` : applied(x)))) onClose(); };
  const branches = (org?.branches || []).filter(b => b.active && (!f.companies.length || f.companies.includes(b.company)));
  return (
    <Modal wide title={isNew ? 'New user' : `${u.name} (${u.emp_id})`} onClose={onClose} footer={can('users', 'ctl.users') && <>
      {!isNew && u.status === 'Locked' && <Btn busy={busy} onClick={() => act('unlock')}>Unlock</Btn>}
      {!isNew && <Btn busy={busy} onClick={() => act('sessions/end')}>End sessions</Btn>}
      {!isNew && superAdmin && u.id !== me?.id && <Btn busy={busy} onClick={() => setResetting(true)}>Reset password</Btn>}
      <Btn kind="pri" busy={busy} onClick={save}>{isNew ? 'Create user' : 'Save changes'}</Btn></>}>
      <div className="form grid2">
        <Field label="Employee ID"><input value={f.emp_id || ''} onChange={set('emp_id')} /></Field>
        <Field label="Full name"><input value={f.name || ''} onChange={set('name')} /></Field>
        <Field label="Mobile (OTP goes here)"><input inputMode="tel" value={f.mobile || ''} onChange={set('mobile')} /></Field>
        <Field label="Email"><input type="email" value={f.email || ''} onChange={set('email')} /></Field>
        <Field label="Department"><input value={f.department || ''} onChange={set('department')} /></Field>
        <Field label="Designation"><input value={f.designation || ''} onChange={set('designation')} /></Field>
        <Field label="Main role"><select value={f.main_role || ''} onChange={set('main_role')}><option value="">Choose…</option>{(roles || []).filter(r => r.active).map(r => <option key={r.name}>{r.name}</option>)}</select></Field>
        <Field label="Status"><select value={f.status} onChange={set('status')}>{['Active', 'Inactive', 'Locked', 'Suspended', 'Resigned'].map(s => <option key={s}>{s}</option>)}</select></Field>
        <Field label="Access from"><input type="date" value={f.valid_from || ''} onChange={set('valid_from')} /></Field>
        <Field label="Access until"><input type="date" value={f.valid_to || ''} onChange={set('valid_to')} /></Field>
      </div>
      {isNew && <>
        <h4>First password</h4>
        <p className="muted">Optional. Set it here and give it to the user yourself, or leave it empty and a temporary password is sent by SMS.
          Either way the user must choose their own password at the first sign-in.</p>
        <div className="form grid2"><PasswordPair label="First password" value={firstPw} onChange={setFirstPw} /></div>
      </>}
      <h4>Extra roles</h4>
      <div className="chips">{(roles || []).filter(r => r.active && r.name !== f.main_role).map(r => <label key={r.name} className="chip"><input type="checkbox" checked={f.roles.includes(r.name)} onChange={() => toggle('roles', r.name)} />{r.name}</label>)}</div>
      <h4>Companies</h4>
      <div className="chips">{(org?.companies || []).map(c => <label key={c.code} className="chip"><input type="checkbox" checked={f.companies.includes(c.code)} onChange={() => toggle('companies', c.code)} />{c.name}</label>)}</div>
      <h4>Locations</h4>
      <label className="check"><input type="checkbox" checked={!!f.all_branches} onChange={set('all_branches')} />All locations of the chosen companies</label>
      {!f.all_branches && <div className="chips">{branches.map(b => <label key={b.id} className="chip"><input type="checkbox" checked={f.branches.includes(b.id)} onChange={() => toggle('branches', b.id)} />{b.company} · {b.name}</label>)}</div>}
      <Field label="Reason for this change" wide><input value={reason} onChange={e => setReason(e.target.value)} placeholder="e.g. joined as cashier at Mehdipatnam, HR letter 2026/114" /></Field>
      {!isNew && <AccessChanges uid={u.id} />}
      {full && <><Kv items={[['Last sign-in', `${when(full.last_login_at)} · ${full.last_login_device || ''}`], ['Joined', day(full.join_date)], ['Failed attempts', String(full.failed_count)], ['Lock reason', full.lock_reason]]} />
        <h4>Recent sessions</h4><DataTable rows={full.sessions} cols={[{ key: 'device', label: 'Device', primary: true }, { key: 'ip', label: 'IP' }, { key: 'created_at', label: 'Started', render: r => when(r.created_at) },
          { key: 'ended_at', label: 'Ended', render: r => (r.ended_at ? `${when(r.ended_at)} · ${r.end_reason || ''}` : <Pill s="Active" />) }]} empty="No sessions." /></>}
      {resetting && <ResetPassword u={u} onDone={() => setResetting(false)} />}
    </Modal>
  );
}
