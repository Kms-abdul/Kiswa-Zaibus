"""Production setup: everything an empty database needs before anyone can sign in and work, and nothing else.

    python -m alembic upgrade head          # 1. tables (from the backend folder, (.venv) active)
    python seed_essentials.py               # 2. this script

    python seed_essentials.py --emp-id KZ-IT-001 --name "IT Super Admin" --mobile 9xxxxxxxxx --email it@example.in

Each step is checked first and SKIPPED when it is already done, so it is safe to run again (for example after an upgrade):
  1. Super Admin      one user with every screen; a temporary password is printed once and must be changed at first sign-in
                      (only when the database has no users at all)
  2. Companies        Kiswa Garments (KG) and Zaibus Enterprises (ZE) from seed/seed_data.json; the Super Admin gets both
  3. Settings         the setting rows the screens save into (security, controls, maker-checker, ERP, online store ...);
                      existing rows are never overwritten
  4. Chart of accounts  the standard accounts per company plus the finance and tax reference data (financial years, GST,
                      TDS sections, cost centres ...)
  5. Customers        per company: MS Education Academy (voucher bills, receivable 1210) and the walk-in customer

NOT created (you add these on the screens): locations, counters, roles, users, schools, item categories, items, suppliers,
stock, students (ERP import). No test or demo data is ever written.
"""
import argparse
import datetime as dt
import json
import secrets
import string
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND))

from wsgi import app  # noqa: E402  (reads backend/.env -> DB_NAME)
from kz import db  # noqa: E402
from kz.cli.seed import CONTROL, EXTRA_ACCOUNTS  # noqa: E402
from kz.config import Config  # noqa: E402
from kz.core import audit  # noqa: E402
from kz.core.security import hash_pw  # noqa: E402

SEED = BACKEND / 'seed' / 'seed_data.json'
SUPER = 'Super Admin'
CLI = {'id': None, 'name': 'System (setup)', 'main_role': 'System'}


def _who():
    return db.one("SELECT id, name, main_role FROM users WHERE main_role=:r ORDER BY id LIMIT 1", r=SUPER) or CLI


def _head():
    """The newest migration in migrations/versions, to check the database is up to date before writing anything."""
    from alembic.config import Config as AConfig
    from alembic.script import ScriptDirectory
    return ScriptDirectory.from_config(AConfig(str(BACKEND / 'alembic.ini'))).get_current_head()


# ---------------------------------------------------------------- steps
def super_admin(a):
    if db.val('SELECT COUNT(*) FROM users'):
        return 'skipped (users already exist)', None
    temp = 'Kz#' + ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(10)) + '7'
    h = hash_pw(temp)
    db.run("INSERT IGNORE INTO roles (name, description, approval_rank, read_only, is_custom, active) "
           "VALUES (:n, 'Every screen and action; cannot approve own requests', 3, 0, 0, 1)", n=SUPER)
    db.run('INSERT INTO users (id, emp_id, name, mobile, email, department, designation, main_role, status, all_branches, '
           'pw_hash, pw_set_at, pw_must_change) VALUES (:id, :e, :n, :m, :em, :dep, :des, :r, :s, 1, :h, NOW(), 1)',
           id='u1', e=a.emp_id, n=a.name, m=a.mobile, em=a.email, dep='IT & Maintenance', des='System administration', r=SUPER, s='Active', h=h)
    db.run('INSERT INTO password_history (user_id, pw_hash) VALUES (:i, :h)', i='u1', h=h)
    audit.write('Super Admin created (setup)', a.name, new=a.emp_id, category='security', user=CLI)
    return f'created {a.emp_id} ({a.name})', temp


def companies():
    seed = json.loads(SEED.read_text(encoding='utf-8'))['companies']
    have = {r['code'] for r in db.q('SELECT code FROM companies')}
    user, made = _who(), []
    for co in seed.values():
        if co['id'] in have:
            continue
        gst = co.get('gstin') or ''
        db.run('INSERT INTO companies (code, name, short_name, gstin, state_code, address, phone, email, online_prefix, active) '
               'VALUES (:c, :n, :s, :g, :sc, :a, :p, :e, :o, 1)',
               c=co['id'], n=co['name'], s=co['short'], g=gst or None, sc=gst[:2] if len(gst) >= 2 else '36',
               a=co.get('addr'), p=co.get('phone'), e=co.get('email'), o=co.get('onlPrefix'))
        audit.write('Company created (setup)', co['id'], new=co['name'], category='setup', status='Done', company=co['id'], user=user)
        made.append(co['id'])
    if user['id']:                                       # company access comes from user_companies, even for the Super Admin
        for co in seed.values():
            db.run('INSERT IGNORE INTO user_companies (user_id, company) VALUES (:u, :c)', u=user['id'], c=co['id'])
    return f"created {', '.join(made)}" if made else 'skipped (both companies exist)'


def settings():
    sys.path.insert(0, str(BACKEND / 'scripts'))
    from create_settings import rows_from_seed          # the same defaults as the step-by-step script
    have = {r['skey'] for r in db.q('SELECT skey FROM settings')}
    added = []
    for key, value in rows_from_seed().items():
        if key not in have:
            db.run('INSERT IGNORE INTO settings (skey, svalue) VALUES (:k, :v)', k=key, v=json.dumps(value, ensure_ascii=False))
            added.append(key)
    if added:
        audit.write('Settings created (setup)', 'settings', new=', '.join(added), category='setup', status='Done', user=_who())
    return f"created {', '.join(added)}" if added else 'skipped (all setting rows exist)'


def chart_of_accounts():
    from kz.services.fin_bootstrap import bootstrap
    coa = {c: (c, n, g) for c, n, g in json.loads(SEED.read_text(encoding='utf-8'))['coa']}
    for c, n, g in EXTRA_ACCOUNTS:
        coa.setdefault(c, (c, n, g))
    made = []
    for co in [r['code'] for r in db.q('SELECT code FROM companies ORDER BY code')]:
        have = {r['code'] for r in db.q('SELECT code FROM accounts WHERE company=:c', c=co)}
        new = [x for x in sorted(coa.values()) if x[0] not in have]
        for code, name, grp in new:
            db.run('INSERT INTO accounts (company, code, name, grp, is_control, active) VALUES (:co, :c, :n, :g, :ctl, 1)',
                   co=co, c=code, n=name, g=grp, ctl=1 if code in CONTROL else 0)
        if new:
            made.append(f'{co} {len(new)}')
    bootstrap(db.session().connection(), dt.date.today())   # finance and tax reference data; safe to run again
    if made:
        audit.write('Chart of accounts loaded (setup)', 'accounts', new='; '.join(made), category='setup', status='Done', user=_who())
    return f"created accounts: {', '.join(made)}" if made else 'skipped (accounts exist); finance reference data checked'


def customers():
    made = []
    for co in [r['code'] for r in db.q('SELECT code FROM companies ORDER BY code')]:
        for cid, typ, name, acct in ((f'{co}-MSEA', 'Institution', 'MS Education Academy (sponsorships)', '1210'),
                                     (f'{co}-WALKIN', 'Walk-in', 'Walk-in customer', '1200')):
            if not db.val('SELECT 1 FROM customers WHERE id=:i', i=cid):
                db.run('INSERT INTO customers (id, company, type, name, receivable_account, credit_limit) VALUES (:i, :c, :t, :n, :a, 0)',
                       i=cid, c=co, t=typ, n=name, a=acct)
                made.append(cid)
    return f"created {', '.join(made)}" if made else 'skipped (they exist)'


def main():
    p = argparse.ArgumentParser(description='Essential setup of an empty production database (safe to run again)')
    p.add_argument('--emp-id', default='KZ-IT-001', help='Super Admin employee ID used to sign in')
    p.add_argument('--name', default='IT Super Admin')
    p.add_argument('--mobile', default='9000000000', help='10-digit mobile of the Super Admin (sign-in codes go here once SMS is set up)')
    p.add_argument('--email', default=None)
    a = p.parse_args()

    with app.app_context():
        print(f'Database: {Config.DB_NAME} on {Config.DB_HOST}')
        try:
            at = db.val('SELECT version_num FROM alembic_version')
        except Exception:                                # noqa: BLE001
            at = None
        head = _head()
        if at != head:
            print(f'Refused: the database is at migration {at or "(none)"}, the code needs {head}. Run first:  python -m alembic upgrade head')
            sys.exit(1)
        steps = [('1. Super Admin', lambda: super_admin(a)), ('2. Companies', companies), ('3. Settings', settings),
                 ('4. Chart of accounts', chart_of_accounts), ('5. Customers', customers)]
        temp = None
        try:
            for title, fn in steps:
                r = fn()
                if isinstance(r, tuple):
                    r, temp = r
                print(f'  {title}: {r}')
            db.finish(True)
        except Exception:
            db.finish(False)
            print('Nothing was saved (the whole setup is one transaction). Fix the error above and run it again.')
            raise
    if temp:
        print(f'\nSign in as {a.emp_id} with the temporary password:  {temp}')
        print('It is shown only now and must be changed at the first sign-in.')
    print('\nDone. Next, on the screens: Setup > Locations, Counters, Schools, Item categories, Items, Suppliers; '
          'Settings > Roles and Users; Settings > ERP import for the students.')


if __name__ == '__main__':
    main()
