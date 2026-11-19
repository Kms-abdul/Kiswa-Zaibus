"""Super Admin has every right (2.7): every screen and action, every report including financial statements, the highest limits,
finance closing and reopening, stock-audit approval — while maker-checker still stops anyone approving their own request."""
from conftest import as_user, ok, sql

SA = 'KZ-IT-001'


def test_every_screen_every_action(app):
    from kz.api.setup import ACTIONS, SCREENS
    from kz.core.rbac import ALL_ACTIONS, ALL_SCREENS
    assert set(ACTIONS) <= set(ALL_ACTIONS) and {k for k, _ in SCREENS} <= set(ALL_SCREENS)   # the lists cannot drift apart
    me = ok(as_user(app, SA).get('/api/auth/me'))
    for k, _ in SCREENS:
        assert set(me['permissions'].get(k, [])) >= set(ACTIONS), k


def test_every_module_and_report_opens(app):
    c = as_user(app, SA)
    rules = [r.rule for r in app.url_map.iter_rules() if 'GET' in r.methods and '<' not in r.rule and r.rule.startswith('/api/')]
    refused = [r for r in rules if c.get(r).status_code == 403]
    assert len(rules) > 80 and not refused, refused                      # no screen or list in the API is closed to the Super Admin
    lib = ok(c.get('/api/reports/library'))
    from kz.services.op_reports import REPORTS
    assert {x['key'] for x in lib['reports']} == set(REPORTS) and set(lib['formats']) == {'print', 'pdf', 'xlsx', 'csv'}
    for k in ('trial-balance', 'balance-sheet', 'stock-valuation'):
        d = ok(c.get(f'/api/reports/library/{k}?company=KG'))
        if k == 'stock-valuation':
            assert 'Cost value' in [x['h'] for x in d['columns']]
    roles = ok(c.get('/api/setup/roles'))
    sa = next(r for r in roles if r['name'] == 'Super Admin')
    assert sa['all_rights'] and all(len(v) >= 20 for v in sa['permissions'].values())


def test_highest_limits_and_finance_powers(app):
    from flask import g
    from kz.core import approvals as appr
    from kz.core.rbac import is_super, limit, load_user
    from kz.services import fin_vouchers as fv
    from kz.services import fin_yearend as ye
    uid = sql('SELECT id FROM users WHERE emp_id=%s', SA)[0]['id']
    with app.app_context():
        u = load_user(uid)
        g.user = u
        assert is_super(u) and not u['read_only']
        for k in ('disc', 'po', 'pay', 'refund', 'adj', 'jv', 'cash'):
            top = sql('SELECT MAX(amount) a FROM approval_limits WHERE limit_key=%s', k)[0]['a']
            assert limit(u, k) == float(top or 0) and appr.user_limit(u, k) == float(top or 0), k
        assert u['rank'] == max(float(r['approval_rank']) for r in sql('SELECT approval_rank FROM roles WHERE active=1'))
        assert fv.is_closer(u) and ye._may_reopen(u)


def test_approves_others_but_not_own(app):
    c = as_user(app, SA)
    pend = [a for a in sql("SELECT id, requested_by FROM approvals WHERE status='Pending'")]
    me = sql('SELECT id FROM users WHERE emp_id=%s', SA)[0]['id']
    mine = {a['id'] for a in ok(c.get('/api/approvals?mine=1'))}
    for a in pend:
        assert (a['id'] in mine) == (a['requested_by'] != me), a
