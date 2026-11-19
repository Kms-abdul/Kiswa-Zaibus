import { config } from './config';
import { log } from './log';
import { drainOutbox } from './outbox';

async function tick() {
  try {
    const r = await fetch(`${config.apiUrl}/internal/tick`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Internal-Key': config.internalKey }, body: '{}' });
    const j = await r.json() as Record<string, number>;
    if (!r.ok) log('WARN', `tick answered ${r.status}`);
    else if (Object.values(j).some(v => v)) log('INFO', `tick ${JSON.stringify(j)}`);
  } catch (e) { log('WARN', `tick: API not reachable (${(e as Error).message})`); }
}

let busy = false;
async function outbox() {
  if (busy) return; busy = true;
  try { const r = await drainOutbox(); if (r.sent || r.failed) log('INFO', `outbox sent=${r.sent} retry/failed=${r.failed}`); }
  catch (e) { log('WARN', `outbox: ${(e as Error).message}`); }
  finally { busy = false; }
}

export function startWorkers() {
  setInterval(tick, config.tickSeconds * 1000).unref();
  setInterval(outbox, config.outboxSeconds * 1000).unref();
  setTimeout(tick, 5000).unref();
  log('INFO', `workers on: scheduler every ${config.tickSeconds}s, outbox every ${config.outboxSeconds}s`);
}
