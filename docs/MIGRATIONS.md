# Database migrations (Alembic)

Every schema change is a numbered, reviewed Alembic revision in `backend/migrations/versions/`. The database records the
revision it is at in `alembic_version`; `/api/health` shows it (`"migration": "0006"`).

| Revision | Release | What it does |
|---|---|---|
| `0001_initial_schema` | 2.0 | all tables of release 2.0 |
| `0002_audit_guards` | 2.0 | triggers that make `audit_log` append-only; the `audit_head` chain row |
| `0003_setup_and_extras` | 2.1 | `discount_schemes`, `custom_fields`, `custom_values`, `report_schedules`; `online_order_lines.disc_pct`; payment-link columns; outbox attachments; default `pos` receipt settings |
| `0004_stock_audit` | 2.2 | `stock_audits`, `stock_audit_lines`, `stock_audit_events`; `items.uom`; `stock_adjustments.audit_no` / `approved_by`; permission grants for screen `saudit`; default `stock_audit` settings |
| `0005_finance_and_stock_audit_approval` | 2.3 | finance & tax tables; stock-audit approval and decisions |
| `0006_year_end_closing` | 2.6 | `fin_years`: closing journal, profit, reason, checklist snapshot, reopen details |

Migrations run with the schema-owner account: `MIGRATE_DB_USER` / `MIGRATE_DB_PASSWORD` (from `/etc/kz/kz.env`);
`migrations/env.py` switches to it automatically. The runtime account `kz_app` cannot change the schema.

## Everyday commands (from `backend/`, venv active)

```bash
alembic current                 # revision of this database
alembic history --verbose       # all revisions
alembic upgrade head            # apply everything pending (install.sh / update.sh do this)
alembic check                   # models and database agree? ("No new upgrade operations detected.")
alembic upgrade head --sql      # print the SQL without running it (for review by a DBA)
alembic downgrade -1            # undo the last revision (test databases; production: restore the backup instead)
```

## Making a schema change

1. Change the model in `kz/models/…` (new class → also import it in `kz/models/__init__.py`).
2. Generate against a database that is **at head**:
   `alembic revision --autogenerate -m "short description" --rev-id 0004`
3. Review the file — autogenerate is a draft:
   * rename columns by hand (`op.alter_column(..., new_column_name=...)`), otherwise it drops and re-adds (data loss);
   * add data steps (`op.execute("UPDATE …")`) for new NOT NULL columns, with a `server_default`;
   * MySQL refuses to drop an index that a foreign key uses: in `downgrade()` drop the table, not the index first;
   * keep the `DT6` / `LONG_TEXT` aliases used in earlier revisions for DATETIME(6) and MEDIUMTEXT.
4. `alembic upgrade head`, then `alembic downgrade -1` and `alembic upgrade head` again on a test database (round trip).
5. `alembic check` must say nothing is pending; `python -m pytest -q` builds a fresh test database through all revisions.
6. `python scripts/gen_docs.py` (SCHEMA.md) and add the revision to the table above.
7. Never edit a revision that has already run on production; write a new one.

## Version mismatch

| Message | Meaning | Fix |
|---|---|---|
| `Can't locate revision identified by '0004'` | the database is newer than the deployed code | deploy the newer release (or restore the pre-update backup) |
| health shows an older revision than `alembic heads` | `upgrade head` was not run | `cd /opt/kz-app/backend && set -a && . /etc/kz/kz.env && set +a && .venv/bin/alembic upgrade head` |
| `Multiple head revisions` | two developers created revisions from the same parent | `alembic merge -m "merge" <rev1> <rev2>` and review |
| Error 1419 creating triggers (0002) | binary logging needs `log_bin_trust_function_creators=ON` | see DEPLOYMENT_OCI.md §2 |
| A migration failed half-way | MySQL DDL is not transactional | restore the backup taken by `update.sh` (`/var/lib/kz/pre-update-*.sql.gz`), fix the revision, run again |
