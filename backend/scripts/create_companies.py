"""Step 3 of an empty install: create ONLY the two companies, Kiswa Garments (KG) and Zaibus Enterprises (ZE).

    python scripts\\create_companies.py            # from the backend folder, (.venv) active

Details (name, GSTIN, address, phone, e-mail, online prefix) come from seed/seed_data.json; change them afterwards on
Setup > Companies. It writes exactly:
  1. companies  - two rows: KG and ZE (state code taken from the GSTIN; books are NOT locked to any date)
  2. user_companies - links the Super Admin user to both companies (needed to use company-scoped screens)
  3. audit_log  - one entry per company in the tamper-proof audit chain
No branches, items, stock, accounts or users. Refuses to run when any company already exists.
(The app supports exactly these two company codes today; see docs for what a different client's companies would need.)
"""
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from wsgi import app  # noqa: E402  (reads backend/.env -> DB_NAME)
from kz import db  # noqa: E402
from kz.config import Config  # noqa: E402
from kz.core import audit  # noqa: E402

SEED = BACKEND / 'seed' / 'seed_data.json'


def main():
    companies = json.loads(SEED.read_text(encoding='utf-8'))['companies']
    with app.app_context():
        print(f'Database: {Config.DB_NAME} on {Config.DB_HOST}')
        if db.val('SELECT COUNT(*) FROM companies'):
            print('Refused: this database already has companies. This script is only for an empty database.')
            sys.exit(1)
        user = db.one("SELECT id, name, main_role FROM users WHERE main_role='Super Admin' ORDER BY id LIMIT 1") \
            or {'id': None, 'name': 'System (CLI)', 'main_role': 'System'}
        try:
            for co in companies.values():
                gst = co.get('gstin') or ''
                db.run('INSERT INTO companies (code, name, short_name, gstin, state_code, address, phone, email, online_prefix, active) '
                       'VALUES (:c, :n, :s, :g, :sc, :a, :p, :e, :o, 1)',
                       c=co['id'], n=co['name'], s=co['short'], g=gst or None, sc=gst[:2] if len(gst) >= 2 else '36',
                       a=co.get('addr'), p=co.get('phone'), e=co.get('email'), o=co.get('onlPrefix'))
                audit.write('Company created (command line)', co['id'], new=co['name'], category='setup', status='Done', company=co['id'], user=user)
                print(f"  created {co['id']}  {co['name']}  GSTIN {gst}")
                if user['id']:   # company access is read from user_companies (Super Admin has every screen, but not every company automatically)
                    db.run('INSERT IGNORE INTO user_companies (user_id, company) VALUES (:u, :c)', u=user['id'], c=co['id'])
            if user['id']:
                print(f"  linked {user['name']} to both companies")
            db.finish(True)
        except Exception:
            db.finish(False)
            raise
    print('Done. Edit the details on Setup > Companies after signing in.')


if __name__ == '__main__':
    main()
