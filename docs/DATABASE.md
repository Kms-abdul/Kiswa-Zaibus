# Database guide

MySQL 8 (OCI MySQL HeatWave or MySQL on the VM), schema `kiswa_zaibus`, `utf8mb4_unicode_ci`. The schema is defined by the
SQLAlchemy models in `backend/kz/models/` and created only through Alembic (see [MIGRATIONS.md](MIGRATIONS.md)).
Every table and column: [SCHEMA.md](SCHEMA.md) (generated).

## 1. Conventions

| Item | Convention |
|---|---|
| Money | `DECIMAL(16,2)` rupees; unit cost `DECIMAL(12,4)`; never FLOAT |
| Prices | `items.price` is the GST-inclusive selling price; `items.cost` the moving-average cost **excluding** GST |
| Time | `DATETIME(6)` in the app server's local time (IST). Each connection sets `time_zone` to the app's UTC offset (kz/db.py) |
| JSON | small structures only: settings values, line lists on approvals, snapshots, voucher entitlements |
| Names | tables plural snake_case; constraint names follow `NAMING` in models/base.py (`pk_`, `fk_`, `ix_`, `uq_`) |
| Accounts | two users: `kz_owner` (schema owner, migrations only) and `kz_app` (SELECT/INSERT/UPDATE/DELETE only) |

## 2. Table groups

| Area | Tables |
|---|---|
| Organisation | `companies`, `regions`, `zones`, `locations`, `branches` (one per company per location), `number_series`, `settings` |
| Access | `roles`, `permissions`, `approval_limits`, `users`, `user_roles`, `user_companies`, `user_branches`, `user_zones`, `user_regions`, `sessions`, `login_challenges`, `password_history`, `user_devices`, `security_events`, `access_schedule`, `parent_challenges`, `parent_sessions` |
| Control | `approvals`, `approval_log`, `routing_rules`, `audit_log` (append-only), `audit_head`, `access_log`, `alerts`, `alert_rules`, `outbox`, `evidence` |
| Masters | `items`, `item_categories`, `suppliers`, `customers`, `students`, `schools`, `packages`, `package_lines`, `accounts` (chart of accounts), `msea_vouchers`, `discount_schemes`, `custom_fields`, `custom_values`, `report_schedules` |
| Stock | `stock_balance` (current qty/reserved per branch+item), `stock_ledger` (every movement), `transfers`(+`_lines`), `stock_adjustments`(+`_lines`, `audit_no` links a stock audit) |
| Stock audit | `stock_audits` (header, status, people, summary values), `stock_audit_lines` (system qty and cost captured at count time, physical qty, condition, batch, remarks), `stock_audit_events` (user-wise activity log) |
| Stock-audit decisions | `stock_audit_decisions` (one row per CFO / Director decision: mode, items approved / rejected / recount / held with before-after quantities, adjustment and journal numbers); `stock_audit_lines.decision`, `dmg_qty`, `recount`, `adjustment_no` |
| Finance & tax | `fin_years`, `cost_centres`, `fin_vouchers` (all 21 voucher types; `entered` = what the maker typed, `built` = the journal that posts), `fin_approval_rules`, `recurring_journals`, `budgets`, `compliance_status` |
| GST | `gst_registrations`, `hsn_codes`, `gst_challans`, `gst_filings`, `einvoices` (IRN, e-way bill), `gst_portal_rows` (GSTR-2B / 2A downloads) |
| TDS | `tds_sections`, `party_tax` (PAN, section, lower-deduction certificate, priority, related party), `employees`, `tds_entries`, `tds_challans`, `tds_returns`, `tds_certificates` |
| Bank & assets | `bank_accounts`, `bank_statement_lines` (with the matched journal line), `cheques`, `asset_categories`, `fixed_assets`, `depreciation_runs` |
| Sales | `invoices`, `invoice_lines`, `invoice_payments`, `held_bills`, `credit_notes`, `credit_note_lines` |
| Purchasing | `purchase_orders`, `purchase_order_lines`, `purchase_quotes`, `goods_receipts`, `supplier_bills`, `supplier_payments`, `debit_notes` |
| Accounts | `journals`, `journal_lines` (with `cost_centre` and `dept` from release 2.3), `receipts`, `inter_company` |
| Cash | `cash_shifts`, `cash_handovers`, `owner_instructions` |
| Online | `online_orders`, `online_order_lines`, `order_requests`, `refunds` |
| Integrations | `rzp_payments`, `rzp_refunds`, `rzp_settlements`, `rzp_webhook_events`, `rzp_exceptions`, `payment_links`, `erp_sync_runs`, `erp_sync_records`, `erp_keys` |

## 3. Chart of accounts (seeded, per company)

| Code | Account | Group | Posted by |
|---|---|---|---|
| 1100 | Cash in Hand | Asset | cash sales/receipts/refunds, handovers, deposits |
| 1105 | Cash with Outdoor Clerk (party = clerk) | Asset · sub-ledger | handover accepted / instruction executed |
| 1110 | UPI & Card Clearing | Asset | UPI / card sales; cleared by "settle" contra |
| 1115 | Razorpay Clearing | Asset | online orders, payment links; cleared by settlements |
| 1120 | Bank Account | Asset | bank receipts/payments, deposits, settlements |
| 1200 | Trade Receivables (party = customer) | Asset · sub-ledger | credit sales, receipts |
| 1210 | MS Education Academy Receivable | Asset · sub-ledger | sponsored issues |
| 1300 | Inventory (branch) | Asset · sub-ledger | GRN, sales COGS, transfers, adjustments, returns |
| 1310 | GST Input Credit | Asset | supplier bills, gateway fee GST |
| 1400 / 2400 | Inter-company receivable / payable | sub-ledger | inter-company funds |
| 2000 | Trade Payables (party = supplier) | Liability · sub-ledger | supplier bills, payments, debit notes |
| 2050 | Goods Received Not Invoiced | Liability · sub-ledger | GRN (Cr) → supplier bill (Dr) |
| 2100 / 2110 / 2120 | Output CGST / SGST / IGST | Liability | sales (Cr), returns (Dr) |
| 2200 | Customer Advances (wallet, party = customer) | Liability · sub-ledger | advances, over-payments, wallet spend |
| 3000 | Capital | Equity | transfer to owner |
| 4000 / 4010 | Sales / Sales Returns | Income | invoices / credit notes (taxable value) |
| 5000 | Cost of Goods Sold | Expense | delivered quantity × moving-average cost |
| 5100 | Freight & Purchase Variance | Expense | purchase returns rounding / freight |
| 5600 | Stock Shrinkage & Write-off | Expense | adjustments, transit damage, damaged returns |
| 5700 | Cash Short / Excess | Expense | shift close difference |
| 5850 | Payment Gateway Charges | Expense | Razorpay settlement fee |
| 5950 | Round Off | Expense | bill round-off to the rupee |
| 5300–5900 | Rent, salaries, electricity, HO, other | Expense | expenses; new heads in Setup → Chart of accounts |

Accounts marked *sub-ledger* (`ledger.CONTROL_ACCOUNTS`) can only be posted by their own documents, never by a manual journal.

## 4. Posting rules (what each document writes)

| Document | Debit | Credit | Code |
|---|---|---|---|
| Counter bill | payment accounts (cash 1100 / UPI-card 1110 / bank 1120 / wallet 2200 / credit receivable) ; 5000 COGS | 4000 taxable ; 2100+2110 (or 2120 IGST) ; 5950 round-off ; 1300 cost | services/sales.py `post_invoice` |
| MSEA sponsored issue | 1210 (customer `<CO>-MSEA`) ; 5000 | 4000 ; GST ; 1300 | same, `voucher_no` |
| Pending delivery handed over | 5000 | 1300 | sales.deliver_pending |
| Return / cancellation (credit note) | 4010 ; GST ; 1300 (or 5600 if damaged) | refund account (cash/UPI/bank/wallet/receivable) ; 5000 | sales.execute_return |
| Receipt / paid payment link | 1100/1110/1120 or 1115 (link) | 1200 against bills (oldest first) ; remainder 2200 | accounts.record_receipt |
| Expense | expense head | 1100 or 1120 | accounts.post_expense |
| Contra deposit / withdraw / settle | 1120 / 1100 / 1120 | 1100 / 1120 / 1110 | accounts.contra |
| GRN | 1300 (accepted × rate, + freight as landed cost) | 2050 | purchasing.receive |
| Supplier bill | 2050 taxable ; 1310 GST | 2000 (party supplier) | purchasing.book_bill |
| Supplier payment | 2000 | 1100 / 1120 | purchasing.post_payment |
| Purchase return (debit note) | 2000 | 1300 ; 1310 ; 5100 variance | purchasing.purchase_return |
| Transfer received | 1300 to-branch ; 5600 damaged/short | 1300 from-branch | inventory.receive_transfer |
| Stock adjustment | 5600 (loss) or 1300 (gain) | 1300 or 5600 | inventory (approval handler) |
| Stock audit posted | 5600 for shortages / 1300 for excess (variance × cost captured at count time) | 1300 / 5600 | stock_audit.post_adjustment → inventory._post_adjustment |
| Shift close difference | 5700 (short) / 1100 (excess) | 1100 / 5700 | cash._post_shift_difference |
| Handover to clerk | 1105 (party clerk) | 1100 | cash.accept_handover |
| Owner instruction executed | 1120 deposit / expense / 3000 owner | 1105 | cash.execute_instruction |
| Online order paid | 1115 | 4000 ; GST ; 5950 | online._post_online_invoice |
| Online order packed | 5000 | 1300 (warehouse) | online._pack |
| Razorpay settlement | 1120 net ; 5850 fee ; 1310 GST on fee | 1115 gross | razorpay.record_settlement |
| Inter-company funds | payer: 1400 ; receiver: 1120 | payer: 1120 ; receiver: 2400 | accounts approval `ic` |
| Reversal | swaps every line of the original | | ledger.reverse |

## 5. Health checks (run these first)

`cd /opt/kz-app/backend && sudo -u kz .venv/bin/flask --app wsgi kz doctor` prints `OK` or `FAIL` for each of the checks below.
The same SQL, if you need to look yourself:

```sql
-- every voucher balances (must return no rows)
SELECT j.company, j.no, SUM(l.debit) dr, SUM(l.credit) cr FROM journals j JOIN journal_lines l ON l.journal_id=j.id
GROUP BY j.id HAVING dr <> cr;
-- stock balance equals the movement ledger (no rows)
SELECT b.branch_id, b.item_id, b.qty, COALESCE(SUM(l.qty),0) ledger FROM stock_balance b
LEFT JOIN stock_ledger l ON l.branch_id=b.branch_id AND l.item_id=b.item_id GROUP BY b.branch_id, b.item_id HAVING b.qty <> ledger;
-- negative or over-reserved stock (no rows)
SELECT * FROM stock_balance WHERE qty < 0 OR reserved < 0 OR reserved > qty;
-- invoice payments add up (no rows)
SELECT i.no, i.total, SUM(p.amount) paid FROM invoices i JOIN invoice_payments p ON p.invoice_id=i.id GROUP BY i.id HAVING i.total <> paid;
-- inventory account vs stock value per company (small rounding only)
SELECT j.company, SUM(l.debit-l.credit) gl FROM journal_lines l JOIN journals j ON j.id=l.journal_id WHERE l.account='1300' GROUP BY j.company;
-- approvals stuck for more than 7 days
SELECT no, title, required_role, requested_at FROM approvals WHERE status='Pending' AND requested_at < NOW() - INTERVAL 7 DAY;
```
Audit chain: Audit trail → **Verify integrity**, or `GET /api/audit/verify` → `{"ok": true, "checked": n}`.

## 6. Repairs — the safe way

**Never** `UPDATE` or `DELETE` journal lines, invoices, stock ledger rows or audit rows by hand. The audit log refuses it
(triggers), and the other tables would stop agreeing with each other. Fix through the application so every correction is
itself recorded:

| Problem | Correct fix |
|---|---|
| Wrong amount / account in a manual journal, expense or contra | Accounts → open voucher → **Reverse voucher** (reason), then post the correct one |
| Wrong items or price on a bill | Bills → **Return / cancel** (credit note), then bill again |
| Stock count differs from the system | **Stock audits** (count → verify → review → approve → post), or Adjustments → *Physical count* for one or two items |
| Stock audit posted with a wrong count | Director / Super Admin **Reopen** (reason) → recount → the new posting corrects the difference |
| Goods damaged in transit | Receive the transfer with the damaged quantity (posts 5600) |
| Supplier bill booked with wrong number | Purchase return / debit note, or reverse the PI voucher then book again |
| Cash short at shift close | Close with the counted denominations + reason → approval posts 5700 |
| Payment taken at Razorpay but order not confirmed | Wait for the webhook (minutes); else Settings → Payments → reconciliation exception → resolve |
| Approval waiting on someone who left | Settings → Policies → escalation, or the Director decides it (higher rank can always approve) |
| User locked out | Users → open user → **Unlock** (reason; approval if maker-checker on) or `flask kz set-password EMP_ID` |

### When SQL is unavoidable (data loaded wrongly before go-live, or a bug)
1. Take a backup first: `sudo /opt/kz-app/deploy/oci/backup.sh`.
2. Stop the API: `sudo systemctl stop kz-api kz-tick.timer` (so nobody posts while you fix).
3. Fix in **one transaction** with the `kz_owner` account, inside `START TRANSACTION; … COMMIT;`, and re-run section 5.
4. Rebuild a broken stock balance from its ledger (the ledger is the truth):
   ```sql
   UPDATE stock_balance b SET qty = (SELECT COALESCE(SUM(l.qty),0) FROM stock_ledger l WHERE l.branch_id=b.branch_id AND l.item_id=b.item_id);
   ```
5. Release a reservation left by a crashed online order (order already Cancelled/Expired):
   ```sql
   UPDATE stock_balance b JOIN online_order_lines ol ON ol.item_id=b.item_id JOIN online_orders o ON o.id=ol.order_id AND o.warehouse_branch=b.branch_id
   SET b.reserved = GREATEST(0, b.reserved - ol.qty) WHERE o.no='KG/ONL/2627/0007' AND o.status IN ('Cancelled','Expired');
   ```
6. Record what you did: Audit has no "manual SQL" button, so write it in the change log of your ticket and add an audit
   entry through `flask shell`: `from kz.core import audit; audit.write('Manual data fix', 'ticket 123', reason='...', category='settings')`.
7. `sudo systemctl start kz-api kz-tick.timer` and run `flask kz doctor`.

## 7. Backup and restore

* Nightly `kz-backup.timer` (01:17): `mysqldump --single-transaction --routines --triggers` + `/var/lib/kz/evidence` tar,
  SHA-256 checksums, uploaded to OCI Object Storage (`/etc/kz/backup.env`), 7 days kept locally. HeatWave also keeps its own
  automatic backups (enable point-in-time recovery on the DB system).
* Restore to a new schema first and compare before touching production:
  ```bash
  mysql -h $DB_HOST -u kz_owner -p -e "CREATE DATABASE kz_restore CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
  gunzip -c kz-db-20261105-0117.sql.gz | mysql -h $DB_HOST -u kz_owner -p kz_restore
  DB_NAME=kz_restore .venv/bin/flask --app wsgi kz doctor
  ```
  Then point `DB_NAME` in `/etc/kz/kz.env` at it (or rename) and restart `kz-api`.

## 8. Time zone

The server should run in IST (`sudo timedatectl set-timezone Asia/Kolkata`). The app sets each MySQL connection's
`time_zone` to the app server's offset, so `NOW()` in SQL and `datetime.now()` in Python always agree — sessions, one-time
codes and idle time-outs depend on it. Reports and bill dates use the server's local date.
