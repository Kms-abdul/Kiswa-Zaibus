"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
exports.verifySignature = verifySignature;
exports.razorpayRouter = razorpayRouter;
/* Razorpay webhook: verify X-Razorpay-Signature = HMAC-SHA256(raw body, webhook secret), then hand to Flask.
   One webhook URL per company: /webhooks/razorpay/KG and /webhooks/razorpay/ZE (each Razorpay account has its own secret). */
const crypto_1 = __importDefault(require("crypto"));
const express_1 = __importDefault(require("express"));
const config_1 = require("./config");
const log_1 = require("./log");
function verifySignature(raw, signature, secret) {
    if (!secret || !signature || !/^[0-9a-f]{64}$/i.test(signature))
        return false;
    const expected = crypto_1.default.createHmac('sha256', secret).update(raw).digest('hex');
    return crypto_1.default.timingSafeEqual(Buffer.from(expected, 'hex'), Buffer.from(signature.toLowerCase(), 'hex'));
}
function razorpayRouter() {
    const r = express_1.default.Router();
    r.post('/webhooks/razorpay/:company', express_1.default.raw({ type: '*/*', limit: '1mb' }), async (req, res) => {
        const company = String(req.params.company || '').toUpperCase();
        if (!(company in config_1.config.razorpaySecrets))
            return res.status(404).json({ error: 'unknown_company' });
        const raw = Buffer.isBuffer(req.body) ? req.body : Buffer.from('');
        const ok = verifySignature(raw, req.header('X-Razorpay-Signature'), config_1.config.razorpaySecrets[company]);
        try {
            const resp = await fetch(`${config_1.config.apiUrl}/internal/razorpay/event`, {
                method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Internal-Key': config_1.config.internalKey },
                body: JSON.stringify({ company, signature_ok: ok, event_id: req.header('X-Razorpay-Event-Id') || undefined, body: raw.toString('utf8') }),
            });
            const j = await resp.json();
            (0, log_1.log)(ok ? 'INFO' : 'WARN', `razorpay webhook ${company} signature=${ok ? 'ok' : 'BAD'} -> ${j.status}`);
            if (!ok)
                return res.status(400).json({ error: 'bad_signature' });
            return res.json({ status: j.status });
        }
        catch (e) {
            (0, log_1.log)('ERROR', `razorpay webhook could not reach the API: ${e.message}`);
            return res.status(503).json({ error: 'api_unavailable' }); // Razorpay retries non-2xx
        }
    });
    return r;
}
