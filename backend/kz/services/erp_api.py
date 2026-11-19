"""MCB Read-Only API connector (students), with the column mapping chosen on screen.

The API (built over the ERP's database views) exposes:
    GET /views                        list of views the key may read
    GET /views/{view}/schema          the view's columns
    GET /views/{view}?skip=&limit=&sort_by=&<column>=<value>      rows, at most 500 per call
    GET /health/db                    open, no key
Auth: the key in a header (default `x-api-key`). Address, key and header name come only from backend/.env
(ERP_API_URL, ERP_API_KEY, ERP_API_KEY_HEADER): never from the database, the screen or a request, and the key is never logged.

The mapping (settings 'erp_map') says which view feeds the student import and, per app field, where its value comes from:
    {"view": "vw_Students", "filters": {"AcademicYear": "2026-2027"},
     "fields": {"adm_no": {"col": "EnrollmentCode"}, "erp_ref": {"same_as": "adm_no"}, "school_id": {"const": "MSEA"},
                "status": {"col": "Status", "map": {"Studying": "Active"}}, "campus": {"col": "EnrollmentCode", "regex": "^\\d{2}([A-Z]+)"}}}
Rows are mapped to the importer's columns and go through erp.upsert_students (validation, dedupe by erp_ref / adm_no, run log).
A full pull runs in a background thread: it can be tens of thousands of rows.
"""
import re
import threading
from urllib.parse import quote, urlparse

import requests
from flask import current_app, g

from .. import db
from ..config import Config
from ..core import audit
from ..core.numbering import next_no
from ..core.security import setting
from ..errors import ApiError, bad, conflict
from . import erp

TIMEOUT = 60
PAGE = 500                       # the API's maximum page size
MAX_PAGES = 2000                 # safety stop (1,000,000 rows)
REQUIRED = ('adm_no', 'erp_ref', 'name', 'class_name')
VIEW_RE = re.compile(r'^[A-Za-z0-9_.$]{1,128}$')
LABELS = {'adm_no': 'Admission / enrollment number', 'erp_ref': 'ERP reference (unique, never changes)', 'name': 'Student name',
          'gender': 'Gender', 'class_name': 'Class', 'section': 'Section', 'parent_mobile': 'Parent mobile', 'parent_email': 'Parent e-mail',
          'category': 'Category (Regular, MSEA Sponsored …)', 'status': 'Status (Active, Inactive, Transferred, Left, Cancelled)',
          'school_id': 'School code', 'campus': 'Campus / branch code', 'academic_year': 'Academic year'}


# ---------------------------------------------------------------- HTTP
def configured():
    return bool(Config.ERP_API_URL and Config.ERP_API_KEY)


def _get(path, params=None, auth=True):
    if not Config.ERP_API_URL:
        raise ApiError(409, 'erp_not_configured', 'ERP_API_URL is not set in backend/.env.')
    if auth and not Config.ERP_API_KEY:
        raise ApiError(409, 'erp_key_missing', 'ERP_API_KEY is not set in backend/.env.')
    headers = {'Accept': 'application/json'}
    if auth:
        headers[Config.ERP_API_KEY_HEADER] = Config.ERP_API_KEY
    try:
        r = requests.get(Config.ERP_API_URL.rstrip('/') + path, params=params, headers=headers, timeout=TIMEOUT)
    except requests.RequestException as e:
        raise ApiError(502, 'erp_unreachable', f'The ERP API could not be reached ({type(e).__name__}).')
    if r.status_code in (401, 403):
        raise ApiError(502, 'erp_key_refused', f'The ERP API refused the key (HTTP {r.status_code}). Check ERP_API_KEY in backend/.env.')
    if r.status_code == 404:
        raise ApiError(404, 'erp_not_found', 'The ERP API has no such view.')
    if r.status_code in (400, 422):
        raise ApiError(400, 'erp_bad_request', 'The ERP API did not accept the request: check the view, column and filter names.')
    if r.status_code >= 400:
        raise ApiError(502, 'erp_error', f'The ERP API answered with an error (HTTP {r.status_code}).')
    try:
        return r.json()
    except ValueError:
        raise ApiError(502, 'erp_bad_data', 'The ERP API did not return JSON.')


def _list(payload, *keys):
    """The API's list may come bare or wrapped ({"views": [...]}, {"rows": [...]}, {"data": [...]} …)."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for k in keys + ('items', 'data', 'rows', 'results', 'value'):
            if isinstance(payload.get(k), list):
                return payload[k]
    return []


def _name(x, *keys):
    if isinstance(x, str):
        return x
    if isinstance(x, dict):
        for k in keys:
            if x.get(k):
                return str(x[k])
    return None


def status():
    out = {'configured': bool(Config.ERP_API_URL), 'key_set': bool(Config.ERP_API_KEY), 'header': Config.ERP_API_KEY_HEADER,
           'host': urlparse(Config.ERP_API_URL).netloc if Config.ERP_API_URL else None, 'reachable': None, 'database': None,
           'mapping_saved': bool(load_mapping())}
    if out['configured']:
        try:
            j = _get('/health/db', auth=False)
            out['reachable'], out['database'] = True, (j or {}).get('database') if isinstance(j, dict) else None
        except ApiError as e:
            out['reachable'], out['error'] = False, e.message
    return out


def list_views():
    views = [_name(v, 'name', 'view_name', 'view', 'TABLE_NAME', 'table_name') for v in _list(_get('/views'), 'views')]
    return sorted({v for v in views if v})


def _check_view(view):
    if not isinstance(view, str) or not VIEW_RE.fullmatch(view):
        raise bad('Choose a view.', 'bad_view')
    return view


def view_schema(view):
    payload = _get(f'/views/{quote(_check_view(view))}/schema')
    if isinstance(payload, dict) and not any(isinstance(payload.get(k), list) for k in ('columns', 'items', 'data', 'rows', 'results')):
        cols = [{'name': str(k), 'type': str(v) if not isinstance(v, dict) else str(v.get('type', ''))} for k, v in payload.items()
                if k not in ('view', 'view_name', 'name')]
    else:
        cols = []
        for c in _list(payload, 'columns'):
            n = _name(c, 'name', 'column_name', 'column', 'COLUMN_NAME', 'Field', 'field')
            if n:
                cols.append({'name': n, 'type': str(c.get('type') or c.get('data_type') or c.get('DATA_TYPE') or '') if isinstance(c, dict) else ''})
    return cols


def fetch_rows(view, filters=None, page_size=PAGE, max_rows=None, sort_by=None):
    """All rows of a view, page by page (skip / limit) until a short page."""
    _check_view(view)
    skip = got = 0
    for _ in range(MAX_PAGES):
        limit = min(page_size, max_rows - got) if max_rows else page_size
        if limit <= 0:
            return
        params = {'skip': skip, 'limit': limit, **(filters or {})}
        if sort_by:
            params['sort_by'] = sort_by
        rows = [r for r in _list(_get(f'/views/{quote(view)}', params)) if isinstance(r, dict)]
        yield from rows
        got += len(rows)
        if len(rows) < limit:
            return
        skip += len(rows)
    raise ApiError(502, 'erp_too_many_pages', 'The ERP view returned more pages than expected; stopped for safety.')


# ---------------------------------------------------------------- mapping
def load_mapping():
    m = setting('erp_map')
    return m if isinstance(m, dict) and m.get('view') and isinstance(m.get('fields'), dict) else None


def validate_mapping(spec, columns):
    """Check a mapping against the view's real columns. Returns a clean copy."""
    if not isinstance(spec, dict):
        raise bad('Send the mapping.', 'bad_mapping')
    names = {c['name'] for c in columns}
    view = _check_view(spec.get('view'))
    fields_in = spec.get('fields') or {}
    if not isinstance(fields_in, dict):
        raise bad('Fields must be an object.', 'bad_mapping')
    fields = {}
    for key, src in fields_in.items():
        if key not in erp.STUDENT_COLUMNS:
            raise bad(f'Unknown field {key}.', 'bad_mapping')
        if not src:
            continue
        if not isinstance(src, dict):
            raise bad(f'{LABELS[key]}: the source must be an object.', 'bad_mapping')
        kinds = [k for k in ('col', 'const', 'same_as') if src.get(k) not in (None, '')]
        if len(kinds) != 1:
            raise bad(f'{LABELS[key]}: choose exactly one source (a column, a fixed value or another field).', 'bad_mapping')
        out = {kinds[0]: str(src[kinds[0]]).strip()[:200]}
        if 'col' in out and out['col'] not in names:
            raise bad(f'{LABELS[key]}: the view has no column {out["col"]}.', 'unknown_column')
        if 'same_as' in out:
            if out['same_as'] not in erp.STUDENT_COLUMNS or out['same_as'] == key:
                raise bad(f'{LABELS[key]}: "same as" must name another field.', 'bad_mapping')
        if src.get('regex'):
            rx = str(src['regex'])
            if len(rx) > 200:
                raise bad(f'{LABELS[key]}: the pattern is too long.', 'bad_mapping')
            try:
                re.compile(rx)
            except re.error as e:
                raise bad(f'{LABELS[key]}: the pattern is not valid ({e}).', 'bad_mapping')
            out['regex'] = rx
        if src.get('map'):
            mp = src['map']
            if not isinstance(mp, dict) or len(mp) > 300:
                raise bad(f'{LABELS[key]}: value translations must be pairs (at most 300).', 'bad_mapping')
            out['map'] = {str(a).strip()[:120]: str(b).strip()[:120] for a, b in mp.items() if str(a).strip()}
        fields[key] = out
    for key, src in fields.items():                     # "same as" may only point at a field that has its own source
        if 'same_as' in src and ('same_as' in fields.get(src['same_as'], {'same_as': 1})):
            raise bad(f'{LABELS[key]}: "same as {src["same_as"]}" needs {src["same_as"]} to have its own column or fixed value.', 'bad_mapping')
    missing = [LABELS[k] for k in REQUIRED if k not in fields]
    if missing:
        raise bad('Map these first: ' + ', '.join(missing) + '.', 'mapping_incomplete')
    filters = {}
    for col, val in (spec.get('filters') or {}).items():
        if col not in names:
            raise bad(f'Filter: the view has no column {col}.', 'unknown_column')
        if str(val).strip():
            filters[col] = str(val).strip()[:120]
    return {'view': view, 'filters': filters, 'fields': fields}


def _value(row, lower, src):
    if 'const' in src:
        v = src['const']
    else:
        c = src.get('col')
        v = row.get(c) if c in row else lower.get((c or '').lower())
    v = None if v is None else str(v).strip()
    if v and src.get('regex'):
        m = re.search(src['regex'], v)
        v = (m.group(1) if m.groups() else m.group(0)) if m else None
    if v and src.get('map'):
        mp = src['map']
        v = mp.get(v, next((b for a, b in mp.items() if a.lower() == v.lower()), v))
    return v or None


def apply_mapping(row, spec):
    lower = {str(k).lower(): v for k, v in row.items()}
    out = {}
    for key, src in spec['fields'].items():
        if 'same_as' not in src:
            out[key] = _value(row, lower, src)
    for key, src in spec['fields'].items():
        if 'same_as' in src:
            out[key] = out.get(src['same_as'])
    return out


def _sort_column(spec):
    f = spec['fields']
    return (f.get('erp_ref') or {}).get('col') or (f.get('adm_no') or {}).get('col')


def preview(spec, n=20):
    rows = list(fetch_rows(spec['view'], spec.get('filters'), page_size=n, max_rows=n, sort_by=_sort_column(spec)))
    out = []
    for r in rows:
        m = apply_mapping(r, spec)
        out.append({'raw': r, 'mapped': m, 'problem': erp._validate(erp._norm(m))})
    return {'rows': out, 'fields': list(erp.STUDENT_COLUMNS)}


def save_mapping(spec, user):
    clean = validate_mapping(spec, view_schema(spec.get('view') if isinstance(spec, dict) else None))
    old = load_mapping()
    db.run('''INSERT INTO settings (skey, svalue, updated_by, updated_at) VALUES ('erp_map', :v, :u, NOW(6))
              ON DUPLICATE KEY UPDATE svalue=:v, updated_by=:u, updated_at=NOW(6)''', v=clean, u=user['id'])
    audit.write('ERP mapping changed', clean['view'], old=old, new=clean, category='integration')
    return clean


# ---------------------------------------------------------------- the pull (background)
def start_pull(by, dry_run=False):
    spec = load_mapping()
    if not spec:
        raise conflict('Save the column mapping first (Settings → ERP import → API connection).', 'no_mapping')
    if not configured():
        raise ApiError(409, 'erp_key_missing', 'ERP_API_URL and ERP_API_KEY must be set in backend/.env.')
    db.run("UPDATE erp_sync_runs SET status='Failed', finished_at=NOW(6) WHERE status='Running' AND started_at < NOW(6) - INTERVAL 2 HOUR")
    if db.val("SELECT COUNT(*) FROM erp_sync_runs WHERE status='Running'"):
        raise conflict('An ERP pull is already running. Wait for it to finish.', 'erp_running')
    run_no = next_no('HO', 'ERP')
    run_id = db.insert('erp_sync_runs', dict(no=run_no, triggered_by=by, trigger_type='api', feeds='students', dry_run=1 if dry_run else 0, status='Running'))
    db.session().commit()                                # visible to the screen while the thread works
    app = current_app._get_current_object()
    threading.Thread(target=_job, args=(app, run_id, by, spec, dry_run), daemon=True, name=f'erp-pull-{run_no}').start()
    return {'run': run_no, 'status': 'Running', 'dry_run': dry_run}


def _job(app, run_id, by, spec, dry_run):
    with app.app_context():
        g.response_ok = True
        try:
            rows = [apply_mapping(r, spec) for r in fetch_rows(spec['view'], spec.get('filters'), sort_by=_sort_column(spec))]
            erp.upsert_students(rows, trigger='api', by=by, dry_run=dry_run, run_id=run_id, commit_every=500)
            db.finish(True)
        except Exception as e:                               # noqa: BLE001 (record any failure on the run)
            db.finish(False)
            msg = e.message if isinstance(e, ApiError) else f'Unexpected error ({type(e).__name__}).'
            app.logger.warning('ERP pull run %s failed: %s', run_id, msg)
            try:
                db.run("UPDATE erp_sync_runs SET status='Failed', finished_at=NOW(6) WHERE id=:i", i=run_id)
                db.insert('erp_sync_records', dict(run_id=run_id, feed='students', direction='in', source_ref='RUN', action='failed',
                                                   status='Failed', error=msg[:500], snapshot={}))
                db.finish(True)
            except Exception:                                # noqa: BLE001
                db.finish(False)
                app.logger.exception('Could not mark ERP run %s as failed', run_id)
