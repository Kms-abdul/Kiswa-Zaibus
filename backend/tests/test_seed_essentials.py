"""seed_essentials.py on a brand-new empty database: refuses before the migrations, creates only the essentials, and a second run
changes nothing."""
import os
import subprocess
import sys

from conftest import BACKEND, TEST_DB, _conn

EMPTY = TEST_DB + '_empty'


def _run(*args):
    env = dict(os.environ, DB_NAME=EMPTY, MIGRATE_DB_NAME=EMPTY)
    return subprocess.run([sys.executable, str(BACKEND / 'seed_essentials.py'), *args], cwd=str(BACKEND), env=env,
                          capture_output=True, text=True, timeout=600)


def _q(sql):
    c = _conn(EMPTY)
    try:
        with c.cursor() as cur:
            cur.execute(sql)
            return cur.fetchall()
    finally:
        c.close()


def test_seed_essentials_on_empty_database():
    c = _conn(admin=True)
    c.cursor().execute(f'DROP DATABASE IF EXISTS `{EMPTY}`')
    c.cursor().execute(f'CREATE DATABASE `{EMPTY}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci')
    c.close()
    try:
        r = _run()
        assert r.returncode == 1 and 'alembic upgrade head' in r.stdout          # tables first
        env = dict(os.environ, DB_NAME=EMPTY, MIGRATE_DB_NAME=EMPTY)
        m = subprocess.run([sys.executable, '-m', 'alembic', 'upgrade', 'head'], cwd=str(BACKEND), env=env, capture_output=True, text=True, timeout=600)
        assert m.returncode == 0, m.stderr[-2000:]

        r = _run('--emp-id', 'KZ-IT-900', '--mobile', '9000000099')
        assert r.returncode == 0, r.stdout + r.stderr[-2000:]
        assert 'temporary password' in r.stdout
        users = _q('SELECT emp_id, main_role, pw_must_change FROM users')
        assert users == [{'emp_id': 'KZ-IT-900', 'main_role': 'Super Admin', 'pw_must_change': 1}]
        assert {x['code'] for x in _q('SELECT code FROM companies')} == {'KG', 'ZE'}
        assert {x['company'] for x in _q('SELECT company FROM user_companies')} == {'KG', 'ZE'}
        assert _q("SELECT COUNT(*) n FROM accounts WHERE code='1210'")[0]['n'] == 2
        assert {x['id'] for x in _q('SELECT id FROM customers')} == {'KG-MSEA', 'ZE-MSEA', 'KG-WALKIN', 'ZE-WALKIN'}
        assert {'security', 'controls', 'maker_checker', 'erp'} <= {x['skey'] for x in _q('SELECT skey FROM settings')}
        for t in ('items', 'branches', 'students', 'suppliers', 'invoices', 'stock_balance'):   # no demo or business data
            assert _q(f'SELECT COUNT(*) n FROM {t}')[0]['n'] == 0, t

        counts = {t: _q(f'SELECT COUNT(*) n FROM {t}')[0]['n'] for t in ('users', 'companies', 'accounts', 'customers', 'settings')}
        again = _run()
        assert again.returncode == 0 and 'temporary password' not in again.stdout and 'skipped' in again.stdout
        assert counts == {t: _q(f'SELECT COUNT(*) n FROM {t}')[0]['n'] for t in counts}            # a second run changes nothing
    finally:
        c = _conn(admin=True)
        c.cursor().execute(f'DROP DATABASE IF EXISTS `{EMPTY}`')
        c.close()
