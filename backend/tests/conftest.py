"""Test fixtures. A throw-away database (<DB_NAME>_test or TEST_DB_NAME) is rebuilt once per test session with
`alembic upgrade head` + `flask kz seed --demo-students`. The DB user needs rights to create/drop that database.

Run from backend/:   python -m pytest -q          (MIGRATE_DB_USER/PASSWORD may name a schema-owner account)
"""
import json
import os
import sys
from pathlib import Path

import pymysql
import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from dotenv import dotenv_values  # noqa: E402

_env = dotenv_values(BACKEND / '.env')
TEST_DB = os.environ.get('TEST_DB_NAME') or ((_env.get('DB_NAME') or 'kiswa_zaibus') + '_test')
os.environ.update({'DB_NAME': TEST_DB, 'OTP_DEMO': '1', 'COOKIE_SECURE': '0', 'SECRET_KEY': 'test-secret', 'KZ_ENV': 'test',
                   'LOG_DIR': str(BACKEND.parent / 'logs' / 'test'), 'EVIDENCE_DIR': str(BACKEND.parent / 'data' / 'test-evidence')})
TEMP = 'Kiswa@2026'


def _conn(database=None, admin=False):
    user = (os.environ.get('MIGRATE_DB_USER') if admin else None) or os.environ.get('DB_USER') or _env.get('DB_USER')
    pw = (os.environ.get('MIGRATE_DB_PASSWORD') if admin else None) or os.environ.get('DB_PASSWORD') or _env.get('DB_PASSWORD') or ''
    return pymysql.connect(host=os.environ.get('DB_HOST') or _env.get('DB_HOST') or '127.0.0.1', port=int(os.environ.get('DB_PORT') or _env.get('DB_PORT') or 3306),
                           user=user, password=pw, database=database, autocommit=True, cursorclass=pymysql.cursors.DictCursor)


@pytest.fixture(scope='session')
def app():
    c = _conn(admin=True)
    c.cursor().execute(f'DROP DATABASE IF EXISTS `{TEST_DB}`')
    c.cursor().execute(f'CREATE DATABASE `{TEST_DB}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci')
    c.close()
    from alembic import command
    from alembic.config import Config as AlembicConfig
    os.environ['MIGRATE_DB_NAME'] = TEST_DB
    cfg = AlembicConfig(str(BACKEND / 'alembic.ini'))
    cfg.set_main_option('script_location', str(BACKEND / 'migrations'))
    command.upgrade(cfg, 'head')
    from kz import create_app
    application = create_app()
    from flask import g
    with application.app_context():
        from kz import db
        from kz.cli.seed import seed
        g.response_ok = True
        seed(TEMP, demo_students=True)
        db.finish(True)
    return application


@pytest.fixture()
def client(app):
    return app.test_client()


def sql(q, *a):
    c = _conn(TEST_DB, admin=True)
    try:
        cur = c.cursor()
        cur.execute(q, a)
        return cur.fetchall()
    finally:
        c.close()


NEW_PW = {}


def sign_in(client, emp_id, password=None):
    pw = password or NEW_PW.get(emp_id, TEMP)
    r = client.post('/api/auth/login', json={'login': emp_id, 'password': pw, 'device_id': 'pytest'})
    assert r.status_code == 200, r.get_json()
    j = r.get_json()
    if j['stage'] == 'otp':
        r = client.post('/api/auth/otp', json={'challenge': j['challenge'], 'otp': j['dev_otp'], 'device_id': 'pytest'})
        assert r.status_code == 200, r.get_json()
        j = r.get_json()
    if j['stage'] == 'change':
        new = 'Strong#Pass' + str(abs(hash(emp_id)) % 10000).zfill(4) + 'x'
        r = client.post('/api/auth/change-password', json={'challenge': j['challenge'], 'new_password': new, 'device_id': 'pytest'})
        assert r.status_code == 200, r.get_json()
        NEW_PW[emp_id] = new
        j = r.get_json()
    assert j['stage'] == 'done'
    client.post('/api/auth/reauth', json={'password': NEW_PW.get(emp_id, pw)})
    return j['user']


def as_user(app, emp_id):
    c = app.test_client()
    sign_in(c, emp_id)
    return c


def ok(r, code=200):
    assert r.status_code == code, (r.status_code, r.get_json())
    return r.get_json()


def books_balance():
    rows = sql('''SELECT j.company, SUM(l.debit) d, SUM(l.credit) c FROM journal_lines l JOIN journals j ON j.id=l.journal_id GROUP BY j.company''')
    return {r['company']: (r['d'], r['c']) for r in rows}


def stock_consistent():
    return sql('''SELECT b.branch_id, b.item_id, b.qty, COALESCE(SUM(l.qty),0) led FROM stock_balance b
                  LEFT JOIN stock_ledger l ON l.branch_id=b.branch_id AND l.item_id=b.item_id
                  GROUP BY b.branch_id, b.item_id, b.qty HAVING b.qty <> led''')


json_dumps = json.dumps
