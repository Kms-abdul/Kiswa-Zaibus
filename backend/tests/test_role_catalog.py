"""Dynamic roles (migration 0007): the permission catalogue, modes, display names, legacy keys and role deactivation."""
from conftest import as_user, ok, sql

DIRECTOR, SUPER = 'KZ-HO-001', 'KZ-IT-001'
SPARE = 'KZ-WH-033'          # a seeded user no other test signs in as


def approve_latest(app, who=DIRECTOR):
    aid = sql("SELECT id FROM approvals WHERE status='Pending' ORDER BY id DESC LIMIT 1")[0]['id']
    return ok(as_user(app, who).post(f'/api/approvals/{aid}/decide', json={'action': 'approve', 'remarks': 'ok'}))


def perms_of(role):
    return {(x['screen_key'], x['action']) for x in sql('SELECT screen_key, action FROM permissions WHERE role=%s', role)}


def test_catalog_shape_and_sync(app):
    from kz.core import permission_catalog as pc
    cat = ok(as_user(app, SUPER).get('/api/setup/catalog'))['permissions']
    assert [m['key'] for m in cat['modules']] == [m for m, _ in pc.MODULES]
    keys = {s['key'] for s in cat['screens']}
    assert {'pur', 'inv', 'ctl.users', 'ctl.roles', 'ctl.sec'} <= keys and 'dash' not in keys
    assert [m['key'] for m in cat['modes']] == ['none', 'read', 'write', 'full']
    assert {e['key'] for e in cat['extras']} == set(pc.EXTRAS)
    pur = next(s for s in cat['screens'] if s['key'] == 'pur')
    assert 'print' not in pur['actions'] and 'master' in pur['extras']          # only what the code checks is offered
    rows = sql('SELECT `key` FROM perm_screens WHERE active=1')
    assert {r['key'] for r in rows} == set(pc.screen_keys())                       # synced by the migration / start-up


def test_new_screen_in_code_appears_after_sync(app):
    from kz import db
    from kz.core import permission_catalog as pc
    saved = list(pc.SCREENS)
    try:
        pc.SCREENS.append(('demo.new', 'stock', 'Demo screen', ('view', 'add'), ()))
        with db.engine().begin() as conn:
            pc.sync_catalog(conn)
        assert sql("SELECT active, label FROM perm_screens WHERE `key`='demo.new'")[0]['active'] == 1
        cat = ok(as_user(app, SUPER).get('/api/setup/catalog'))['permissions']
        assert any(s['key'] == 'demo.new' and s['module'] == 'stock' for s in cat['screens'])
    finally:
        pc.SCREENS[:] = saved
        with db.engine().begin() as conn:
            pc.sync_catalog(conn)
    assert sql("SELECT active FROM perm_screens WHERE `key`='demo.new'")[0]['active'] == 0   # removed from code -> switched off


def test_read_mode_role_is_enforced(app):
    s = as_user(app, SUPER)
    r = s.post('/api/setup/roles', json={'name': 'Purchase Viewer', 'display_name': 'Purchase viewer (read)', 'reason': 'test',
                                         'permissions': {'pur': ['view', 'print']}, 'limits': {}})
    assert r.status_code == 202                                                      # maker-checker is on in the seed
    approve_latest(app)
    assert perms_of('Purchase Viewer') == {('pur', 'view')}                         # 'print' is not checked on pur: dropped
    assert sql("SELECT display_name, is_custom FROM roles WHERE name='Purchase Viewer'")[0] == {'display_name': 'Purchase viewer (read)', 'is_custom': 1}
    uid = sql('SELECT id FROM users WHERE emp_id=%s', SPARE)[0]['id']
    sql("UPDATE users SET main_role='Purchase Viewer', all_branches=1 WHERE id=%s", uid)
    sql('DELETE FROM user_roles WHERE user_id=%s', uid)
    sql("INSERT IGNORE INTO user_companies (user_id, company) VALUES (%s, 'KG')", uid)
    v = as_user(app, SPARE)
    me = ok(v.get('/api/auth/me'))
    assert me['role'] == 'Purchase Viewer' and me['role_label'] == 'Purchase viewer (read)'
    assert v.get('/api/purchasing/orders').status_code == 200
    assert v.post('/api/purchasing/orders', json={'branch_id': 'KG-ABD', 'lines': []}).status_code == 403
    assert v.get('/api/setup/roles').status_code == 403


def test_custom_actions_keep_legacy_keys(app):
    s = as_user(app, SUPER)
    # a role shaped like the seed's finance roles: real grants plus keys no code checks (dash, ctl.dash, ctl.flow)
    role = 'Legacy Holder'
    sql("INSERT INTO roles (name, approval_rank, read_only, is_custom, active) VALUES (%s, 0, 0, 1, 1)", role)
    for key, act in (('inv', 'view'), ('dash', 'view'), ('ctl.dash', 'view'), ('ctl.flow', 'view'), ('pos', 'submit')):
        sql('INSERT INTO permissions (role, screen_key, action) VALUES (%s, %s, %s)', role, key, act)
    legacy_before = {p for p in perms_of(role) if p[0] in ('dash', 'ctl.dash', 'ctl.scope', 'ctl.flow', 'ctl.alerts', 'ctl.sys')}
    assert len(legacy_before) == 3
    cur = next(r for r in ok(s.get('/api/setup/roles')) if r['name'] == role)
    body = dict(cur, reason='custom test')
    body['permissions'] = dict(cur['permissions'], inv=['view', 'cancel', 'cost'])    # a Custom mix plus an extra
    r = s.put(f'/api/setup/roles/{role}', json=body)
    assert r.status_code in (200, 202), r.get_json()                                # used to fail: "Unknown screen ctl.dash"
    if r.status_code == 202:
        approve_latest(app)
    after = perms_of(role)
    assert {a for k, a in after if k == 'inv'} == {'view', 'cancel', 'cost'}
    assert ('pos', 'submit') not in after                                           # pos never checks 'submit': dropped on save
    assert {p for p in after if p[0] in ('dash', 'ctl.dash', 'ctl.scope', 'ctl.flow', 'ctl.alerts', 'ctl.sys')} == legacy_before


def test_unknown_screen_and_deactivate_rules(app):
    s = as_user(app, SUPER)
    assert s.put('/api/setup/roles/Store Cashier', json={'permissions': {'nope': ['view']}, 'reason': 'x'}).get_json()['error'] == 'invalid'
    r = s.delete('/api/setup/roles/Store Cashier', json={'reason': 'x'})
    assert r.status_code == 409 and r.get_json()['error'] == 'role_in_use'
    assert s.delete('/api/setup/roles/Super Admin', json={'reason': 'x'}).get_json()['error'] == 'protected_role'
    s.post('/api/setup/roles', json={'name': 'Temp Role', 'reason': 'test', 'permissions': {'rep': ['view']}})
    approve_latest(app)
    assert s.delete('/api/setup/roles/Temp Role', json={'reason': 'not needed'}).status_code in (200, 202)
    if sql("SELECT COUNT(*) n FROM approvals WHERE status='Pending' AND title LIKE %s", '%Temp Role%')[0]['n']:
        approve_latest(app)
    assert sql("SELECT active FROM roles WHERE name='Temp Role'")[0]['active'] == 0
