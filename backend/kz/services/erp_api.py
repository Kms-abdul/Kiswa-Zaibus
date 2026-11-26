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
import datetime as dt
import hashlib
import json
import re
import threading
import time
from urllib.parse import quote, urlparse

import requests
from flask import current_app, g
from sqlalchemy import text

from .. import db
from ..config import Config
from ..core import audit
from ..core.numbering import next_no
from ..core.security import setting
from ..errors import ApiError, bad, conflict
from . import erp

TIMEOUT = 60
PAGE = 500                       # the API's maximum page size
PAUSE = 0.3                      # seconds between pages, to be gentle on the ERP server
RETRIES = (2, 5, 15)             # wait and retry a page this many times when the server refuses, times out or answers 5xx
_STOP = set()                    # run ids asked to stop (checked between pages and every 500 rows)


class Stopped(Exception):
    pass
MAX_PAGES = 2000                 # safety stop (1,000,000 rows)
REQUIRED = ('adm_no', 'erp_ref', 'name', 'class_name')
VIEW_RE = re.compile(r'^[A-Za-z0-9_.$]{1,128}$')
LABELS = {'adm_no': 'Admission / enrollment number', 'erp_ref': 'ERP reference (unique, never changes)', 'name': 'Student name',
          'gender': 'Gender', 'class_name': 'Class', 'section': 'Section', 'parent_mobile': 'Parent mobile', 'parent_email': 'Parent e-mail',
          'category': 'Category (Regular, MSEA Sponsored …)', 'status': 'Status (Active, Inactive, Transferred, Left, Cancelled)',
          'school_id': 'School code (matches Setup → Schools, e.g. MSEA)', 'campus': 'Campus / branch code', 'branch_name': 'Branch name (e.g. MS Charminar Boys)', 'location': 'Location (city / state)',
          'academic_year': 'Academic year'}


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
    spec = load_mapping()
    st = load_state(spec) if spec else None
    out['sync'] = {'tracking': bool(spec and spec.get('track')), 'changes_ready': bool(spec and spec.get('track') and st and st.get('max')),
                   'full_at': (st or {}).get('full_at'), 'last_at': (st or {}).get('last_at'), 'since': (st or {}).get('max')}
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


def _filter_sets(filters):
    """A filter value may list several values ("2026-2027, 2027-2028"); the API only matches one value, so ask once per combination,
    in the order written."""
    sets = [{}]
    for col, val in (filters or {}).items():
        vals = [v.strip() for v in str(val).split(',') if v.strip()] or ['']
        sets = [{**s, col: v} for s in sets for v in vals]
    return sets


def fetch_rows(view, filters=None, page_size=PAGE, max_rows=None, sort_by=None, stop=None):
    """All rows of a view; with several filter values, each set in turn (max_rows counts across all of them)."""
    got = 0
    for f in _filter_sets(filters):
        for r in _fetch_rows(view, f, page_size, (max_rows - got) if max_rows else None, sort_by, stop):
            got += 1
            yield r
        if max_rows and got >= max_rows:
            return


def _fetch_rows(view, filters=None, page_size=PAGE, max_rows=None, sort_by=None, stop=None):
    """All rows of a view, page by page (skip / limit) until a short page. `stop`: callable, True = abandon (raises Stopped)."""
    _check_view(view)
    skip = got = 0
    for _ in range(MAX_PAGES):
        if stop and stop():
            raise Stopped()
        limit = min(page_size, max_rows - got) if max_rows else page_size
        if limit <= 0:
            return
        params = {'skip': skip, 'limit': limit, **(filters or {})}
        if sort_by:
            params['sort_by'] = sort_by
        rows = _page(view, params)
        yield from rows
        got += len(rows)
        if len(rows) < limit:
            return
        skip += len(rows)
        if PAUSE:
            time.sleep(PAUSE)
    raise ApiError(502, 'erp_too_many_pages', 'The ERP view returned more pages than expected; stopped for safety.')


def _page(view, params):
    """One page, retried when the server is busy, refusing or restarting."""
    for attempt, wait in enumerate((0,) + RETRIES):
        if wait:
            time.sleep(wait)
        try:
            return [r for r in _list(_get(f'/views/{quote(view)}', params)) if isinstance(r, dict)]
        except ApiError as e:
            if e.code not in ('erp_unreachable', 'erp_error') or attempt == len(RETRIES):
                raise
    return []


# ---------------------------------------------------------------- incremental pulls ("Pull changes")
OVERLAP = dt.timedelta(days=1)     # re-read one day before the last sync point; rows already up to date are simply skipped


def _ts(v):
    """The API's dates are ISO text (2026-11-21T15:08:41.837000); anything else counts as unknown."""
    if v in (None, ''):
        return None
    try:
        return dt.datetime.fromisoformat(str(v).replace('Z', '+00:00')[:26]).replace(tzinfo=None)
    except ValueError:
        return None


def fetch_changes(spec, state, stop=None):
    """Rows created or changed since the last pull: for each tracked date column, read newest first and stop at the first row
    older than (last sync point - OVERLAP). Yields raw rows; a row may come twice (changed and created), the caller de-duplicates."""
    view = spec['view']
    for filters, (kind, col) in ((f, t) for f in _filter_sets(spec.get('filters')) for t in (spec.get('track') or {}).items()):
        last = _ts((state.get('max') or {}).get(kind))
        if not col or not last:
            continue
        cutoff = last - OVERLAP
        skip = 0
        for _ in range(MAX_PAGES):
            if stop and stop():
                raise Stopped()
            rows = _page(view, {'skip': skip, 'limit': PAGE, 'sort_by': col, 'sort_dir': 'desc', **filters})
            older = False
            for r in rows:
                t = _ts(r.get(col))
                if t is None or t < cutoff:          # sorted newest first: the rest is older than the sync point
                    older = True
                    break
                yield r
            if older or len(rows) < PAGE:
                break
            skip += len(rows)
            if PAUSE:
                time.sleep(PAUSE)


def _signature(spec):
    """What decides WHICH rows a pull reads (view, filters, tracked dates). Changing how a column maps does not lock "Pull changes"."""
    key = {k: spec.get(k) for k in ('view', 'filters', 'track')}
    return hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()


def load_state(spec=None):
    """Where the last successful pull got to. Ignored (a full pull is needed) when the mapping changed since."""
    s = setting('erp_sync_state')
    if not isinstance(s, dict):
        return None
    if spec is not None and s.get('sig') != _signature(spec):
        return None
    return s


def _save_state(spec, maxima, full, old):
    st = {'sig': _signature(spec), 'max': {}, 'full_at': (old or {}).get('full_at'), 'last_at': dt.datetime.now().isoformat(timespec='seconds')}
    for kind in (spec.get('track') or {}):
        vals = [v for v in (maxima.get(kind), ((old or {}).get('max') or {}).get(kind)) if _ts(v)]
        if vals:
            st['max'][kind] = max(vals, key=_ts)
    if full:
        st['full_at'] = st['last_at']
    db.run('''INSERT INTO settings (skey, svalue, updated_at) VALUES ('erp_sync_state', :v, NOW(6))
              ON DUPLICATE KEY UPDATE svalue=:v, updated_at=NOW(6)''', v=st)
    return st


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
        vals = [v.strip() for v in str(val).split(',') if v.strip()]
        if len(vals) > 10:
            raise bad(f'Filter {col}: at most 10 values.', 'bad_mapping')
        if vals:
            filters[col] = ', '.join(vals)[:250]
    track, tin = {}, spec.get('track') if isinstance(spec.get('track'), dict) else {}
    for kind in ('changed', 'created'):                 # date columns that let "Pull changes" read only what is new
        col = str(tin.get(kind) or '').strip()
        if col:
            if col not in names:
                raise bad(f'Change tracking: the view has no column {col}.', 'unknown_column')
            track[kind] = col
    out = {'view': view, 'filters': filters, 'fields': fields}
    if track:
        out['track'] = track
    return out


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
def start_pull(by, dry_run=False, mode='auto', since=None):
    """mode: 'full' (every row), 'changes' (only rows created / changed since the last pull), 'auto' (changes when possible).
    since: a date (YYYY-MM-DD): only rows created / changed from that day on, without needing a full pull first."""
    spec = load_mapping()
    if not spec:
        raise conflict('Save the column mapping first (Settings → ERP import → API connection).', 'no_mapping')
    if not configured():
        raise ApiError(409, 'erp_key_missing', 'ERP_API_URL and ERP_API_KEY must be set in backend/.env.')
    state = load_state(spec)
    if since:
        if not spec.get('track'):
            raise conflict('Choose the change-tracking date columns (step 4) and save the mapping first.', 'no_tracking')
        try:
            day = dt.datetime.strptime(str(since)[:10], '%Y-%m-%d')
        except ValueError:
            raise bad('Give the date as YYYY-MM-DD.', 'bad_date') from None
        if day > dt.datetime.now():
            raise bad('The date cannot be in the future.', 'bad_date')
        # start from that day (the one-day overlap is added back so the day itself is read in full)
        state = {'max': {k: (day + OVERLAP).isoformat() for k in spec['track']}, 'full_at': (state or {}).get('full_at'), 'since': day.date().isoformat()}
        mode = 'changes'
    can_changes = bool(spec.get('track')) and bool(state and state.get('max'))
    if mode == 'auto':
        mode = 'changes' if can_changes else 'full'
    if mode == 'changes' and not can_changes:
        raise conflict('Pull changes needs the change-tracking date columns and one completed full pull with this mapping. Run a full pull first.',
                       'full_pull_needed')
    if mode not in ('full', 'changes'):
        raise bad('Unknown pull mode.', 'bad_mode')
    db.run("UPDATE erp_sync_runs SET status='Failed', finished_at=NOW(6) WHERE status='Running' AND started_at < NOW(6) - INTERVAL 2 HOUR")
    if db.val("SELECT COUNT(*) FROM erp_sync_runs WHERE status='Running'"):
        raise conflict('An ERP pull is already running. Wait for it to finish.', 'erp_running')
    run_no = next_no('HO', 'ERP')
    run_id = db.insert('erp_sync_runs', dict(no=run_no, triggered_by=by, trigger_type='api changes' if mode == 'changes' else 'api full', feeds='students',
                                             dry_run=1 if dry_run else 0, status='Running'))
    db.session().commit()                                # visible to the screen while the thread works
    app = current_app._get_current_object()
    threading.Thread(target=_job, args=(app, run_id, by, spec, dry_run, mode, state), daemon=True, name=f'erp-pull-{run_no}').start()
    return {'run': run_no, 'status': 'Running', 'dry_run': dry_run, 'mode': mode}


def stop_run(run_id, user):
    """Stop a running pull. Works also for a run whose thread is gone (backend restarted): it is simply marked Stopped."""
    r = db.one('SELECT id, no, status FROM erp_sync_runs WHERE id=:i', i=run_id)
    if not r:
        raise ApiError(404, 'not_found', 'Run not found.')
    if r['status'] != 'Running':
        raise conflict(f"{r['no']} is not running.", 'not_running')
    _STOP.add(run_id)
    db.run("UPDATE erp_sync_runs SET status='Stopped', finished_at=NOW(6) WHERE id=:i AND status='Running'", i=run_id)
    db.insert('erp_sync_records', dict(run_id=run_id, feed='students', direction='in', source_ref='RUN', action='stopped', status='Stopped',
                                       error=f"Stopped by {user['name']}; rows already saved stay saved.", snapshot={}))
    audit.write('ERP pull stopped', r['no'], category='integration')
    return {'run': r['no'], 'status': 'Stopped'}


def _stopped(run_id):
    """Asked to stop? In this process (_STOP) or by another server worker (the run row is no longer Running). A fresh connection
    reads the latest committed status (the job's own transaction would keep seeing its old snapshot)."""
    if run_id in _STOP:
        return True
    try:
        with db.engine().connect() as c:
            return c.execute(text('SELECT status FROM erp_sync_runs WHERE id=:i'), {'i': run_id}).scalar() != 'Running'
    except Exception:                                    # noqa: BLE001 (a failed check never stops the pull)
        return False


def _progress(run_id, text_):
    """Show where a background pull is (in erp_sync_runs.feeds), on its own short connection so the screen sees it at once."""
    try:
        with db.engine().begin() as c:
            c.execute(text('UPDATE erp_sync_runs SET feeds=:f WHERE id=:i'), {'f': text_[:200], 'i': run_id})
    except Exception:                                    # noqa: BLE001 (progress text is best-effort)
        pass


def _job(app, run_id, by, spec, dry_run, mode='full', state=None):
    stop = lambda: _stopped(run_id)                             # noqa: E731
    track = spec.get('track') or {}
    with app.app_context():
        g.response_ok = True
        try:
            rows, seen, maxima = [], set(), {}
            source = fetch_changes(spec, state or {}, stop=stop) if mode == 'changes' else \
                fetch_rows(spec['view'], spec.get('filters'), sort_by=_sort_column(spec), stop=stop)
            for raw in source:
                for kind, col in track.items():          # remember the newest date seen: next time start from there
                    v = raw.get(col)
                    if _ts(v) and (kind not in maxima or _ts(v) > _ts(maxima[kind])):
                        maxima[kind] = v
                m = apply_mapping(raw, spec)
                key = m.get('adm_no') or id(raw)
                if key in seen:                          # "changes" reads by two dates: the same student may come twice
                    continue
                seen.add(key)
                rows.append(m)
                if len(rows) % PAGE == 0:
                    _progress(run_id, f'students · downloading: {len(rows):,} rows so far')
            _progress(run_id, f'students · downloaded {len(rows):,} rows; saving')
            erp.upsert_students(rows, trigger='api', by=by, dry_run=dry_run, run_id=run_id, commit_every=500, stop=stop)
            if not dry_run and track:
                _save_state(spec, maxima, full=(mode == 'full'), old=state)
            what = (f"changes since {state['since']}" if (state or {}).get('since') else 'changes since the last pull') if mode == 'changes' else 'all rows'
            _progress(run_id, f'students · {what}: {len(rows):,} rows')
            db.finish(True)
        except Stopped:
            db.finish(False)                                    # the run is already marked Stopped; rows committed so far stay
            app.logger.info('ERP pull run %s stopped', run_id)
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
