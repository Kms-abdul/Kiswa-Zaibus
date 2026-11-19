"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.startWorkers = startWorkers;
const config_1 = require("./config");
const log_1 = require("./log");
const outbox_1 = require("./outbox");
async function tick() {
    try {
        const r = await fetch(`${config_1.config.apiUrl}/internal/tick`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Internal-Key': config_1.config.internalKey }, body: '{}' });
        const j = await r.json();
        if (!r.ok)
            (0, log_1.log)('WARN', `tick answered ${r.status}`);
        else if (Object.values(j).some(v => v))
            (0, log_1.log)('INFO', `tick ${JSON.stringify(j)}`);
    }
    catch (e) {
        (0, log_1.log)('WARN', `tick: API not reachable (${e.message})`);
    }
}
let busy = false;
async function outbox() {
    if (busy)
        return;
    busy = true;
    try {
        const r = await (0, outbox_1.drainOutbox)();
        if (r.sent || r.failed)
            (0, log_1.log)('INFO', `outbox sent=${r.sent} retry/failed=${r.failed}`);
    }
    catch (e) {
        (0, log_1.log)('WARN', `outbox: ${e.message}`);
    }
    finally {
        busy = false;
    }
}
function startWorkers() {
    setInterval(tick, config_1.config.tickSeconds * 1000).unref();
    setInterval(outbox, config_1.config.outboxSeconds * 1000).unref();
    setTimeout(tick, 5000).unref();
    (0, log_1.log)('INFO', `workers on: scheduler every ${config_1.config.tickSeconds}s, outbox every ${config_1.config.outboxSeconds}s`);
}
