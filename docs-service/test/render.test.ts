import assert from 'node:assert/strict';
import type { AddressInfo } from 'node:net';
import { after, before, test } from 'node:test';
import { app } from '../src/server.js';
import { fmt, validate } from '../src/render.js';

const REPORT = {
  title: 'Trial Balance', subtitle: 'Kiswa Garments · 2026-04-01 to 2026-11-10',
  columns: [{ h: 'Code', t: 'text' }, { h: 'Account', t: 'text' }, { h: 'Date', t: 'date' }, { h: 'Debit', t: 'num' }, { h: 'Credit', t: 'num' }, { h: 'Share', t: 'pct' }],
  rows: [['1100', 'Cash', '2026-11-01', 12500.5, null, 12.5], ['4000', 'Sales', '2026-11-02', null, 12500.5, 87.5]],
  totals: ['', 'Total', '', 12500.5, 12500.5, null], note: 'Balanced',
};

let base = '';
let server: ReturnType<ReturnType<typeof app>['listen']>;
before(async () => { server = app('s3cret').listen(0, '127.0.0.1'); await new Promise(r => server.once('listening', r)); base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`; });
after(() => server.close());

const post = (kind: string, body: unknown, secret = 's3cret') => fetch(`${base}/render/${kind}`, { method: 'POST', headers: { 'content-type': 'application/json', 'X-KZ-Docs-Secret': secret }, body: JSON.stringify(body) });

test('health answers without the secret', async () => {
  const r = await fetch(`${base}/health`);
  assert.equal(r.status, 200);
  assert.equal((await r.json()).service, 'kz-docs');
});

test('xlsx is a real workbook', async () => {
  const r = await post('xlsx', REPORT);
  assert.equal(r.status, 200);
  const b = Buffer.from(await r.arrayBuffer());
  assert.equal(b.subarray(0, 2).toString(), 'PK');
});

test('pdf is a real PDF, also with many rows (page breaks)', async () => {
  const many = { ...REPORT, rows: Array.from({ length: 400 }, (_, i) => [String(1000 + i), `Account ${i}`, '2026-11-01', i * 10.25, null, 1]) };
  const r = await post('pdf', many);
  assert.equal(r.status, 200);
  const b = Buffer.from(await r.arrayBuffer());
  assert.equal(b.subarray(0, 4).toString(), '%PDF');
  assert.ok(b.length > 5000);
});

test('wrong secret is refused; bad body is a 400', async () => {
  assert.equal((await post('pdf', REPORT, 'nope')).status, 401);
  const r = await post('xlsx', { title: 'x', columns: [{ h: 'A', t: 'money' }], rows: [] });
  assert.equal(r.status, 400);
  assert.equal((await post('docx', REPORT)).status, 404);
});

test('validation and formatting', () => {
  assert.throws(() => validate({ columns: [] }));
  assert.equal(fmt(1234567.5, 'num'), '12,34,567.50');
  assert.equal(fmt('2026-11-10', 'date'), '10-11-2026');
  assert.equal(fmt(null, 'num'), '');
});
