# Troubleshooting

How to find and fix a problem in the code or the database. Work top to bottom: **find the error code → read where it is
raised → apply the fix below**. Business rules that *refuse* an action (e.g. "Not enough stock") are working as designed;
the fix is a business step, not a code change.

## 1. Find the cause in 5 minutes

| Step | Where | What you get |
|---|---|---|
| 1. Read the message on screen | the red note / toast | a sentence that says what to do |
| 2. Get the error code | browser → DevTools → Network → the red request → Response `{"error": "<code>", ...}` (on a phone: ask for a screenshot, the message is unique) | e.g. `negative_stock` |
| 3. Look the code up | [ERROR_CODES.md](ERROR_CODES.md) | HTTP status, every message, **file:line** that raises it |
| 4. Read the rule | that file:line in `backend/kz/` | the condition that failed |
| 5. Server side errors (HTTP 500 `server_error`) | `sudo journalctl -u kz-api -n 200` and `/var/log/kz/api.log` | full Python traceback with the route |
| 6. Web server | `/var/log/nginx/error.log`, `sudo nginx -t` | 502/504, TLS, permission (SELinux) |
| 7. Data consistency | `sudo -u kz /opt/kz-app/backend/.venv/bin/flask --app wsgi kz doctor` (run in `/opt/kz-app/backend`) | OK / FAIL per invariant |
| 8. What happened before | Audit trail screen (filter by ref / user), `security_events`, `approvals` + `approval_log` | who did what, with old and new values |
| 9. Background jobs | `sudo journalctl -u kz-tick -n 50`, `systemctl list-timers kz-*` | escalations, outbox sends, scheduled reports |

`GET /api/health` → `{"database":"ok","migration":"0006","status":"ok","version":"2.8.0"}`; `deploy/oci/healthcheck.sh` checks the whole stack.

## 2. Deployment and server

| Symptom | Cause | Fix |
|---|---|---|
| Browser shows **502 Bad Gateway** | Gunicorn not running / crashed on start | `sudo systemctl status kz-api`; `journalctl -u kz-api -n 100`. Common: wrong `DB_PASSWORD`, missing `/etc/kz/kz.env`, Python package missing (`.venv/bin/pip install -r requirements.txt`) |
| 502 only on SELinux hosts, API fine on `curl 127.0.0.1:5000` | Nginx not allowed to connect | `sudo setsebool -P httpd_can_network_connect 1` |
| 403 / blank page for the app itself | Nginx cannot read `frontend/dist` | `sudo restorecon -R /opt/kz-app/frontend/dist`; `chmod -R o+rX /opt/kz-app/frontend/dist` |
| `/api/health` → `"database": "error"` | DB unreachable: NSG/security list missing port 3306 from the VM subnet, wrong `DB_HOST`, TLS required | `mysql -h $DB_HOST -u kz_app -p` from the VM; for HeatWave TLS set `DB_SSL_CA` |
| `alembic upgrade head` stops at 0002 with **error 1419** (*You do not have the SUPER privilege and binary logging is enabled*) | creating triggers needs `log_bin_trust_function_creators=ON` | HeatWave: DB System → Configuration → custom config with that variable ON; self-managed: `SET PERSIST log_bin_trust_function_creators = ON;` then rerun |
| `alembic` says **Can't locate revision** | database is newer than the code (old release deployed) | deploy the matching release, or see MIGRATIONS.md → "Version mismatch" |
| Users are **signed out immediately** after signing in / OTP "expired" at once | server clock or time zone wrong | `timedatectl` (set `Asia/Kolkata`, NTP on). The app aligns MySQL's session time zone to the app server, so fix the VM clock |
| Cookies not kept on plain http | `COOKIE_SECURE=1` needs HTTPS | finish certbot / load balancer TLS; only for a test server set `COOKIE_SECURE=0` |
| certbot fails | DNS not pointing at the VM or port 80 closed in the OCI security list | fix DNS / ingress rule for 80 and 443, rerun `install.sh --domain …` |
| No SMS / e-mails arrive; `outbox` rows stay `Queued` or become `Failed` | provider not configured or wrong key | fill `SMTP_*`, `SMS_API_*` in `/etc/kz/kz.env`, restart `kz-api`; `flask kz send-outbox` shows `sent=… failed=…`; `last_error` column says why |
| HTTP **429 Too Many Requests** on sign-in | Nginx rate limit on password / code endpoints (10 per minute per IP, burst 10) — a whole office behind one IP signing in at once can hit it | wait a minute, or raise `rate=` of `kz_auth` in /etc/nginx/conf.d/kz.conf and `sudo systemctl reload nginx` |
| Phones keep showing an old version | service worker cached by a proxy | Nginx must send `Cache-Control: no-cache` for `/sw.js` and `/index.html` (deploy/oci/nginx/kz.conf); users tap "Reload now" on the banner |

## 3. Sign-in and access

| Code / symptom | Meaning | Fix |
|---|---|---|
| `bad_credentials` | wrong password (counts toward lock-out) | Forgot password, or admin **Unlock** + `flask kz set-password EMP_ID` |
| `locked` / `account_inactive` | too many failures, or status not Active | Users → open user → Unlock (reason) / set status Active |
| `captcha_required` | 3+ failures: answer the question shown | — |
| `bad_otp`, `challenge_expired` | wrong or old one-time code (5 min, 3 tries) | sign in again for a new code; check the mobile number on the user |
| `outside_hours` | Settings → security `hours` restricts sign-in time | change the policy or sign in during hours |
| `access_expired` | today is outside the user's *Access from / until* | Users → extend the dates |
| `reauth_required` (HTTP 428) | high-risk action needs the password again | the app opens the password dialog automatically |
| `forbidden` (403) | the user's roles lack that action on that screen | Setup → Roles & permissions (approval needed); check the right role is assigned in Users |
| `out_of_scope` | the location is not in the user's locations | Users → Locations |
| `weak_password`, `password_reused` | policy: length, upper/lower/digit/special, last 5 not reused | choose another password |

## 4. Billing (POS)

| Code | Meaning | Fix |
|---|---|---|
| `shift_required` | cash bill without an open shift | the screen shows *Open your cash shift* — count the float |
| `student_required` | every counter bill needs the admission number | scan the ID card or search the student |
| `student_blocked` | student is not Active (Transferred / Left) in the ERP | correct in the ERP, then Settings → ERP import |
| `unpriced_item` | item has price 0 | Items → Price change (approval) |
| `negative_stock` | not enough free stock at this location | transfer stock in, or enter a stock-override reason (supervisor approval), or bill with *Deliver later* |
| `payment_mismatch` | payments ≠ bill total | adjust the split |
| `payment_ref_required` | UPI / card / bank needs the transaction reference | type the UTR / approval code |
| `cash_limit` | more than ₹1,99,999 cash in one bill (Income-tax s.269ST) | take the rest by UPI / card / bank |
| `credit_not_allowed` | students cannot buy on credit | use a sponsorship voucher or a payment link |
| 202 + "Sent for approval" | discount above the cashier's limit or stock override | approver opens Approvals → the held bill posts on approval |
| `voucher_check_failed` | sponsored issue exceeds the voucher entitlement, value or validity | issue within the voucher, or a new voucher |
| `cancel_window_passed`, `return_window_passed` | outside Settings → controls `cancelHours` / `returnDays` | Director can approve a late return |
| `sponsored_return_mode` | sponsored issues return only to the MSEA voucher | choose refund by "Back to MSEA voucher" |
| Discount appears without being typed | a **discount scheme** matches the item | Setup → Discount schemes (shown on the cart line) |

## 5. Stock, purchasing, accounts

| Code | Meaning | Fix |
|---|---|---|
| `evidence_required` | approver must see the bill / photo first (exp, pay, adj) | attach the file on the approval or the document |
| `stock_remaining` | closing a location that still holds stock | Transfers out first |
| `same_branch`, `cross_company` | transfer to itself / to another company's branch | stock moves only within one company |
| `bill_mismatch` | supplier total ≠ received value + GST (± ₹1) | check the GRN quantities/rates or the supplier invoice |
| `duplicate_bill` | that supplier invoice number is already booked | it is a duplicate — do not book twice |
| `overpayment` | payment above the supplier's outstanding | pay only what is due |
| `books_locked` | date is on/before the company's lock date | post in the open period, or the Director moves the lock date |
| `unbalanced_voucher` | journal debit ≠ credit | fix the lines |
| `control_account` | sub-ledger account in a manual journal | use the proper document (receipt, bill, adjustment) |
| `reverse_via_document`, `already_reversed` | reverse sales/purchase vouchers through their document; already reversed | return / debit note instead |
| `insufficient_cash` | cash account at that branch is lower than the amount | check the shift / handover; deposit what is there |

## 5a. Stock audits

| Code / symptom | Meaning | Fix |
|---|---|---|
| `not_auditor` | only the auditors named on the audit can enter counts or submit | add the person as auditor on a new audit, or let a named auditor count |
| `audit_locked` | counts are locked after submission | supervisor or reviewer *Return for recount* |
| `count_incomplete` | some lines have no physical quantity | enter 0 for items that are not on the shelf (filter *Not counted*) |
| `evidence_required` (on submit) | damaged / expired / missing lines, or variance ≥ `stock_audit.photo_above`, need a photo | *+ Photo* on each listed line (the error lists the item codes) |
| `segregation` | an auditor tried to verify / review / approve, or the supervisor was also chosen as auditor | another person must do it |
| `not_supervisor` | verification is for the named supervisor (or a manager of rank ≥ 2) | — |
| `bad_auditor` / `bad_supervisor` | the chosen person lacks the stock-audit permission or that location | Setup → Roles (`saudit` edit / verify) and Users → Locations |
| `negative_stock` on *Post stock adjustment* | stock was sold or moved after it was counted, so the shortage cannot be applied | Reopen (Director), recount that item, approve again |
| `too_many_lines` | more than `stock_audit.max_lines` items | split by category or bin prefix |
| Audit went to the Director | shortage, excess or damaged value ≥ `stock_audit.high_value` (default ₹25,000) | expected escalation; Director approves in Approvals |
| No reminders / overdue alerts | `kz-tick.timer` not running | `systemctl enable --now kz-tick.timer` |
| Camera button missing | browser without BarcodeDetector (iPhone Safari, Firefox) | use a Bluetooth / USB scanner (types into the box) or type codes |

## 6. Online store, payments, MSEA

| Code / symptom | Meaning | Fix |
|---|---|---|
| `parent_not_found` | admission no. + mobile do not match the ERP record | parent uses the school-registered mobile; correct it in the ERP |
| `over_limit` | per-student limit in Settings → online `limits` reached | expected; staff can sell at the counter |
| `simulated_mode` | no Razorpay keys for that company: test payments only | add `RAZORPAY_KEY_ID_KG/ZE`, `…_SECRET_…`, `…_WEBHOOK_SECRET_…`; Settings → Payments → *Request switch to Live* |
| `bad_payment_signature` | checkout signature did not verify (tampering or wrong secret) | the webhook confirms genuine payments within minutes; check key secret |
| Paid but order still *Order Placed* | webhook not reaching the server | Razorpay dashboard → Webhooks → URL `https://<domain>/webhooks/razorpay/KG` (and `/ZE`), same secret as `RAZORPAY_WEBHOOK_SECRET_<CO>`; `rzp_webhook_events.error` shows rejections |
| `wrong_step_role` | the user's role cannot do that fulfilment step | roles: picker / QC / packer / dispatcher / branch collection (services/online.py `STEP_ROLES`) |
| Collection code rejected | wrong code, or the code of another order | the 6-digit code is in the parent's SMS for that order number; check the order number matches |
| Payment link paid but no receipt | webhook `payment_link.paid` not delivered | enable that event on the webhook; receipts post idempotently when it arrives |
| `not_sponsored` | student category is not a sponsored one | correct the category in the ERP (Settings → student categories marks which are sponsored) |

## 7. Approvals, scheduler, reports

| Symptom | Cause | Fix |
|---|---|---|
| Approval cannot be decided (`cannot_decide`) | requester cannot approve their own request, or rank too low | another user with the required role |
| Approval never escalates | `kz-tick.timer` not running | `systemctl enable --now kz-tick.timer`; `journalctl -u kz-tick` |
| Scheduled report not received | owner lost report permission, SMTP not set, or time not reached | Reports → Scheduled e-mails → *Last run* column shows the result; *Send now* to test |
| Report shows *Does not balance* | a voucher was posted without its pair (bug) or manual SQL | `flask kz doctor` → fix per DATABASE.md §6 |

## 8. Code errors (HTTP 500 `server_error`)

1. Reproduce and copy the traceback from `/var/log/kz/api.log` (`Unhandled error on POST /api/...`).
2. Nothing was saved: the request's transaction was rolled back.
3. Write a failing test in `backend/tests/` that repeats the request (see existing tests for helpers `as_user`, `ok`).
4. Fix, run `python -m pytest -q` and `ruff check .`, deploy with `deploy/oci/update.sh` (it backs up the DB and rolls back
   the code if the health check fails).
5. If data was affected by an earlier bug: DATABASE.md §6 (repairs), never edit posted rows by hand.

## 9. Frontend problems

| Symptom | Where to look |
|---|---|
| "This screen hit an error" panel | a React render error: open DevTools → Console; the component stack names the file in `frontend/src/pages`. The rest of the app keeps working. |
| Screen shows *Loading…* forever | Network tab: a request pending or 4xx; offline banner shown when the device has no connection |
| Layout broken on a phone | `frontend/src/styles.css` media queries (860 / 720 / 600 px); tables need `data-label` (use `DataTable`) |
| Print shows the whole page | the printable block needs class `print-area` (invoices, labels) |

## Finance & tax and exports (release 2.3)

| Symptom | Cause and fix |
|---|---|
| Excel / PDF export works but `X-Rendered-By: python` | The Node.js service is not answering. `systemctl status kz-docs`, `journalctl -u kz-docs -n 50`; check `DOCS_URL` / `DOCS_SECRET` in `/etc/kz/kz.env` (same secret for both services), then `systemctl restart kz-docs kz-api`. Exports still work meanwhile. |
| `kz-docs` answers 401 in its log | `DOCS_SECRET` differs between the two services: both read `/etc/kz/kz.env`; restart both after changing it. |
| `evidence_required` when submitting or approving a voucher | Expense, purchase, GST-adjustment and asset vouchers need the bill attached (Setup → Controls sets the amounts). Attach it on the voucher page. |
| `books_locked` or a voucher goes to the CFO unexpectedly | The date is on or before the books-closed date, in a month whose GSTR-3B is filed, or in a closed year. Use a current date or let the CFO / Director approve it. |
| `Party GSTIN … is not valid: Check digit should be X` | The party's GSTIN fails the GSTN checksum. Correct it in the supplier / customer master. |
| `negative_stock` when approving a stock audit | Stock was sold or moved after the count; mark that item *Recount* on the impact review and approve the rest. |
| `duplicate_bill` / `possible_duplicate` | The same supplier bill number is already booked, or a bill with the same supplier and amount exists within the duplicate window. Tick *Not a duplicate* only for the second case. |
| `invalid_body` / `invalid_input` | The request was not a JSON object or a field had the wrong type (release 2.3.1 answers 400 instead of a server error). The log keeps the details as a warning: `grep "Malformed input" logs/api.log`. |
| `value_out_of_range` | An amount, quantity or text is larger than the database can hold. Correct the value. |
| `duplicate_payment_ref` | The UPI / card / bank reference is already on another bill. Check the payment; one payment cannot settle two bills. |
| `negative_amount`, `future_date` | Journals take positive amounts on the correct side, and Accounts journals cannot be dated in the future. Post-dated Finance vouchers go to the CFO / Director. |
| `bad_quantity` with “no fractions” | Quantities are whole numbers; 2.5 is refused rather than cut to 2. |
| Super Admin cannot approve a request | It is their own request (maker-checker) or the location / company is not assigned to them. Every other right is granted. |
| `year_not_ready` when closing a year | Something on the Year-end checklist marked ✕: the year has not ended, an earlier year is open, or a voucher of the year is still draft / pending. Fix it and close again. |
| `review_warnings` when closing a year | Items marked ! are not done (GST / TDS returns, depreciation, bank, stock audits, cash). Finish them, or tick *I have reviewed these* to close anyway — they are recorded with the closing. |
| `later_year_closed` when reopening | Reopen the latest closed year first, then the earlier one. |
| P&L of a closed year shows its profit but the income accounts are zero in the ledger | Correct: the *Year closing* journal on 31 March moves income and expenses to capital. P&L and budget reports leave that journal out; ledgers and the balance sheet include it. |
| Print / PDF / Excel button missing on Reports | The role lacks *Reports → print*, *pdf* or *export* (Setup → Roles). The API refuses the format with 403 `forbidden` too. |
| Cost or margin columns missing | Shown only with *Reports → cost*. Stock ageing then shows quantities instead of value. |
| A financial report shows only one company | Financial statements are per company; choose the company (default: your first company). |
| `unknown_report` / `unknown_item` | The report key or item code does not exist (or the item is in a company you cannot see). |
| Printed report has the screen menus | Use the report's own **Print** button (not the browser's menu), which prints only the report sheet. |
| Evidence shows “Evidence not found” for a file that exists | Since 2.3.1 a file is visible only to people who may read its document (company, location, screen) and to approvers. |

