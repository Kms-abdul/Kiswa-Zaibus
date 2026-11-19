import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import { test } from 'node:test';
import { verifySignature } from '../razorpay';

test('accepts a correct Razorpay signature and rejects tampering', () => {
  const secret = 'whsec_test', body = Buffer.from('{"event":"payment.captured"}');
  const sig = crypto.createHmac('sha256', secret).update(body).digest('hex');
  assert.equal(verifySignature(body, sig, secret), true);
  assert.equal(verifySignature(Buffer.from('{"event":"payment.captured "}'), sig, secret), false);
  assert.equal(verifySignature(body, sig, 'other'), false);
  assert.equal(verifySignature(body, undefined, secret), false);
  assert.equal(verifySignature(body, 'zz', secret), false);
  assert.equal(verifySignature(body, sig, ''), false);
});
