"""Step 2 of an empty install: create ONLY the Super Admin user (no companies, items or other users).

    python scripts\\create_super_admin.py                 # from the backend folder
    python scripts\\create_super_admin.py --emp-id KZ-IT-001 --name "IT Super Admin" --mobile 9848010233

Run it after `python -m alembic upgrade head` on an empty database. It writes exactly:
  1. roles          - the 'Super Admin' role row (users.main_role must point to an existing role)
  2. users          - one user, main_role = 'Super Admin', all branches, must change password at first sign-in
  3. password_history - the temporary password, so it cannot be reused as the new one
  4. audit_log      - one entry 'Super Admin created (command line)' in the tamper-proof audit chain
The Super Admin needs no rows in `permissions`: kz/core/rbac.py gives that role every screen and every action.
It refuses to run when the database already has any user.
"""
import argparse
import secrets
import string
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from wsgi import app  # noqa: E402  (reads backend/.env -> DB_NAME)
from kz import db  # noqa: E402
from kz.config import Config  # noqa: E402
from kz.core import audit  # noqa: E402
from kz.core.security import hash_pw  # noqa: E402

ROLE = 'Super Admin'


def main():
    p = argparse.ArgumentParser(description='Create only the Super Admin user in an empty database')
    p.add_argument('--emp-id', default='KZ-IT-001', help='employee ID used to sign in')
    p.add_argument('--name', default='IT Super Admin')
    p.add_argument('--mobile', default='9848010233', help='10-digit mobile (sign-in codes go here once SMS is set up)')
    p.add_argument('--email', default='it.super@kiswazaibus.in')
    a = p.parse_args()

    with app.app_context():
        print(f'Database: {Config.DB_NAME} on {Config.DB_HOST}')
        if db.val('SELECT COUNT(*) FROM users'):
            print('Refused: this database already has users. This script is only for an empty database.')
            sys.exit(1)
        temp = 'Kz#' + ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(10)) + '7'
        pw_hash = hash_pw(temp)
        try:
            # 1. the role (INSERT IGNORE: skipped if it already exists)
            db.run("INSERT IGNORE INTO roles (name, description, approval_rank, read_only, is_custom, active) "
                   "VALUES (:n, 'Every screen and action; cannot approve own requests', 3, 0, 0, 1)", n=ROLE)
            # 2. the user
            db.run('INSERT INTO users (id, emp_id, name, mobile, email, department, designation, main_role, status, all_branches, '
                   'pw_hash, pw_set_at, pw_must_change) VALUES (:id, :e, :n, :m, :em, :dep, :des, :r, :s, 1, :h, NOW(), 1)',
                   id='u1', e=a.emp_id, n=a.name, m=a.mobile, em=a.email, dep='IT & Maintenance', des='System administration',
                   r=ROLE, s='Active', h=pw_hash)
            # 3. password history
            db.run('INSERT INTO password_history (user_id, pw_hash) VALUES (:i, :h)', i='u1', h=pw_hash)
            # 4. audit trail
            audit.write('Super Admin created (command line)', a.name, new=a.emp_id, category='security',
                        user={'id': None, 'name': 'System (CLI)', 'main_role': 'System'})
            db.finish(True)
        except Exception:
            db.finish(False)
            raise
    print(f'Created Super Admin {a.emp_id} ({a.name}).')
    print(f'Temporary password: {temp}   (must be changed at first sign-in)')


if __name__ == '__main__':
    main()
