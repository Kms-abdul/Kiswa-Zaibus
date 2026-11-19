"""Maintenance commands:  flask --app wsgi kz <command>

  seed [--demo-students]   load go-live master data into an empty database (after `alembic upgrade head`)
  seed --test-data         load the full sample data set (masters + transactions) instead, for testing only
  export-test-data         write seed/test_data.json.gz from the current database (developers)
  tick                     run the scheduler once (systemd timer runs it every minute; see deploy/oci)
  doctor                   check data integrity: audit chain, books balance, stock ledger = balance, orphans
  send-outbox              send queued email / SMS / WhatsApp now
  set-password EMP_ID      give a user a new temporary password (they must change it at next sign-in)
  sync-permissions         copy the permission catalogue into the database (also done at every start-up)
"""
import secrets
import string
import sys

import click
from flask import g

from .. import db


def _done(ok=True):
    db.finish(ok)


def register(app):
    @app.cli.group('kz')
    def kz():
        """Kiswa-Zaibus maintenance commands."""

    @kz.command('seed')
    @click.option('--demo-students', is_flag=True, help='Also load the 35 cached MSEA demo students.')
    @click.option('--test-data', is_flag=True, help='Load the full sample data set (masters and transactions) for testing, instead of go-live masters.')
    @click.option('--force', is_flag=True, help='Allow --test-data when KZ_ENV=production (never on the live server).')
    @click.option('--temp-password', default='Kiswa@2026', show_default=True, help='Temporary password for every seeded user.')
    def seed_cmd(demo_students, test_data, force, temp_password):
        g.response_ok = True
        if test_data:
            from ..config import Config
            from .testdata import load_snapshot
            if Config.ENV == 'production' and not force:
                click.echo('Refused: KZ_ENV=production. Test data is for a test server or a separate test database (add --force only if you are sure).')
                sys.exit(1)
            try:
                counts = load_snapshot(temp_password)
                _done(True)
            except Exception:
                _done(False)
                raise
            if counts is None:
                click.echo('Seed skipped: the database already has companies (it never overwrites data).')
            else:
                click.echo(f"Test data loaded: {sum(counts.values())} rows in {len(counts)} tables. Every user's temporary password is {temp_password} (change at first sign-in).")
            return
        from .seed import seed
        try:
            done = seed(temp_password, demo_students)
            _done(True)
        except Exception:
            _done(False)
            raise
        click.echo('Seed complete.' if done else 'Seed skipped: the database already has companies (it never overwrites data).')

    @kz.command('export-test-data')
    def export_test_data_cmd():
        from .testdata import SNAPSHOT, export_snapshot
        counts = export_snapshot()
        _done(True)
        click.echo(f'Wrote {SNAPSHOT}: {sum(counts.values())} rows in {len(counts)} tables.')

    @kz.command('tick')
    def tick_cmd():
        from ..services.scheduler import tick
        try:
            out = tick()
            _done(True)
        except Exception:
            _done(False)
            raise
        if any(out.values()):
            click.echo(' '.join(f'{k}={v}' for k, v in out.items() if v))

    @kz.command('send-outbox')
    def outbox_cmd():
        from ..services.outbox import send_queued
        try:
            r = send_queued(limit=200)
            _done(True)
        except Exception:
            _done(False)
            raise
        click.echo(f"sent={r['sent']} failed={r['failed']}")

    @kz.command('sync-permissions')
    def sync_permissions_cmd():
        """Copy the permission catalogue (kz/core/permission_catalog.py) into perm_modules / perm_screens (also done at start-up)."""
        from ..core.permission_catalog import sync_catalog
        try:
            r = sync_catalog(db.session().connection())
            _done(True)
        except Exception:
            _done(False)
            raise
        click.echo(f"Permission catalogue synced: {r['modules']} modules, {r['screens']} screens.")

    @kz.command('doctor')
    def doctor_cmd():
        from ..services.doctor import run_checks
        results = run_checks()
        _done(True)
        bad = 0
        for name, ok, detail in results:
            bad += 0 if ok else 1
            click.echo(f"{'OK  ' if ok else 'FAIL'}  {name}: {detail}")
        sys.exit(1 if bad else 0)

    @kz.command('set-password')
    @click.argument('emp_id')
    def set_password_cmd(emp_id):
        from ..core.security import hash_pw
        u = db.one('SELECT id, name FROM users WHERE emp_id=:e', e=emp_id)
        if not u:
            click.echo(f'No user with employee ID {emp_id}.')
            sys.exit(1)
        temp = 'Kz#' + ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(10)) + '7'
        db.run("UPDATE users SET pw_hash=:h, pw_must_change=1, failed_count=0, status=IF(status='Locked','Active',status), locked_at=NULL WHERE id=:i",
               h=hash_pw(temp), i=u['id'])
        from ..core import audit
        audit.write('Temporary password issued (command line)', u['name'], category='security', user={'id': None, 'name': 'System (CLI)', 'main_role': 'System'})
        _done(True)
        click.echo(f"Temporary password for {u['name']}: {temp}  (must be changed at next sign-in)")
