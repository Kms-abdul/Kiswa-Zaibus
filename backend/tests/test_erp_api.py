"""MCB Read-Only API connector: column mapping chosen on screen, paging, background pull, no duplicates, key never exposed.
The API is faked in-process with the same contract (/views, /views/{v}/schema, /views/{v}?skip&limit&sort_by&<col>=, x-api-key)."""
import time

import pytest

from conftest import as_user, ok, sql

SUPER, CASHIER = 'KZ-IT-001', 'KZ-MSA-021'
KEY = 'test-key-not-real-123'
VIEW = 'vw_StudentList'
COLUMNS = ['EnrollmentCode', 'StudentName', 'ClassName', 'SectionName', 'Gender', 'FatherMobile', 'StudentStatus', 'BranchName', 'AcademicYear']


def make_rows(n=1200):
    rows = [{'EnrollmentCode': f'26TST{i:04d}', 'StudentName': f'Test Student {i}', 'ClassName': str(i % 10 + 1), 'SectionName': 'AB'[i % 2],
             'Gender': 'Male' if i % 2 else 'Female', 'FatherMobile': f'98{i:08d}', 'StudentStatus': 'Studying', 'BranchName': 'Test Branch',
             'AcademicYear': '2026-2027'} for i in range(n)]
    if n > 7:
        rows[7]['FatherMobile'] = '12345'                               # one invalid row
    return rows


class FakeResponse:
    def __init__(self, status, data):
        self.status_code, self._data = status, data

    def json(self):
        return self._data


class FakeMCB:
    def __init__(self, rows):
        self.rows, self.calls = rows, []

    def get(self, url, params=None, headers=None, timeout=None):
        path = url.split('/MCB-api', 1)[1]
        self.calls.append((path, dict(params or {})))
        if path == '/health/db':
            return FakeResponse(200, {'database': 'connected'})
        if (headers or {}).get('x-api-key') != KEY:
            return FakeResponse(401, {'detail': 'bad key'})
        if path == '/views':
            return FakeResponse(200, {'views': [VIEW, 'vw_Other']})
        if path == f'/views/{VIEW}/schema':
            return FakeResponse(200, {'view': VIEW, 'columns': [{'column_name': c, 'data_type': 'varchar'} for c in COLUMNS]})
        if path == f'/views/{VIEW}':
            p = dict(params or {})
            skip, limit = int(p.pop('skip', 0)), int(p.pop('limit', 50))
            assert limit <= 500, 'the API caps limit at 500'
            sort_by = p.pop('sort_by', None)
            data = [r for r in self.rows if all(str(r.get(k)) == v for k, v in p.items())]
            if sort_by:
                data = sorted(data, key=lambda r: r[sort_by])
            return FakeResponse(200, data[skip:skip + limit])
        return FakeResponse(404, {'detail': 'not found'})


MAPPING = {'view': VIEW, 'filters': {'AcademicYear': '2026-2027'}, 'fields': {
    'adm_no': {'col': 'EnrollmentCode'}, 'erp_ref': {'same_as': 'adm_no'}, 'name': {'col': 'StudentName'},
    'class_name': {'col': 'ClassName'}, 'section': {'col': 'SectionName'}, 'gender': {'col': 'Gender'},
    'parent_mobile': {'col': 'FatherMobile'}, 'status': {'col': 'StudentStatus', 'map': {'Studying': 'Active'}},
    'school_id': {'const': 'MSEA'}, 'campus': {'col': 'EnrollmentCode', 'regex': r'^\d{2}([A-Z]+)\d+$'}, 'location': {'col': 'BranchName'},
    'academic_year': {'col': 'AcademicYear'}}}


@pytest.fixture()
def fake(monkeypatch):
    from kz.config import Config
    from kz.services import erp_api
    f = FakeMCB(make_rows())
    monkeypatch.setattr(Config, 'ERP_API_URL', 'https://mcb.example.test/MCB-api')
    monkeypatch.setattr(Config, 'ERP_API_KEY', KEY)
    monkeypatch.setattr(Config, 'ERP_API_KEY_HEADER', 'x-api-key')
    monkeypatch.setattr(erp_api.requests, 'get', f.get)
    monkeypatch.setattr(erp_api, 'PAUSE', 0)
    monkeypatch.setattr(erp_api, 'RETRIES', (0.01, 0.01))
    return f


def wait_run(no, timeout=180):
    end = time.time() + timeout
    while time.time() < end:
        st = sql('SELECT status, created, updated, skipped, failed, dry_run FROM erp_sync_runs WHERE no=%s', no)[0]
        if st['status'] != 'Running':
            return st
        time.sleep(0.5)
    raise AssertionError(f'run {no} did not finish')


def test_mapping_transforms_and_paging(app, fake):
    from kz.services import erp_api
    clean = erp_api.validate_mapping(MAPPING, [{'name': c} for c in COLUMNS])
    m = erp_api.apply_mapping({**make_rows(1)[0], 'StudentStatus': 'studying'}, clean)
    assert m == {'adm_no': '26TST0000', 'erp_ref': '26TST0000', 'name': 'Test Student 0', 'class_name': '1', 'section': 'A', 'gender': 'Female',
                 'parent_mobile': '9800000000', 'status': 'Active', 'school_id': 'MSEA', 'campus': 'TST', 'location': 'Test Branch',
                 'academic_year': '2026-2027'}
    with app.app_context():
        rows = list(erp_api.fetch_rows(VIEW, {'AcademicYear': '2026-2027'}, sort_by='EnrollmentCode'))
    assert len(rows) == 1200
    pages = [p for path, p in fake.calls if path == f'/views/{VIEW}']
    assert [p['limit'] for p in pages] == [500, 500, 500] and [p['skip'] for p in pages] == [0, 500, 1000]   # 500 + 500 + 200 (short page)


def test_mapping_rules_and_save(app, fake):
    s = as_user(app, SUPER)
    st = ok(s.get('/api/erp/api/status'))
    assert st['configured'] and st['key_set'] and st['reachable'] and KEY not in str(st)
    assert ok(s.get('/api/erp/api/views')) == ['vw_Other', VIEW]
    cols = ok(s.get(f'/api/erp/api/views/{VIEW}/schema'))
    assert [c['name'] for c in cols] == COLUMNS
    bad_col = dict(MAPPING, fields=dict(MAPPING['fields'], name={'col': 'NoSuchColumn'}))
    assert s.put('/api/erp/api/mapping', json={'mapping': bad_col}).get_json()['error'] == 'unknown_column'
    missing = dict(MAPPING, fields={k: v for k, v in MAPPING['fields'].items() if k != 'class_name'})
    assert s.put('/api/erp/api/mapping', json={'mapping': missing}).get_json()['error'] == 'mapping_incomplete'
    loop = dict(MAPPING, fields=dict(MAPPING['fields'], adm_no={'same_as': 'erp_ref'}))
    assert s.put('/api/erp/api/mapping', json={'mapping': loop}).get_json()['error'] == 'bad_mapping'
    pv = ok(s.post('/api/erp/api/preview', json={'mapping': MAPPING}))
    assert len(pv['rows']) == 20 and pv['rows'][7]['problem'] == 'parent mobile is not a valid 10-digit number'
    saved = ok(s.put('/api/erp/api/mapping', json={'mapping': MAPPING}))['mapping']
    assert saved['fields']['status'] == {'col': 'StudentStatus', 'map': {'Studying': 'Active'}}
    assert sql("SELECT COUNT(*) n FROM audit_log WHERE action='ERP mapping changed'")[0]['n'] >= 1
    assert not sql('SELECT COUNT(*) n FROM settings WHERE svalue LIKE %s', f'%{KEY}%')[0]['n']


def test_trial_then_real_pull_never_duplicates(app, fake):
    s = as_user(app, SUPER)
    ok(s.put('/api/erp/api/mapping', json={'mapping': MAPPING}))
    before = sql("SELECT COUNT(*) n FROM students WHERE adm_no LIKE '26TST%%'")[0]['n']
    trial = ok(s.post('/api/erp/sync', json={'dry_run': True}))
    assert trial['status'] == 'Running'
    st = wait_run(trial['run'])
    assert st['dry_run'] == 1 and st['created'] == 1199 and st['failed'] == 1
    assert sql("SELECT COUNT(*) n FROM students WHERE adm_no LIKE '26TST%%'")[0]['n'] == before          # trial saved nothing

    real = wait_run(ok(s.post('/api/erp/sync', json={}))['run'])
    assert (real['created'], real['failed']) == (1199, 1)
    assert sql("SELECT COUNT(*) n FROM students WHERE adm_no LIKE '26TST%%'")[0]['n'] == 1199
    row = sql("SELECT name, status, campus, location, school_id, gender, parent_mobile FROM students WHERE adm_no='26TST0011'")[0]
    assert row == {'name': 'Test Student 11', 'status': 'Active', 'campus': 'TST', 'location': 'Test Branch', 'school_id': 'MSEA', 'gender': 'M',
                   'parent_mobile': '9800000011'}

    again = wait_run(ok(s.post('/api/erp/sync', json={}))['run'])
    assert (again['created'], again['updated'], again['skipped']) == (0, 0, 1199)                        # pulling again never duplicates

    fake.rows[11]['StudentStatus'] = 'Left'
    changed = wait_run(ok(s.post('/api/erp/sync', json={}))['run'])
    assert (changed['updated'], changed['skipped']) == (1, 1198)
    assert sql("SELECT status FROM students WHERE adm_no='26TST0011'")[0]['status'] == 'Left'
    assert not sql('SELECT COUNT(*) n FROM erp_sync_records WHERE snapshot LIKE %s OR error LIKE %s', f'%{KEY}%', f'%{KEY}%')[0]['n']
    assert not sql('SELECT COUNT(*) n FROM audit_log WHERE new_value LIKE %s OR old_value LIKE %s', f'%{KEY}%', f'%{KEY}%')[0]['n']


def test_wrong_key_marks_run_failed_and_permissions(app, fake, monkeypatch):
    from kz.config import Config
    s = as_user(app, SUPER)
    ok(s.put('/api/erp/api/mapping', json={'mapping': MAPPING}))
    monkeypatch.setattr(Config, 'ERP_API_KEY', 'wrong-key')
    st = wait_run(ok(s.post('/api/erp/sync', json={}))['run'])
    assert st['status'] == 'Failed'
    assert 'refused the key' in sql("SELECT error FROM erp_sync_records WHERE source_ref='RUN' ORDER BY id DESC LIMIT 1")[0]['error']
    c = as_user(app, CASHIER)
    assert c.put('/api/erp/api/mapping', json={'mapping': MAPPING}).status_code == 403
    assert c.post('/api/erp/sync', json={}).status_code == 403
    assert c.get('/api/erp/api/status').status_code == 403


def test_retry_long_values_and_stop(app, fake, monkeypatch):
    from kz.services import erp_api
    s = as_user(app, SUPER)
    # a server hiccup on one page is retried instead of failing the run
    real_get, state = fake.get, {'n': 0}
    def flaky(url, params=None, headers=None, timeout=None):
        if url.endswith(f'/views/{VIEW}') and (params or {}).get('skip') == 500 and state['n'] == 0:
            state['n'] += 1
            raise erp_api.requests.ConnectionError('refused')
        return real_get(url, params=params, headers=headers, timeout=timeout)
    monkeypatch.setattr(erp_api.requests, 'get', flaky)
    with app.app_context():
        assert len(list(erp_api.fetch_rows(VIEW, sort_by='EnrollmentCode'))) == 1200 and state['n'] == 1
    # a value longer than its column fails that row only, the rest is imported
    fake.rows[3]['SectionName'] = 'A section name that is far too long'
    ok(s.put('/api/erp/api/mapping', json={'mapping': MAPPING}))
    st = wait_run(ok(s.post('/api/erp/sync', json={'dry_run': True}))['run'])
    assert st['status'] == 'Completed with errors' and st['failed'] == 2
    assert sql("SELECT COUNT(*) n FROM erp_sync_records WHERE error LIKE 'section is longer than 30 characters%%'")[0]['n'] >= 1
    # Stop: a running pull is marked Stopped, and a stale "Running" row no longer blocks the next pull
    rid = sql("SELECT id FROM erp_sync_runs ORDER BY id DESC LIMIT 1")[0]['id']
    sql("UPDATE erp_sync_runs SET status='Running' WHERE id=%s", rid)
    assert s.post('/api/erp/sync', json={}).get_json()['error'] == 'erp_running'
    r = ok(s.post(f'/api/erp/runs/{rid}/stop', json={}))
    assert r['status'] == 'Stopped'
    assert s.post(f'/api/erp/runs/{rid}/stop', json={}).get_json()['error'] == 'not_running'
    wait_run(ok(s.post('/api/erp/sync', json={'dry_run': True}))['run'])
