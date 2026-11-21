"""MSEA ERP sync (students). Two sources, same upsert:
  * file:  CSV upload (Settings -> ERP sync) with the columns in STUDENT_COLUMNS
  * api:   with a saved column mapping: the MCB Read-Only views API (services/erp_api.py, background pull);
           otherwise GET {ERP_API_URL}/students?updated_since=<iso> with Bearer ERP_API_TOKEN, returning a JSON list of the same fields
Each ERP record is keyed by erp_ref in erp_keys with a hash of its content: unchanged records are skipped, changed ones
updated, new ones created, so repeated syncs never duplicate. Every row is logged in erp_sync_records.
"""
import csv
import datetime as dt
import hashlib
import io
import json
import re

import requests

from .. import db
from ..config import Config
from ..core import audit, notify
from ..core.numbering import next_no
from ..errors import ApiError, bad

STUDENT_COLUMNS = ['adm_no', 'erp_ref', 'name', 'gender', 'class_name', 'section', 'parent_mobile', 'parent_email', 'category', 'status',
                   'school_id', 'campus', 'academic_year']
ALIASES = {'admission_no': 'adm_no', 'adm': 'adm_no', 'erp': 'erp_ref', 'erp_id': 'erp_ref', 'student_name': 'name', 'class': 'class_name',
           'mobile': 'parent_mobile', 'email': 'parent_email', 'school': 'school_id', 'ay': 'academic_year', 'g': 'gender', 'sec': 'section', 'cat': 'category'}
STATUSES = {'Active', 'Inactive', 'Transferred', 'Left', 'Cancelled'}
# column sizes of the students table (models/masters.py Student)
MAX_LEN = {'erp_ref': 30, 'name': 120, 'class_name': 20, 'section': 5, 'parent_email': 120, 'category': 40, 'school_id': 10, 'campus': 30,
           'academic_year': 10}


def _norm(row):
    out = {}
    for k, v in row.items():
        k = (k or '').strip().lower().replace(' ', '_')
        k = ALIASES.get(k, k)
        if k in STUDENT_COLUMNS:
            out[k] = (str(v).strip() if v is not None else '') or None
    return out


def _validate(r):
    if not r.get('adm_no') or not re.fullmatch(r'[A-Z0-9/-]{4,20}', r['adm_no'].upper()):
        return 'admission number missing or invalid'
    if not r.get('erp_ref'):
        return 'ERP reference missing'
    if not r.get('name'):
        return 'name missing'
    if not r.get('class_name'):
        return 'class missing'
    if r.get('gender') and r['gender'][:1].upper() not in ('M', 'F'):
        return 'gender must be M or F'
    if r.get('parent_mobile'):
        m = re.sub(r'\D', '', r['parent_mobile'])[-10:]
        if not re.fullmatch(r'[6-9]\d{9}', m):
            return 'parent mobile is not a valid 10-digit number'
    if r.get('status') and r['status'] not in STATUSES:
        return f"status must be one of {', '.join(sorted(STATUSES))}"
    for k, n in MAX_LEN.items():                       # a value longer than its column fails this row only (not the whole run)
        if r.get(k) and len(str(r[k])) > n:
            return f'{k.replace("_", " ")} is longer than {n} characters ({str(r[k])[:30]}…)'
    return None


def upsert_students(rows, *, trigger, by, dry_run=False, run_id=None, commit_every=0, stop=None):
    """run_id: a run row created beforehand (background pulls); commit_every: commit and publish the counts every N rows,
    so a long pull is visible on the screen while it works."""
    if run_id is None:
        run_no = next_no('HO', 'ERP')
        run_id = db.insert('erp_sync_runs', dict(no=run_no, triggered_by=by, trigger_type=trigger, feeds='students', dry_run=1 if dry_run else 0, status='Running'))
    else:
        run_no = db.val('SELECT no FROM erp_sync_runs WHERE id=:i', i=run_id)
    counts = {'created': 0, 'updated': 0, 'skipped': 0, 'failed': 0}
    for n, raw in enumerate(rows, 1):
        if commit_every and n % commit_every == 0:
            if stop and stop():
                from .erp_api import Stopped
                raise Stopped()
            db.run("UPDATE erp_sync_runs SET created=:c, updated=:u, skipped=:s, failed=:f WHERE id=:i AND status='Running'",
                   c=counts['created'], u=counts['updated'], s=counts['skipped'], f=counts['failed'], i=run_id)
            db.session().commit()
        r = _norm(raw)
        err = _validate(r)
        ref = r.get('erp_ref') or r.get('adm_no') or '?'
        if err:
            counts['failed'] += 1
            db.insert('erp_sync_records', dict(run_id=run_id, feed='students', direction='in', source_ref=str(ref)[:60], action='failed', status='Failed',
                                               error=err, snapshot=r))
            continue
        r['adm_no'] = r['adm_no'].upper()
        r['gender'] = (r.get('gender') or '')[:1].upper() or None
        r['parent_mobile'] = re.sub(r'\D', '', r.get('parent_mobile') or '')[-10:] or None
        r.setdefault('category', 'Regular')
        r['category'] = r['category'] or 'Regular'
        r['status'] = r.get('status') or 'Active'
        version = hashlib.sha256(json.dumps(r, sort_keys=True).encode()).hexdigest()
        key = db.one('SELECT version FROM erp_keys WHERE feed=:f AND source_ref=:s', f='students', s=r['erp_ref'])
        if key and key['version'] == version:
            counts['skipped'] += 1
            continue
        clash = db.one('SELECT erp_ref FROM students WHERE adm_no=:a', a=r['adm_no'])
        if clash and clash['erp_ref'] != r['erp_ref']:
            counts['failed'] += 1
            db.insert('erp_sync_records', dict(run_id=run_id, feed='students', direction='in', source_ref=r['erp_ref'], action='failed', status='Failed',
                                               error=f"admission number {r['adm_no']} already belongs to ERP record {clash['erp_ref']}", snapshot=r))
            continue
        action = 'updated' if clash else 'created'
        if not dry_run:
            vals = {k: r.get(k) for k in STUDENT_COLUMNS}
            vals['synced_at'] = dt.datetime.now()
            if clash:
                sets = ', '.join(f'{k}=:{k}' for k in vals if k != 'adm_no')
                db.run(f'UPDATE students SET {sets} WHERE adm_no=:adm_no', **vals)
            else:
                db.insert('students', vals)
            db.run('''INSERT INTO erp_keys (feed, source_ref, version, target_ref) VALUES ('students', :s, :v, :t)
                      ON DUPLICATE KEY UPDATE version=:v, target_ref=:t, synced_at=NOW(6)''', s=r['erp_ref'], v=version, t=r['adm_no'])
        counts[action] += 1
        db.insert('erp_sync_records', dict(run_id=run_id, feed='students', direction='in', source_ref=r['erp_ref'], action=action, status='Done' if not dry_run else 'Dry run',
                                           snapshot=r))
    status = 'Completed' if not counts['failed'] else 'Completed with errors'
    db.run('''UPDATE erp_sync_runs SET finished_at=NOW(6), created=:c, updated=:u, skipped=:s, failed=:f, status=:st WHERE id=:i''',
           c=counts['created'], u=counts['updated'], s=counts['skipped'], f=counts['failed'], st=status, i=run_id)
    audit.write('ERP sync', run_no, new=counts, category='integration', user={'id': None, 'name': by, 'main_role': 'System'})
    if counts['failed']:
        notify.alert('ERP sync errors', f'{run_no}: {counts["failed"]} record(s) failed; see Settings -> ERP sync', ref=run_no, severity='warn')
    return {'run': run_no, **counts, 'status': status, 'dry_run': dry_run}


def import_csv(data: bytes, by, dry_run=False):
    try:
        text = data.decode('utf-8-sig')
    except UnicodeDecodeError:
        text = data.decode('latin-1')
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        raise bad('The file has no rows. The first line must be the column names.', 'empty_file')
    cols = {ALIASES.get(c.strip().lower().replace(' ', '_'), c.strip().lower().replace(' ', '_')) for c in rows[0].keys()}
    missing = {'adm_no', 'erp_ref', 'name', 'class_name'} - cols
    if missing:
        raise bad('Missing column(s): ' + ', '.join(sorted(missing)), 'missing_columns')
    return upsert_students(rows, trigger='file', by=by, dry_run=dry_run)


def pull_api(by, since=None, dry_run=False):
    """With a saved column mapping and ERP_API_KEY: the MCB views API, in the background (services/erp_api.py).
    Otherwise the original connector: GET {ERP_API_URL}/students with a Bearer token, already in our column names."""
    from . import erp_api
    if erp_api.load_mapping() and Config.ERP_API_KEY:
        return erp_api.start_pull(by, dry_run=dry_run)
    if not Config.ERP_API_URL:
        raise ApiError(409, 'erp_not_configured', 'ERP_API_URL is not set on the server. Use the CSV import, or configure the connector.')
    try:
        r = requests.get(Config.ERP_API_URL.rstrip('/') + '/students', params={'updated_since': since} if since else None,
                         headers={'Authorization': f'Bearer {Config.ERP_API_TOKEN}'} if Config.ERP_API_TOKEN else {}, timeout=60)
        r.raise_for_status()
        rows = r.json()
    except (requests.RequestException, ValueError) as e:
        notify.alert('ERP sync errors', f'ERP API call failed: {type(e).__name__}', severity='bad')
        raise ApiError(502, 'erp_unreachable', f'The ERP could not be reached or returned bad data ({type(e).__name__}).')
    if isinstance(rows, dict):
        rows = rows.get('items') or rows.get('students') or []
    return upsert_students(rows, trigger='api', by=by, dry_run=dry_run)
