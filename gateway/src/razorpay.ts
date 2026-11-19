/* Razorpay webhook: verify X-Razorpay-Signature = HMAC-SHA256(raw body, webhook secret), then hand to Flask.
   One webhook URL per company: /webhooks/razorpay/KG and /webhooks/razorpay/ZE (each Razorpay account has its own secret). */
import crypto from 'crypto';
import express, { Request, Response } from 'express';
import { config } from './config';
import { log } from './log';

export function verifySignature(raw: Buffer, signature: string | undefined, secret: string): boolean {
  if (!secret || !signature || !/^[0-9a-f]{64}$/i.test(signature)) return false;
  const expected = crypto.createHmac('sha256', secret).update(raw).digest('hex');
  return crypto.timingSafeEqual(Buffer.from(expected, 'hex'), Buffer.from(signature.toLowerCase(), 'hex'));
}

export function razorpayRouter() {
  const r = express.Router();
  r.post('/webhooks/razorpay/:company', express.raw({ type: '*/*', limit: '1mb' }), async (req: Request, res: Response) => {
    const company = String(req.params.company || '').toUpperCase();
    if (!(company in config.razorpaySecrets)) return res.status(404).json({ error: 'unknown_company' });
    const raw: Buffer = Buffer.isBuffer(req.body) ? req.body : Buffer.from('');
    const ok = verifySignature(raw, req.header('X-Razorpay-Signature'), config.razorpaySecrets[company]);
    try {
      const resp = await fetch(`${config.apiUrl}/internal/razorpay/event`, {
        method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Internal-Key': config.internalKey },
        body: JSON.stringify({ company, signature_ok: ok, event_id: req.header('X-Razorpay-Event-Id') || undefined, body: raw.toString('utf8') }),
      });
      const j = await resp.json() as { status?: string };
      log(ok ? 'INFO' : 'WARN', `razorpay webhook ${company} signature=${ok ? 'ok' : 'BAD'} -> ${j.status}`);
      if (!ok) return res.status(400).json({ error: 'bad_signature' });
      return res.json({ status: j.status });
    } catch (e) {
      log('ERROR', `razorpay webhook could not reach the API: ${(e as Error).message}`);
      return res.status(503).json({ error: 'api_unavailable' });   // Razorpay retries non-2xx
    }
  });
  return r;
}
