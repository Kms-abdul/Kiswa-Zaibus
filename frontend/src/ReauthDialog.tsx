import { useEffect, useRef, useState } from 'react';
import { api, ApiError, onReauth } from './api';

/* Opens when the API answers 428: the user re-enters their password for a high-risk action. */
export default function ReauthDialog() {
  const [open, setOpen] = useState(false);
  const [pw, setPw] = useState('');
  const [err, setErr] = useState('');
  const resolver = useRef<((ok: boolean) => void) | null>(null);
  useEffect(() => onReauth(() => new Promise<boolean>(res => { resolver.current = res; setPw(''); setErr(''); setOpen(true); })), []);
  const close = (ok: boolean) => { setOpen(false); resolver.current?.(ok); resolver.current = null; };
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    try { await api('/auth/reauth', { body: { password: pw } }); close(true); }
    catch (x) { setErr(x instanceof ApiError ? x.message : 'Could not confirm.'); }
  };
  if (!open) return null;
  return (
    <div className="modal-bg">
      <form className="modal" onSubmit={submit}>
        <h3>Confirm it's you</h3>
        <p className="muted">This is a high-risk action. Re-enter your password to continue.</p>
        <label>Password<input type="password" autoFocus value={pw} onChange={e => setPw(e.target.value)} autoComplete="current-password" /></label>
        {err && <div className="err">{err}</div>}
        <div className="row end"><button type="button" className="ghost" onClick={() => close(false)}>Cancel</button><button className="pri">Confirm</button></div>
      </form>
    </div>
  );
}
