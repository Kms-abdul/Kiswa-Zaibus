# Kiswa-Zaibus Operations — v2.8.0

Point of sale, stock, purchasing, cash custody, accounts, MSEA sponsorship, a parent online store, payment links, barcode
labels, scheduled reports, **stock audits** (count on a phone, verify, review, CFO / Director impact review with item-wise
approval and automatic posting), **finance & tax** (21 voucher types, GST and TDS compliance, 73 reports, banking, fixed assets,
compliance calendar), a **report library** (48 reports with Print, PDF and Excel), setup of all master data, and maker-checker approvals for Kiswa Garments (uniforms) and Zaibus
Enterprises (books). One responsive PWA for desktop and Android phones.

**Documentation: [docs/README.md](docs/README.md)** — user workflow, developer guide, troubleshooting, database, API, OCI deployment.

| Part | Stack | Folder |
|---|---|---|
| API | Python 3.12 · Flask 3 · SQLAlchemy 2 · MySQL 8 / OCI HeatWave | `backend/` |
| Schema history | Alembic (`backend/migrations/versions`) | `backend/migrations/` |
| Web + mobile app | React 18 · TypeScript · Vite · PWA (installable on Android) | `frontend/` |
| Document service | Node.js 20 · TypeScript · Express · exceljs · pdfkit (Excel / PDF exports, loopback only) | `docs-service/` |
| Deployment | Oracle Linux 9 on OCI: Nginx + Gunicorn + systemd | `deploy/oci/` |

## What's new in 2.8.0 (Oracle Redwood-style look)
- New look on every screen: dark patterned header with breadcrumb (Head Office › company › location › screen), light navigation drawer with line icons and a red "you are here" marker, warm off-white work area, dark action buttons, Source Sans 3 / Source Code Pro fonts (bundled, no external font host).
- Header: quick search, Online / Offline indicator, alerts bell with count, help.
- Dashboard: Overview / Branch comparison / Alerts tabs; quick-action tiles for the user's role (with waiting counts); KPI cards: sales today (14-day sparkline), sales this month vs the same days last month, gross profit month to date, collections today, receivables, payables, cash in hand, bank, stock at cost. Each card shows only if the user may see that figure.
- `GET /api/dashboard` adds `prev_mtd`, `by_branch`, `collections_today` and `gp_mtd` (cost permission). No database change; migration stays 0006.
- **Test data set** (`backend/seed/test_data.json.gz`): the complete sample data used for testing 2.8 (two companies, six locations, 17 users, items, students, suppliers, POs and GRNs, transfers, bills, MSEA vouchers, online orders, stock audits, cash shifts, journals, finance vouchers, approvals, audit trail; 9,853 rows, `flask kz doctor` all OK). Load it into an **empty test database** instead of the go-live seed:
  ```
  alembic upgrade head
  flask --app wsgi kz seed --test-data --temp-password 'Test#2026kz'
  ```
  Every user signs in with that temporary password and must change it. Sign-in sessions, devices, queued messages and evidence files are not included. It refuses to run when `KZ_ENV=production`: on a test server set `KZ_ENV=test` (or add `--force` for a separate test database, never the live one). Without an SMS provider, also set `OTP_DEMO=1` on the test server so the sign-in code is shown on screen. `flask kz export-test-data` writes a new snapshot from the current database.

## What's new in 2.7.0 (Super Admin has every right)
* The **Super Admin** role now has every action on every screen — billing, stock, purchasing, cash, online, MSEA, accounts,
  finance & tax, stock audits, reports (including financial statements and cost columns), approvals, audit, setup and control —
  and the highest approval and transaction limits of any role. It can close and reopen periods and financial years and approve
  stock audits.
* Two rules still apply: nobody approves their own request (maker-checker), and the user sees only the companies and locations
  assigned to them (the seeded Super Admin has both companies and all locations).
* *Setup → Roles* shows the Super Admin with every right ticked and its effective limits. No database change.

## What's new in 2.6.0 (year-end closing)
* **Finance & tax → Year-end** closes a financial year in three steps:
  1. **Before closing** — a checklist. Must be fixed first: the year has ended, earlier years are closed, no draft or pending
     vouchers in the year, the books balance. To review (accepted with a tick): GSTR-3B / GSTR-1 filed for every month, TDS
     returns filed and TDS deposited, depreciation run for every month, bank lines reconciled, stock audits finished, no cash or
     bank account overdrawn. Each item opens the screen that fixes it.
  2. **Profit to transfer** — income, expenses and the profit or loss, shared between capital / reserve accounts (one
     proprietor's capital, or partners by their ratio); the closing entry and next year's opening position are shown.
  3. **Close** — CFO / Director with password re-entry: posts one *Year closing* journal on the last day of the year (every income
     and expense account to zero, profit to capital), locks the books to the year end, opens the next year and keeps a record of
     the checklist and figures.
* **Reports stay right after closing:** P&L, budgets and comparisons still show what the year earned; the balance sheet shows the
  profit inside capital; the trial balance of the year shows it before closing; the next year starts with income and expenses at zero.
* **Reopen** (Director / Owner, with a reason and password): reverses the closing journal, unlocks the year and alerts management;
  the latest closed year is reopened first. Reopening a period from Setup into closed years reverses their closing journals too.
* Year-end pack links (trial balance, P&L, balance sheet, cash flow for the year → Print / PDF / Excel). Migration `0006`.

## What's new in 2.5.0 (easier to use)
* **Quick search** — the search box in the top bar (or <kbd>Ctrl</kbd>+<kbd>K</kbd> / <kbd>/</kbd> anywhere) finds screens, common
  actions, bills (number or admission no.), students and items; arrow keys and Enter open the result. <kbd>?</kbd> lists all shortcuts.
* **Dashboard → Your work** — what is waiting for *you*, each with a count and a link: approvals you can decide, audits to count or
  verify, transfers to receive, POs to receive goods against, cash handovers to accept, online orders at your step, bills with items
  still to hand over, draft vouchers, items to reorder, and a warning if yesterday's shift is still open. Quick-action buttons for
  your role; the 30-day sales chart shows one column per day; alerts are short, readable and link to their screen.
* **Counter billing** — keyboard first: <kbd>F2</kbd> student, <kbd>F3</kbd> item, <kbd>F8</kbd> cash received,
  <kbd>F9</kbd> / <kbd>Ctrl</kbd>+<kbd>Enter</kbd> bill; ↑ ↓ Enter in the student and item lists; the cursor moves to the next
  step by itself (student → item → next customer). Quick cash buttons (Exact, ₹500, ₹1,000 …) with *Give back* / *Short by*;
  the Bill button says what is missing; Clear asks first; closing the tab with a bill in progress asks first.
* **Everywhere** — dates read “10 Nov 2026, 8:37 pm”, negative amounts “−₹5,002”, “1 bill / 2 bills”; tabs, filters and the open
  record stay in the address, so Back, refresh and shared links return to the same view; empty lists say what to do next;
  messages can be dismissed and errors stay longer; server outages show a plain sentence instead of a proxy page.
* **Phones** — list cards are about half as tall (two fields per line, an arrow on rows that open); the location name no longer
  crowds the top bar.
* **Fixes** — dates no longer default to yesterday between midnight and 5:30 am (the app used UTC); the dashboard's low-stock tile
  now opens the filtered stock list; Setup → Companies no longer shows an empty button; Setup tabs wrap instead of hiding.

## What's new in 2.4.0 (reports)
* **Report library** (*Reports*): 48 reports in seven groups, each with **Print, PDF, Excel and CSV**, drill-down from summary
  rows, and filters for company, period, location and the report's own fields.
  * **Sales** — sales register; daily sales and collection; cashier-wise; payment-mode collection; returns / credit notes;
    discount register; school and class-wise; category-wise; item-wise; hourly; pending deliveries.
  * **Stock** — valuation; summary by location; movement (opening, in, out, closing); item stock ledger; low stock / reorder
    with a suggested order; slow and non-moving; ageing; negative and over-reserved stock; transfer register; in transit;
    adjustment register.
  * **Purchasing** — PO register; pending POs; GRN register; supplier-wise purchases and dues; purchase returns / debit notes;
    supplier price comparison from quotes.
  * **Cash** — cashier shift closing (short / excess); cash handover register; owner instruction register.
  * **MSEA & online** — MSEA voucher utilisation; sponsored student issues; online orders by status; payment links and refunds.
  * **Controls** — approvals register with turnaround hours; audit trail; user sign-in activity; price change history.
  * **Financial** — trial balance, profit and loss, balance sheet, day book, account ledger, cash book, receivable and payable
    ageing, GST liability (from the finance books).
* **Print everywhere** — the report library, the 73 finance reports and the 10 stock-audit reports print only the report:
  organisation, title, company, period, who printed it and when, the column header repeated on every page, totals and a footer;
  wide reports print on A4 landscape. Stock-audit reports now also download as **PDF**.
* **Rights and logging** — *Reports → print / pdf / export* decide which buttons a role sees and the API enforces them; cost and
  margin columns need *Reports → cost*, financial statements need *Reports → financial figures*; rows stay inside the user's
  companies and locations. Every print and export is written to the access log.
* The old `/api/reports/*` endpoints remain for scheduled e-mails.

## What's new in 2.3.1 (hardening)
Fixes from an aggressive test round (every route × every role with hostile input, access-control attacks, double-click races,
a 400-step random walk):
* **GST payment** — two simultaneous *Pay GST* clicks could create two payments for one month; now the second waits and is refused.
  Depreciation runs and GST filings are protected the same way.
* **Evidence files** — a file can be listed, downloaded, verified or withdrawn only by people who may read its document (company,
  location and screen rights; approvers may read the bills they approve). Files are served with the type of their extension.
* **Clean errors** — malformed requests (a list instead of an object, text instead of a number, missing fields, values too large)
  get a clear 400 message instead of “Something went wrong”, including the public parent-store sign-in.
* **Refused instead of silently changed** — negative journal amounts, journals dated in the future (Accounts) or outside an open
  financial year (Finance; post-dated finance vouchers now need CFO / Director approval), fractional quantities (counts, sales,
  returns, receipts, adjustments), and a UPI / card / bank reference already used on another bill.
* **Finance Setup lists** show only the user's companies (PANs, salaries, banks); payroll, payment proposals and finance
  settings no longer act on an empty or malformed request.

## What's new in 2.3.0
* **Finance & tax module** — 21 voucher types with GST (CGST / SGST / IGST, reverse charge, blocked credit) and TDS (thresholds,
  lower-deduction certificates, no-PAN rate) worked out automatically; maker → checker → approver by rules and limits; reversals
  instead of edits; closed / filed period protection; GST set-off (rule 88A), GSTR-1 / 3B / 9 / 9C with portal JSON, e-invoice and
  e-way bill numbers, GSTR-2B / 2A reconciliation; TDS challans, Form 138 / 140 / 144 / 143 returns and Form 130 / 131 certificates
  (Income-tax Act 2025 numbering); 73 reports with drill-down and Excel / PDF; bank import, auto-matching and BRS; cheques;
  payment proposals; fixed assets with depreciation; compliance calendar; payroll vouchers; recurring journals.
* **Stock audits** — the CFO / Director approves on an impact review (approve all, approve some items, return for recount,
  reject); approved items post the stock adjustment and journal automatically with configurable ledgers; a permanent decision
  record; damaged / expired quantities; the internal Auditor can count; a *Next* bar shows whose step it is.
* **Node.js document service** (`docs-service/`, systemd `kz-docs`) renders Excel and PDF exports; Python renders them if it is down.
* Migration `0005` adds 27 tables and the finance reference data; existing data is untouched.

## Run locally
```bash
# database: create kiswa_zaibus + accounts with deploy/oci/mysql-setup.sql
cd backend && python3.12 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
cp .env.example .env            # set DB_*, SECRET_KEY; KZ_ENV=development COOKIE_SECURE=0 OTP_DEMO=1 for local work
alembic upgrade head
flask --app wsgi kz seed --demo-students --temp-password 'Temp#2026x'
flask --app wsgi run            # API on :5000
cd ../frontend && npm ci && npm run dev    # app on :5173 (proxies /api); parent store at /shop
cd ../docs-service && npm ci && npm run dev   # optional: Excel / PDF renderer on :8095 (Flask falls back to Python without it)
```
Tests: `cd backend && python -m pytest -q` (92 tests, including every report × every format × role, a route sweep with hostile input; every flow checks books balance and stock = stock ledger);
`cd docs-service && npm test` (5 tests).
Health of a running system: `flask --app wsgi kz doctor`.

## Deploy on OCI
1. Create the database and accounts: `mysql -h <host> -u admin -p < deploy/oci/mysql-setup.sql` (read the note about `log_bin_trust_function_creators`).
2. On the Oracle Linux 9 instance, from the unpacked folder: `sudo bash deploy/oci/install.sh --domain ops.example.in --email it@example.in`
   (first run creates `/etc/kz/kz.env`; fill it in and run again with `--seed` once).
3. Later releases: `sudo bash deploy/oci/update.sh` (backs up the DB, migrates, rolls code back on failure).
4. Nightly backups: `deploy/oci/backup.sh` via `kz-backup.timer` (set `/etc/kz/backup.env` for Object Storage).

## Change the database schema
Edit the models in `backend/kz/models/`, then `alembic revision --autogenerate -m "what changed"`, review the file, `alembic upgrade head`, and commit it. Never edit a migration that has already run in production.
