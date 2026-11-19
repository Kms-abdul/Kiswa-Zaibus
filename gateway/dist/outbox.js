"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
exports.db = db;
exports.drainOutbox = drainOutbox;
/* Sends queued messages (email via SMTP, SMS and WhatsApp via the provider's HTTP API), with retries.
   OTP message bodies are blanked once sent so codes never stay in the database. */
const promise_1 = __importDefault(require("mysql2/promise"));
const nodemailer_1 = __importDefault(require("nodemailer"));
const config_1 = require("./config");
const log_1 = require("./log");
const MAX_ATTEMPTS = 5;
let pool = null;
let mailer = null;
function db() {
    if (!pool)
        pool = promise_1.default.createPool({ ...config_1.config.db, connectionLimit: 4, charset: 'utf8mb4', timezone: 'local' });
    return pool;
}
async function send(m) {
    if (m.channel === 'email') {
        if (!config_1.config.smtp.host)
            throw new Error('SMTP is not configured (SMTP_HOST)');
        mailer = mailer || nodemailer_1.default.createTransport({ host: config_1.config.smtp.host, port: config_1.config.smtp.port, secure: config_1.config.smtp.secure,
            auth: config_1.config.smtp.user ? { user: config_1.config.smtp.user, pass: config_1.config.smtp.password } : undefined });
        await mailer.sendMail({ from: config_1.config.smtp.from, to: m.to_name ? `"${m.to_name.replace(/"/g, '')}" <${m.address}>` : m.address, subject: m.subject || 'Kiswa-Zaibus', text: m.body });
        return;
    }
    const p = m.channel === 'sms' ? config_1.config.sms : config_1.config.whatsapp;
    if (!p.url)
        throw new Error(`${m.channel.toUpperCase()} provider is not configured`);
    const headers = { 'Content-Type': 'application/json' };
    if (m.channel === 'sms' && config_1.config.sms.key)
        headers.Authorization = `Bearer ${config_1.config.sms.key}`;
    if (m.channel === 'whatsapp' && config_1.config.whatsapp.token)
        headers.Authorization = `Bearer ${config_1.config.whatsapp.token}`;
    const to = m.address.replace(/\D/g, '').slice(-10);
    const resp = await fetch(p.url, { method: 'POST', headers, body: JSON.stringify({ to: '91' + to, sender: config_1.config.sms.sender, message: m.body }) });
    if (!resp.ok)
        throw new Error(`${m.channel} provider answered ${resp.status}`);
}
async function drainOutbox(limit = 20) {
    const c = db();
    // claim a batch (single gateway instance; the status flip makes a crash-restart safe)
    await c.query("UPDATE outbox SET status='Queued' WHERE status='Sending' AND created_at < NOW() - INTERVAL 10 MINUTE");
    // retry with back-off: 2, 4, 8, 16 minutes after the message was queued
    const [rows] = await c.query("SELECT * FROM outbox WHERE status='Queued' AND attempts < ? AND (attempts = 0 OR created_at < NOW() - INTERVAL POW(2, attempts) MINUTE) ORDER BY id LIMIT ?", [MAX_ATTEMPTS, limit]);
    let sent = 0, failed = 0;
    for (const m of rows) {
        const [claim] = await c.query("UPDATE outbox SET status='Sending', attempts=attempts+1 WHERE id=? AND status='Queued'", [m.id]);
        if (claim.affectedRows !== 1)
            continue;
        try {
            await send(m);
            await c.query("UPDATE outbox SET status='Sent', sent_at=NOW(6), last_error=NULL, body=IF(alert_type='OTP','[one-time code sent]',body) WHERE id=?", [m.id]);
            sent++;
        }
        catch (e) {
            const final = m.attempts + 1 >= MAX_ATTEMPTS;
            await c.query("UPDATE outbox SET status=?, last_error=?, body=IF(?=1 AND alert_type='OTP','[one-time code not delivered]',body) WHERE id=?", [final ? 'Failed' : 'Queued', e.message.slice(0, 300), final ? 1 : 0, m.id]);
            if (final)
                (0, log_1.log)('WARN', `outbox #${m.id} ${m.channel} to ${m.address} failed: ${e.message}`);
            failed++;
        }
    }
    return { sent, failed };
}
