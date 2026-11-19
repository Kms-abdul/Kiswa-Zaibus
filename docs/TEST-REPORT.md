# Test report — package 1.0.0

Tested on a Linux build server with MariaDB 10.11 (MySQL-compatible), Python 3.13 with the pinned packages, and Node 22.
The Windows-only steps (installers, services, IIS, firewall, scheduled task) were syntax-checked with PowerShell 7.4.
Run them for the first time on a test server before production. `scripts\run-tests.ps1` repeats the automated checks on Windows.

| Area | Result |
|---|---|
| Schema: 81 tables, applied twice (idempotent); audit_log UPDATE/DELETE refused by triggers | Pass |
| Seed: 2 companies, 5 locations (Uppal closed, stock at Myjo), 10 branches, 37 roles, 2,954 permission grants, 17 users, 427 items, opening stock journal balanced (KG ₹10,04,869.69) | Pass |
| Seed refuses to run on a database that already has data | Pass |
| API automated tests (pytest) | 29 / 29 pass |
| Gateway: Razorpay signature verification (valid, tampered body, wrong secret, missing, malformed) | Pass |
| Clean-room install from the package with a data-only database account; browser run-through | Pass |
| Browser: sign-in → OTP → forced password change → dashboard; new user by Super Admin → re-authentication → Director approval → user created | Pass |
| Browser: items, audit trail integrity check, phone width 390 px (no horizontal scroll), dark mode | Pass |
| Webhook through the gateway: valid signature captured; forged body with reused event id rejected | Pass |
| Outbox: email sent through SMTP, UTF-8 intact, OTP text blanked after sending, retries with back-off | Pass |
| PowerShell scripts parse without errors; helpers (secrets, .env round-trip, MySQL option file) exercised | Pass |

API tests cover: OTP and password-change flow, password policy (name, history), HttpOnly/SameSite cookie, lockout after
5 failures with CAPTCHA after 3, unlock through forgot-password OTP, account enumeration protection, single session,
idle timeout, logout, suspended and expired accounts, sign-in by mobile number, JSON-only mutations, unauthorised access
logging, read-only auditor, branch scope, cost visibility, closed Uppal location, maker-checker for users (own request,
rank, re-authentication, duplicate request, validation), Super-Admin-only grants, cancel/reject/return with reason,
self-change refused, ISBN-10/13 rules and duplicates, price change approval, audit hash chain, internal-key protection,
scheduled resignation, Razorpay capture/duplicate/invalid signature/Live gating, dashboard, alerts, roles.
