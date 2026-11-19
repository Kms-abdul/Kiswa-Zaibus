/* kz-docs: the Node.js document service of Kiswa-Zaibus Operations (release 2.3; report library 2.4).
   Listens on 127.0.0.1 only (nginx never exposes it); Flask calls it with a shared secret and falls back to Python rendering
   when it is down. Endpoints: GET /health · POST /render/xlsx · POST /render/pdf (JSON body, see render.ts). */
import express, { type NextFunction, type Request, type Response } from 'express';
import { timingSafeEqual } from 'node:crypto';
import { toPdf, toXlsx, validate } from './render.js';

export const VERSION = '2.8.0';

export function app(secret = process.env.DOCS_SECRET ?? '') {
  const a = express();
  a.disable('x-powered-by');
  a.use(express.json({ limit: process.env.DOCS_BODY_LIMIT ?? '25mb' }));

  const auth = (req: Request, res: Response, next: NextFunction) => {
    if (!secret) return next();                       // no secret configured: loopback-only service (development)
    const got = Buffer.from(String(req.get('X-KZ-Docs-Secret') ?? ''));
    const want = Buffer.from(secret);
    if (got.length !== want.length || !timingSafeEqual(got, want)) return res.status(401).json({ error: 'unauthorised', message: 'Wrong or missing X-KZ-Docs-Secret.' });
    next();
  };

  a.get('/health', (_req, res) => res.json({ ok: true, service: 'kz-docs', version: VERSION, node: process.version, uptime: Math.round(process.uptime()) }));

  a.post('/render/:kind', auth, async (req, res) => {
    const started = Date.now();
    try {
      const r = validate(req.body);
      const kind = req.params.kind;
      if (kind === 'xlsx') {
        const buf = await toXlsx(r);
        res.type('application/vnd.openxmlformats-officedocument.spreadsheetml.sheet').send(buf);
      } else if (kind === 'pdf') {
        const buf = await toPdf(r);
        res.type('application/pdf').send(buf);
      } else {
        return res.status(404).json({ error: 'unknown_format', message: 'Use /render/xlsx or /render/pdf.' });
      }
      console.log(JSON.stringify({ at: new Date().toISOString(), kind, rows: r.rows.length, ms: Date.now() - started }));
    } catch (e) {
      res.status(400).json({ error: 'invalid', message: (e as Error).message });
    }
  });

  a.use((err: Error, _req: Request, res: Response, _next: NextFunction) => {
    res.status(err.name === 'PayloadTooLargeError' ? 413 : 400).json({ error: 'bad_request', message: err.message });
  });
  return a;
}

const isMain = process.argv[1] && import.meta.url === new URL(`file://${process.argv[1]}`).href;
if (isMain) {
  const host = process.env.DOCS_HOST ?? '127.0.0.1';
  const port = Number(process.env.DOCS_PORT ?? 8095);
  app().listen(port, host, () => console.log(`kz-docs ${VERSION} listening on http://${host}:${port}`));
}
