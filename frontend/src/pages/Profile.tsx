import { useState } from 'react';
import { api } from '../api';
import { useAuth } from '../auth';
import { Btn, Field, Kv, Page, Panel, useAction } from '../ui/kit';

export default function Profile() {
  const { me, signOut } = useAuth();
  const [cur, setCur] = useState(''); const [n1, setN1] = useState(''); const [n2, setN2] = useState('');
  const [run, busy] = useAction();
  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    if (n1 !== n2) return alertMismatch();
    const r = await run(() => api('/auth/password', { body: { current_password: cur, new_password: n1 } }), () => 'Password changed.');
    if (r) { setCur(''); setN1(''); setN2(''); }
  };
  const alertMismatch = () => run(() => Promise.reject(new Error('The two new passwords do not match.')));
  return (
    <Page title="My account">
      <Panel title="Details">
        <Kv items={[['Name', me!.name], ['Employee ID', me!.emp_id], ['Role', [me!.role_label || me!.role, ...me!.roles.filter(r => r !== me!.role)].join(', ')],
          ['Companies', me!.companies.join(', ')], ['Locations', me!.branches === '*' ? 'All locations and Head Office' : (me!.branches as string[]).join(', ')]]} />
      </Panel>
      <Panel title="Change password">
        <form onSubmit={save} className="form narrow">
          <Field label="Current password"><input type="password" value={cur} onChange={e => setCur(e.target.value)} autoComplete="current-password" required /></Field>
          <Field label="New password" hint="At least 10 characters with upper and lower case, a digit and a symbol; not your name, ID or mobile; not a recent password.">
            <input type="password" value={n1} onChange={e => setN1(e.target.value)} autoComplete="new-password" required /></Field>
          <Field label="Repeat new password"><input type="password" value={n2} onChange={e => setN2(e.target.value)} autoComplete="new-password" required /></Field>
          <div className="row"><Btn kind="pri" busy={busy}>Change password</Btn><Btn type="button" onClick={signOut}>Sign out</Btn></div>
        </form>
      </Panel>
    </Page>
  );
}
