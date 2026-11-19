"""Dashboard 'Your work' (2.5): each user sees only work they can act on, every item links to a screen they may open, and the counts
agree with the screens (approvals they can decide, audits they count, transfers to receive)."""
from conftest import as_user, ok, sql

ROLES = ['KZ-IT-001', 'KZ-HO-001', 'KZ-HO-002', 'KZ-AUD-001', 'KZ-ABD-010', 'KZ-MSA-020', 'KZ-MSA-021', 'KZ-WH-030', 'MSEA-OFF-004', 'MSEA-CLK-001']
SCREEN = {'/invoices': ('view', 'pos'), '/stock': ('view', 'inv'), '/approvals': ('view', 'appr'), '/pos': ('add', 'pos'), '/stock-audits': ('view', 'saudit'), '/transfers': ('view', 'inv'), '/purchasing': ('view', 'pur'),
          '/cash': ('view', 'cash'), '/online': ('view', 'onl'), '/finance': ('view', 'fin'), '/reports': ('view', 'rep')}


def test_needs_sign_in(app):
    assert app.test_client().get('/api/my-work').status_code == 401


def test_items_are_actionable_and_linked(app):
    for emp in ROLES:
        c = as_user(app, emp)
        me = ok(c.get('/api/auth/me'))
        work = ok(c.get('/api/my-work'))
        assert isinstance(work, list)
        for w in work:
            assert w['count'] > 0 and w['label'] and w['to'].startswith('/'), (emp, w)
            base = w['to'].split('?')[0]
            a, k = SCREEN[base]
            assert a in me['permissions'].get(k, []), (emp, w, 'links to a screen the user cannot open')


def test_approval_count_matches_inbox(app):
    for emp in ('KZ-HO-001', 'KZ-HO-002', 'KZ-ABD-010'):
        c = as_user(app, emp)
        if c.get('/api/approvals').status_code == 403:
            continue
        mine = len(ok(c.get('/api/approvals?mine=1')))
        got = {w['key']: w['count'] for w in ok(c.get('/api/my-work'))}
        assert got.get('approvals', 0) == mine, (emp, got, mine)


def test_audit_counting_matches_assignment(app):
    c = as_user(app, 'KZ-AUD-001')
    uid = sql("SELECT id FROM users WHERE emp_id='KZ-AUD-001'")[0]['id']
    n = sql("SELECT COUNT(*) n FROM stock_audits WHERE status IN ('Draft','In Progress') AND JSON_CONTAINS(auditors, JSON_QUOTE(%s))", uid)[0]['n']
    got = {w['key']: w['count'] for w in ok(c.get('/api/my-work'))}
    assert got.get('count', 0) == n
