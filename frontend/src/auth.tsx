import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { api, onUnauthorised, store } from './api';

export type Me = {
  id: string; name: string; emp_id: string; role: string; role_label?: string; roles: string[]; designation: string | null;
  companies: string[]; branches: string[] | '*'; rank: number; read_only: boolean; permissions: Record<string, string[]>;
};
export type Branch = { id: string; company: string; location: string; code: string; active: number; name: string; type: string };
export type Org = { companies: { code: string; name: string; short_name: string }[]; branches: Branch[]; locations: any[] };

type Ctx = {
  me: Me | null; loading: boolean; setMe: (m: Me | null) => void; signOut: () => Promise<void>;
  can: (action: string, key: string) => boolean; org: Org | null; refreshOrg: () => void;
  location: string; setLocation: (l: string) => void; myBranches: Branch[];
};
const AuthCtx = createContext<Ctx>(null as unknown as Ctx);
const RO = ['view', 'print', 'pdf', 'export', 'audit', 'fin', 'cost'];
// same narrow exception as the backend (core/rbac.py RO_EXCEPTIONS): a read-only internal auditor may count stock audits
const RO_EXCEPTIONS: Record<string, string[]> = { saudit: ['edit', 'submit', 'upload', 'import'] };

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  const [org, setOrg] = useState<Org | null>(null);
  const [location, setLoc] = useState<string>(store.get('kz_location', ''));
  useEffect(() => {
    api<Me>('/auth/me').then(setMe).catch(() => setMe(null)).finally(() => setLoading(false));
    onUnauthorised(() => setMe(null));
  }, []);  // eslint-disable-line react-hooks/exhaustive-deps
  const refreshOrg = useCallback(() => { api<Org>('/org').then(setOrg).catch(() => setOrg(null)); }, []);
  useEffect(() => { if (me) refreshOrg(); else setOrg(null); }, [me, refreshOrg]);
  const myBranches = useMemo(() => (org?.branches || []).filter(b => b.active), [org]);
  useEffect(() => {
    const locs = [...new Set(myBranches.map(b => b.location))];
    if (locs.length && !locs.includes(location)) { setLoc(locs[0]); store.set('kz_location', locs[0]); }
  }, [myBranches, location]);
  const setLocation = (l: string) => { setLoc(l); store.set('kz_location', l); };
  const signOut = useCallback(async () => { try { await api('/auth/logout', { body: {} }); } finally { setMe(null); } }, []);
  const can = useCallback((action: string, key: string) => {
    if (!me) return false;
    if (me.read_only && !RO.includes(action) && !(RO_EXCEPTIONS[key] || []).includes(action)) return false;
    const m = key.split('.')[0];
    const acts = me.permissions[key] ?? me.permissions[m] ?? [];
    if (acts.includes(action)) return true;
    return action === 'view' && !key.includes('.') && Object.entries(me.permissions).some(([k, v]) => k.startsWith(key + '.') && v.includes('view'));
  }, [me]);
  return <AuthCtx.Provider value={{ me, loading, setMe, signOut, can, org, refreshOrg, location, setLocation, myBranches }}>{children}</AuthCtx.Provider>;
}
export const useAuth = () => useContext(AuthCtx);
export const hasRole = (me: Me | null, ...roles: string[]) => !!me && me.roles.some(r => roles.includes(r));
