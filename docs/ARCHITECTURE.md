# Architecture

```
 Browser (staff, phone or PC)
   │ https
   ▼
 IIS / Nginx (optional, TLS)          Razorpay ──► /webhooks/razorpay/{KG|ZE}
   │                                                    │
   ▼                                                    ▼
 KZ-Gateway  (Node 20, Express, TypeScript) ── verifies HMAC-SHA256 signature
   ├─ serves the React app (frontend/dist)
   ├─ /api/*  ─────────────────────────► KZ-API (Flask on waitress, 127.0.0.1:5000)
   ├─ scheduler: POST /internal/tick every minute         │
   └─ outbox worker: email (SMTP), SMS, WhatsApp           │
            │                                             ▼
            └───────────────── MySQL 8 (127.0.0.1:3306) ◄─┘
```

## Request handling (API)
- One database connection and one transaction per request; committed on success, rolled back on error.
  Sign-in failures, lockouts and security events are kept even when the request answers with an error.
- Sessions: random 256-bit token in an **HttpOnly, SameSite=Strict** cookie (Secure with https); only its SHA-256 is stored.
  Idle timeout and "one session per user" come from the security policy.
- Mutations must be JSON, which together with SameSite=Strict blocks cross-site form posts.
- Internal endpoints (`/internal/*`) need the shared `INTERNAL_KEY` and are not reachable through the gateway.

## Security model
| Control | Where |
|---|---|
| Password policy (length, classes, not name/ID/mobile, history 5, expiry 90 days) | `kz/security.py`, settings `security` |
| Lockout after 5 failures, CAPTCHA after 3, OTP for all or chosen roles | `kz/api/auth.py` |
| Status, validity dates, working hours, new-device alerts | `kz/api/auth.py` |
| Permission matrix (role × screen × action), read-only roles, cost/finance visibility | `kz/rbac.py`, table `permissions` |
| Branch scope from assigned branches, zones and regions | `kz/rbac.py` |
| Re-enter password for high-risk actions (approve, users, reverse, restore, delete) | `rbac.high_risk`, HTTP 428 |
| Maker-checker: not own request, rank, scope, Super-Admin-only, evidence rule | `kz/approvals.py` |
| Audit trail: append-only (DB triggers) and SHA-256 hash-chained; verify from the app | `kz/audit.py`, `schema.sql` |
| App DB account has data rights only (cannot drop triggers or alter tables) | `setup.ps1` |
| Webhook HMAC verification, duplicate-event protection, Live mode gated by approval | `gateway/src/razorpay.ts`, `kz/api/system.py` |
| OTP codes and temporary passwords never stored in readable form after delivery | `outbox.ts`, `users.py` |

## Data model (81 tables)
Organisation (companies, locations, branches, zones, regions, number series, settings) · Access (users, roles,
permissions, limits, sessions, challenges, devices, password history, access schedule, security events) · Audit
(audit_log, audit_head, access_log) · Approvals (approvals, approval_log, routing_rules) · Evidence · Masters (accounts,
schools, categories, suppliers, items, packages, students, customers, MSEA vouchers) · Inventory (stock balance and
ledger, transfers, adjustments) · Purchasing (POs, quotes, GRNs, supplier bills) · Sales (invoices, lines, payments,
credit notes, held bills) · Accounts (journals, lines, receipts) · Cash custody (shifts, handovers, owner instructions) ·
Online (orders, parcels, requests, refunds) · Razorpay (orders, payments, refunds, settlements, webhook events,
payment links, exceptions, reconciliation sign-off) · ERP (sync runs, records, keys) · Alerts (rules, alerts, outbox).

Money is `DECIMAL(16,2)`; each company keeps separate books; journal lines carry a branch so every branch balances.

## Adding a module (next releases)
1. Endpoints in `backend/kz/api/<module>.py`, protected with `@require(action, 'module.screen')`; scope with `scope_sql`.
2. Changes needing approval call `approvals.request(...)` and register a `@handler('kind:sub')` that applies them.
3. Every state change calls `audit.write(...)`.
4. A page in `frontend/src/pages/`, a route in `App.tsx`, and set `ready: true` in `Shell.tsx`.
5. Tests in `backend/tests/`.
