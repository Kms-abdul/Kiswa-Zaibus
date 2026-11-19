"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
const strict_1 = __importDefault(require("node:assert/strict"));
const node_crypto_1 = __importDefault(require("node:crypto"));
const node_test_1 = require("node:test");
const razorpay_1 = require("../razorpay");
(0, node_test_1.test)('accepts a correct Razorpay signature and rejects tampering', () => {
    const secret = 'whsec_test', body = Buffer.from('{"event":"payment.captured"}');
    const sig = node_crypto_1.default.createHmac('sha256', secret).update(body).digest('hex');
    strict_1.default.equal((0, razorpay_1.verifySignature)(body, sig, secret), true);
    strict_1.default.equal((0, razorpay_1.verifySignature)(Buffer.from('{"event":"payment.captured "}'), sig, secret), false);
    strict_1.default.equal((0, razorpay_1.verifySignature)(body, sig, 'other'), false);
    strict_1.default.equal((0, razorpay_1.verifySignature)(body, undefined, secret), false);
    strict_1.default.equal((0, razorpay_1.verifySignature)(body, 'zz', secret), false);
    strict_1.default.equal((0, razorpay_1.verifySignature)(body, sig, ''), false);
});
