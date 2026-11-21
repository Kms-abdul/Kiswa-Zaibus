from conftest import as_user, books_balance, ok, sql, stock_consistent


def test_seed_loaded_and_books_balance(app):
    assert sql('SELECT COUNT(*) n FROM items')[0]['n'] == 427
    for co, (d, c) in books_balance().items():
        assert d == c, co
    assert stock_consistent() == ()


def test_health(client):
    j = ok(client.get('/api/health'))
    assert j['migration'] == '0008'


def test_director_org(app):
    c = as_user(app, 'KZ-HO-001')
    org = ok(c.get('/api/org'))
    assert {x['code'] for x in org['companies']} == {'KG', 'ZE'}
