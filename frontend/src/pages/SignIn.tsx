import { useState } from 'react';
import { api, ApiError, deviceId } from '../api';
import { Link } from 'react-router-dom';
import { useAuth, type Me } from '../auth';

type Step = 'password' | 'otp' | 'change' | 'forgot' | 'reset' | 'resetDone';
type Challenge = { challenge: string; stage: string; mobile_hint?: string; dev_otp?: string; then?: string; user?: Me };

export default function SignIn() {
  const { setMe } = useAuth();
  const [step, setStep] = useState<Step>('password');
  const [login, setLogin] = useState('');
  const [pw, setPw] = useState('');
  const [otp, setOtp] = useState('');
  const [npw, setNpw] = useState('');
  const [npw2, setNpw2] = useState('');
  const [ch, setCh] = useState<Challenge | null>(null);
  const [captcha, setCaptcha] = useState<{ question: string; token: string } | null>(null);
  const [capAns, setCapAns] = useState('');
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);

  const next = (j: Challenge) => {
    if (j.stage === 'done' && j.user) { setMe(j.user); return; }
    setCh(j); setOtp(''); setErr('');
    setStep(j.stage === 'otp' ? 'otp' : 'change');
  };
  const run = async (fn: () => Promise<void>) => {
    setBusy(true); setErr('');
    try { await fn(); }
    catch (x) {
      if (x instanceof ApiError) {
        setErr(x.message);
        if (x.data.captcha) { setCaptcha(x.data.captcha as { question: string; token: string }); setCapAns(''); }
        if (x.code === 'challenge_expired' || (x.code === 'bad_otp' && x.data.tries_left === 0)) { setStep('password'); setPw(''); }
      } else setErr('The server could not be reached.');
    } finally { setBusy(false); }
  };

  const doLogin = (e: React.FormEvent) => { e.preventDefault(); run(async () => {
    next(await api<Challenge>('/auth/login', { body: { login, password: pw, device_id: deviceId(), captcha_token: captcha?.token, captcha_answer: capAns } }));
  }); };
  const doOtp = (e: React.FormEvent) => { e.preventDefault(); run(async () => {
    next(await api<Challenge>('/auth/otp', { body: { challenge: ch!.challenge, otp, device_id: deviceId() } }));
  }); };
  const doChange = (e: React.FormEvent) => { e.preventDefault();
    if (npw !== npw2) { setErr('The two passwords do not match.'); return; }
    run(async () => { next(await api<Challenge>('/auth/change-password', { body: { challenge: ch!.challenge, new_password: npw, device_id: deviceId() } })); });
  };
  const doForgot = (e: React.FormEvent) => { e.preventDefault(); run(async () => {
    const j = await api<Challenge>('/auth/forgot', { body: { login } }); setCh(j); setStep('reset');
  }); };
  const doReset = (e: React.FormEvent) => { e.preventDefault();
    if (npw !== npw2) { setErr('The two passwords do not match.'); return; }
    run(async () => { await api('/auth/reset', { body: { challenge: ch!.challenge, otp, new_password: npw } }); setStep('resetDone'); setPw(''); });
  };

  return (
    <div className="signin">
      <div className="card">
        <div className="brand big"><span className="mark">KZ</span><div><b>Kiswa-Zaibus</b><small>Operations</small></div></div>
        {step === 'password' && (
          <form onSubmit={doLogin}>
            <h2>Sign in</h2>
            <label>Employee ID or mobile<input value={login} onChange={e => setLogin(e.target.value)} autoFocus autoComplete="username" required /></label>
            <label>Password<input type="password" value={pw} onChange={e => setPw(e.target.value)} autoComplete="current-password" required /></label>
            {captcha && <label>{captcha.question}<input inputMode="numeric" value={capAns} onChange={e => setCapAns(e.target.value)} required /></label>}
            {err && <div className="err">{err}</div>}
            <button className="pri wide" disabled={busy}>{busy ? 'Checking…' : 'Continue'}</button>
            <button type="button" className="link" onClick={() => { setStep('forgot'); setErr(''); }}>Forgot password?</button>
          </form>
        )}
        {step === 'otp' && (
          <form onSubmit={doOtp}>
            <h2>Enter the code</h2>
            <p className="muted">We sent a 6-digit code to your mobile {ch?.mobile_hint}{ch?.then === 'change' ? '. You will then set a new password.' : '.'}</p>
            {ch?.dev_otp && <p className="note">Test mode code: <code>{ch.dev_otp}</code></p>}
            <label>One-time code<input inputMode="numeric" maxLength={6} value={otp} onChange={e => setOtp(e.target.value.replace(/\D/g, ''))} autoFocus autoComplete="one-time-code" required /></label>
            {err && <div className="err">{err}</div>}
            <button className="pri wide" disabled={busy || otp.length !== 6}>Verify</button>
            <button type="button" className="link" onClick={() => setStep('password')}>Back</button>
          </form>
        )}
        {(step === 'change' || step === 'reset') && (
          <form onSubmit={step === 'change' ? doChange : doReset}>
            <h2>{step === 'change' ? 'Set a new password' : 'Reset your password'}</h2>
            {step === 'reset' && <>
              <p className="muted">If the account exists, a code was sent to its registered mobile {ch?.mobile_hint}.</p>
              {ch?.dev_otp && <p className="note">Test mode code: <code>{ch.dev_otp}</code></p>}
              <label>One-time code<input inputMode="numeric" maxLength={6} value={otp} onChange={e => setOtp(e.target.value.replace(/\D/g, ''))} required /></label>
            </>}
            <p className="muted">At least 10 characters with upper and lower case, a digit and a symbol. It must not contain your name, employee ID or mobile, or repeat a recent password.</p>
            <label>New password<input type="password" value={npw} onChange={e => setNpw(e.target.value)} autoComplete="new-password" required /></label>
            <label>Repeat new password<input type="password" value={npw2} onChange={e => setNpw2(e.target.value)} autoComplete="new-password" required /></label>
            {err && <div className="err">{err}</div>}
            <button className="pri wide" disabled={busy}>Save password</button>
            <button type="button" className="link" onClick={() => setStep('password')}>Cancel</button>
          </form>
        )}
        {step === 'forgot' && (
          <form onSubmit={doForgot}>
            <h2>Forgot password</h2>
            <label>Employee ID or mobile<input value={login} onChange={e => setLogin(e.target.value)} autoFocus required /></label>
            {err && <div className="err">{err}</div>}
            <button className="pri wide" disabled={busy}>Send code</button>
            <button type="button" className="link" onClick={() => setStep('password')}>Back to sign in</button>
          </form>
        )}
        {step === 'resetDone' && (
          <div>
            <h2>Password changed</h2>
            <p className="muted">Sign in with your new password.</p>
            <button className="pri wide" onClick={() => setStep('password')}>Sign in</button>
          </div>
        )}
      </div>
      <p className="foot">Authorised staff only. All activity is recorded. Parents: <Link to="/shop">online store</Link></p>
    </div>
  );
}
