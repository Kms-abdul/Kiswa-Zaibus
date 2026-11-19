"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
exports.config = void 0;
/* Reads ../backend/.env (one configuration file for the whole install) and the environment. */
const fs_1 = __importDefault(require("fs"));
const path_1 = __importDefault(require("path"));
const ROOT = path_1.default.resolve(__dirname, '..', '..');
const env = {};
for (const f of [path_1.default.join(ROOT, 'backend', '.env'), path_1.default.join(ROOT, '.env')]) {
    if (!fs_1.default.existsSync(f))
        continue;
    for (const line of fs_1.default.readFileSync(f, 'utf8').split(/\r?\n/)) {
        const m = line.match(/^\s*([A-Z0-9_]+)\s*=\s*(.*)\s*$/);
        if (m && !line.trim().startsWith('#') && env[m[1]] === undefined)
            env[m[1]] = m[2].trim().replace(/^["']|["']$/g, '');
    }
}
const get = (k, d = '') => process.env[k] ?? env[k] ?? d;
const bool = (k, d = false) => { const v = get(k); return v === '' ? d : ['1', 'true', 'yes', 'on'].includes(v.toLowerCase()); };
exports.config = {
    root: ROOT,
    host: get('GATEWAY_HOST', '0.0.0.0'),
    port: Number(get('GATEWAY_PORT', '8080')),
    tlsPfx: get('TLS_PFX_PATH'),
    tlsPassphrase: get('TLS_PFX_PASSWORD'),
    apiUrl: `http://${get('API_HOST', '127.0.0.1')}:${get('API_PORT', '5000')}`,
    internalKey: get('INTERNAL_KEY'),
    staticDir: get('FRONTEND_DIST', path_1.default.join(ROOT, 'frontend', 'dist')),
    logDir: get('LOG_DIR', path_1.default.join(ROOT, 'logs')),
    trustProxy: get('TRUST_PROXY', 'loopback'),
    httpsPublic: bool('PUBLIC_HTTPS', false) || !!get('TLS_PFX_PATH'), // site is reached over https (directly or via IIS/Nginx)          // IIS / Nginx in front on the same server
    db: {
        host: get('DB_HOST', '127.0.0.1'), port: Number(get('DB_PORT', '3306')), user: get('DB_USER', 'kz_app'),
        password: get('DB_PASSWORD'), database: get('DB_NAME', 'kiswa_zaibus'),
    },
    razorpaySecrets: { KG: get('RAZORPAY_WEBHOOK_SECRET_KG'), ZE: get('RAZORPAY_WEBHOOK_SECRET_ZE') },
    smtp: {
        host: get('SMTP_HOST'), port: Number(get('SMTP_PORT', '587')), secure: bool('SMTP_SECURE', false),
        user: get('SMTP_USER'), password: get('SMTP_PASSWORD'), from: get('SMTP_FROM', 'Kiswa-Zaibus <no-reply@kiswazaibus.in>'),
    },
    sms: { url: get('SMS_API_URL'), key: get('SMS_API_KEY'), sender: get('SMS_SENDER_ID', 'KZOPS') },
    whatsapp: { url: get('WHATSAPP_API_URL'), token: get('WHATSAPP_API_TOKEN') },
    tickSeconds: Number(get('TICK_SECONDS', '60')),
    outboxSeconds: Number(get('OUTBOX_SECONDS', '10')),
    workers: bool('GATEWAY_WORKERS', true),
};
