"""Step 5 of an empty install: the chart of accounts for each company, plus the finance / tax reference data.

    python scripts\\create_chart_of_accounts.py        # from the backend folder, (.venv) active; needs the companies (step 3)

It does what the full seed does for accounts, and nothing else:
  1. accounts     - the standard chart (34 accounts: cash, bank, receivables, inventory, GST, payables, sales, cost of goods, expenses ...)
                    for EVERY company, with the system / control accounts flagged. Bills, receipts, stock and GST post to these codes.
  2. finance & tax reference data (services/fin_bootstrap.py, safe to run again): finance accounts (GST input / output, fixed assets, TDS ...),
     cost centres, TDS sections, SAC / HSN codes, asset categories, the open financial year and the previous one, GST registration and a
     main bank account per company, finance approval rules, finance settings.
  3. audit_log    - one entry
No items, stock, customers, suppliers or users. Refuses to run when accounts already exist.
Roles are not loaded yet, so the finance permission grants for them are added later: this reference data is loaded again (safely)
when the roles are created.
"""
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from wsgi import app  # noqa: E402  (reads backend/.env -> DB_NAME)
from kz import db  # noqa: E402
from kz.cli.seed import CONTROL, EXTRA_ACCOUNTS  # noqa: E402
from kz.config import Config  # noqa: E402
from kz.core import audit  # noqa: E402
from kz.services.fin_bootstrap import bootstrap  # noqa: E402
import datetime as dt  # noqa: E402

SEED = BACKEND / 'seed' / 'seed_data.json'


def main():
    coa = {c: (c, n, g) for c, n, g in json.loads(SEED.read_text(encoding='utf-8'))['coa']}
    for c, n, g in EXTRA_ACCOUNTS:
        coa.setdefault(c, (c, n, g))
    with app.app_context():
        print(f'Database: {Config.DB_NAME} on {Config.DB_HOST}')
        companies = [r['code'] for r in db.q('SELECT code FROM companies ORDER BY code')]
        if not companies:
            print('Refused: there are no companies yet. Run scripts\\create_companies.py first.')
            sys.exit(1)
        if db.val('SELECT COUNT(*) FROM accounts'):
            print('Refused: this database already has accounts. This script is only for an empty chart.')
            sys.exit(1)
        user = db.one("SELECT id, name, main_role FROM users WHERE main_role='Super Admin' ORDER BY id LIMIT 1") \
            or {'id': None, 'name': 'System (CLI)', 'main_role': 'System'}
        try:
            for co in companies:
                for code, name, grp in sorted(coa.values()):
                    db.run('INSERT INTO accounts (company, code, name, grp, is_control, active) VALUES (:co, :c, :n, :g, :ctl, 1)',
                           co=co, c=code, n=name, g=grp, ctl=1 if code in CONTROL else 0)
                print(f'  {co}: {len(coa)} accounts')
            r = bootstrap(db.session().connection(), dt.date.today())
            print(f"  finance and tax reference data loaded: {r['accounts']} finance accounts per company")
            audit.write('Chart of accounts loaded (command line)', 'accounts', new=f'{len(coa)} accounts per company, finance and tax reference data',
                        category='setup', status='Done', user=user)
            db.finish(True)
        except Exception:
            db.finish(False)
            raise
    print('Done. See Setup > Chart of accounts (choose the company at the top).')


if __name__ == '__main__':
    main()
