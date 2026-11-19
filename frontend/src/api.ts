/* API client. Every call goes to /api on the same origin (Nginx proxies it to Flask).
   Errors carry the API's {error, message}: docs/TROUBLESHOOTING.md lists every error code.
   A 428 (password re-entry needed for a high-risk action) opens the re-authentication dialog and retries once. */
export class ApiError extends Error {
  status: number; code: string; data: Record<string, unknown>;
  constructor(status: number, data: Record<string, unknown>) {
    super(String(data.message || (status === 0 ? 'No connection. Check the internet and try again.' : `Request failed (${status})`)));
    this.status = status; this.code = String(data.error || 'error'); this.data = data;
  }
}

// plain words for answers that do not come from the application itself (proxy pages, limits)
const PLAIN: Record<number, string> = {
  413: 'The file is too large to upload. Use a smaller file or a lower photo resolution.',
  429: 'Too many attempts in a short time. Wait a minute and try again.',
  500: 'The server hit a problem and nothing was saved. Try again; if it repeats, tell IT the time it happened.',
  502: 'The server is restarting or not reachable. Wait a minute and try again — nothing was saved.',
  503: 'The server is busy or under maintenance. Wait a minute and try again — nothing was saved.',
  504: 'The server took too long to answer. Check the screen before trying again: the action may have completed.',
};

type Handler = () => Promise<boolean>;
let reauthHandler: Handler | null = null;
let unauthHandler: (() => void) | null = null;
export const onReauth = (h: Handler) => { reauthHandler = h; };
export const onUnauthorised = (h: () => void) => { unauthHandler = h; };

export async function api<T = any>(path: string, opts: { method?: string; body?: unknown; form?: FormData; raw?: boolean } = {}, retried = false): Promise<T> {
  let res: Response;
  try {
    res = await fetch('/api' + path, {
      method: opts.method || (opts.body !== undefined || opts.form ? 'POST' : 'GET'),
      headers: opts.body !== undefined ? { 'Content-Type': 'application/json' } : {},
      body: opts.form ?? (opts.body !== undefined ? JSON.stringify(opts.body) : undefined),
      credentials: 'same-origin',
    });
  } catch {
    throw new ApiError(0, { error: 'offline' });
  }
  if (opts.raw && res.ok) return res as unknown as T;
  const text = await res.text();
  let data: any = {};
  try { data = text ? JSON.parse(text) : {}; } catch { data = { message: PLAIN[res.status] || 'The server answered in an unexpected way. Try again; if it repeats, tell IT.' }; }
  if (!data.message && PLAIN[res.status]) data.message = PLAIN[res.status];
  if (res.status === 428 && !retried && reauthHandler && (await reauthHandler())) return api<T>(path, opts, true);
  if (res.status === 401 && !path.startsWith('/auth/') && !path.startsWith('/shop/') && unauthHandler) unauthHandler();
  if (!res.ok) throw new ApiError(res.status, data);
  return data as T;
}

export async function download(path: string, filename: string) {
  const res = await api<Response>(path, { raw: true });
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = filename; document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

export const errText = (e: unknown) => (e instanceof ApiError ? e.message : e instanceof Error ? e.message : 'Something went wrong.');

export function deviceId() {
  try {
    let d = localStorage.getItem('kz_device');
    if (!d) { d = crypto.randomUUID(); localStorage.setItem('kz_device', d); }
    return d;
  } catch { return 'no-storage'; }
}

export const store = {
  get<T>(k: string, d: T): T { try { const v = localStorage.getItem(k); return v ? (JSON.parse(v) as T) : d; } catch { return d; } },
  set(k: string, v: unknown) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode */ } },
};
