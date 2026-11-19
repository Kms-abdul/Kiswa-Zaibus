/* Sends queued messages (email via SMTP, SMS and WhatsApp via the provider's HTTP API), with retries.
   OTP message bodies are blanked once sent so codes never stay in the database. */
import mysql from 'mysql2/promise';
import nodemailer from 'nodemailer';
import { config } from './config';
import { log } from './log';

type Row = { id: number; channel: 'email' | 'whatsapp' | 'sms'; to_name: string | null; address: string; subject: string | null; body: string; alert_type: string | null; attempts: number };
const MAX_ATTEMPTS = 5;
let pool: mysql.Pool | null = null;
let mailer: nodemailer.Transporter | null = null;

export function db() {
  if (!pool) pool = mysql.createPool({ ...config.db, connectionLimit: 4, charset: 'utf8mb4', timezone: 'local' });
  return pool;
}

async function send(m: Row) {
  if (m.channel === 'email') {
    if (!config.smtp.host) throw new Error('SMTP is not configured (SMTP_HOST)');
    mailer = mailer || nodemailer.createTransport({ host: config.smtp.host, port: config.smtp.port, secure: config.smtp.secure,
      auth: config.smtp.user ? { user: config.smtp.user, pass: config.smtp.password } : undefined });
    await mailer.sendMail({ from: config.smtp.from, to: m.to_name ? `"${m.to_name.replace(/"/g, '')}" <${m.address}>` : m.address, subject: m.subject || 'Kiswa-Zaibus', text: m.body });
    return;
  }
  const p = m.channel === 'sms' ? config.sms : config.whatsapp;
  if (!p.url) throw new Error(`${m.channel.toUpperCase()} provider is not configured`);
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (m.channel === 'sms' && config.sms.key) headers.Authorization = `Bearer ${config.sms.key}`;
  if (m.channel === 'whatsapp' && config.whatsapp.token) headers.Authorization = `Bearer ${config.whatsapp.token}`;
  const to = m.address.replace(/\D/g, '').slice(-10);
  const resp = await fetch(p.url, { method: 'POST', headers, body: JSON.stringify({ to: '91' + to, sender: config.sms.sender, message: m.body }) });
  if (!resp.ok) throw new Error(`${m.channel} provider answered ${resp.status}`);
}

export async function drainOutbox(limit = 20): Promise<{ sent: number; failed: number }> {
  const c = db();
  // claim a batch (single gateway instance; the status flip makes a crash-restart safe)
  await c.query("UPDATE outbox SET status='Queued' WHERE status='Sending' AND created_at < NOW() - INTERVAL 10 MINUTE");
  // retry with back-off: 2, 4, 8, 16 minutes after the message was queued
  const [rows] = await c.query("SELECT * FROM outbox WHERE status='Queued' AND attempts < ? AND (attempts = 0 OR created_at < NOW() - INTERVAL POW(2, attempts) MINUTE) ORDER BY id LIMIT ?", [MAX_ATTEMPTS, limit]);
  let sent = 0, failed = 0;
  for (const m of rows as Row[]) {
    const [claim] = await c.query("UPDATE outbox SET status='Sending', attempts=attempts+1 WHERE id=? AND status='Queued'", [m.id]);
    if ((claim as mysql.ResultSetHeader).affectedRows !== 1) continue;
    try {
      await send(m);
      await c.query("UPDATE outbox SET status='Sent', sent_at=NOW(6), last_error=NULL, body=IF(alert_type='OTP','[one-time code sent]',body) WHERE id=?", [m.id]);
      sent++;
    } catch (e) {
      const final = m.attempts + 1 >= MAX_ATTEMPTS;
      await c.query("UPDATE outbox SET status=?, last_error=?, body=IF(?=1 AND alert_type='OTP','[one-time code not delivered]',body) WHERE id=?",
        [final ? 'Failed' : 'Queued', (e as Error).message.slice(0, 300), final ? 1 : 0, m.id]);
      if (final) log('WARN', `outbox #${m.id} ${m.channel} to ${m.address} failed: ${(e as Error).message}`);
      failed++;
    }
  }
  return { sent, failed };
}
