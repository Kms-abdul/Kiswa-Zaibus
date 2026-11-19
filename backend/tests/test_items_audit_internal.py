import hashlib
import hmac
import json

import pymysql
import pytest

from conftest import reauth, sign_in, sql


def test_isbn_rules(client):
    sign_in(client, 'KZ-WH-030')
    base = {'company': 'ZE', 'name': 'Test Reader', 'category': 'Book', 'price': 250}
    r = client.post('/api/items', json=dict(base, isbn='978-0-306-40615-6'))
    assert r.status_code == 400 and r.get_json()['error'] == 'bad_isbn'
    r = client.post('/api/items', json=dict(base, isbn='0-306-40615-2'))          # valid ISBN-10, stored as ISBN-13 too
    assert r.status_code == 201, r.get_json()
    iid = r.get_json()['id']
    assert sql('SELECT isbn13 FROM items WHERE id=%s', iid)[0]['isbn13'] == '9780306406157'
    r = client.post('/api/items', json=dict(base, name='Dup', isbn='978-0-306-40615-7'))
    assert r.status_code == 409 and r.get_json()['item'] == iid


def test_price_change_needs_cfo(app):
    im, cfo = app.test_client(), app.test_client()
    sign_in(im, 'KZ-WH-030')
    r = im.post('/api/items/price-change', json={'rows': [{'id': 'KG-MSA-SHT-24', 'price': 365}], 'reason': 'Supplier rate revision'})
    assert r.status_code == 202, r.get_json()
    aid = r.get_json()['id']
    assert sql("SELECT price FROM items WHERE id='KG-MSA-SHT-24'")[0]['price'] == 349
    sign_in(cfo, 'KZ-HO-002')
    reauth(cfo, 'KZ-HO-002')
    r = cfo.post(f'/api/approvals/{aid}/decide', json={'action': 'approve'})
    assert r.status_code == 200, r.get_json()
    assert sql("SELECT price FROM items WHERE id='KG-MSA-SHT-24'")[0]['price'] == 365


def test_audit_chain_verifies_and_is_append_only(client):
    sign_in(client, 'KZ-AUD-001')
    j = client.get('/api/audit/verify').get_json()
    assert j['ok'] and j['checked'] > 10
    with pytest.raises(pymysql.err.OperationalError):
        sql("UPDATE audit_log SET action='tampered' WHERE id=(SELECT MIN(id) FROM (SELECT id FROM audit_log) t)")
    with pytest.raises(pymysql.err.OperationalError):
        sql('DELETE FROM audit_log')


def test_internal_requires_key(client):
    assert client.post('/internal/tick', json={}).status_code == 403
    r = client.post('/internal/tick', json={}, headers={'X-Internal-Key': 'test-internal-key'})
    assert r.status_code == 200 and 'escalated' in r.get_json()


def test_scheduled_resignation_applies(client):
    sql("INSERT INTO access_schedule (user_id, change_type, effective_on, reason, status) VALUES ('u13', 'Resign', CURDATE(), 'Resigned', 'Scheduled')")
    r = client.post('/internal/tick', json={}, headers={'X-Internal-Key': 'test-internal-key'})
    assert r.get_json()['access_changes'] == 1
    assert sql("SELECT status FROM users WHERE id='u13'")[0]['status'] == 'Resigned'


def _evt(eid, event, entity_key, entity):
    return json.dumps({'id': eid, 'event': event, 'payload': {entity_key: {'entity': entity}}})


def test_razorpay_events(client):
    H = {'X-Internal-Key': 'test-internal-key'}
    pay = {'id': 'pay_TEST001', 'amount': 129900, 'method': 'upi', 'vpa': 'parent@okhdfc', 'fee': 0, 'tax': 0, 'contact': '+919848012345',
           'notes': {'ref': 'KG/ONL/2627/0001', 'adm_no': 'MSA260101'}, 'created_at': 1793000000, 'acquirer_data': {'rrn': '612345678901'}}
    body = _evt('evt_1', 'payment.captured', 'payment', pay)
    r = client.post('/internal/razorpay/event', json={'company': 'KG', 'signature_ok': False, 'body': body}, headers=H)
    assert r.get_json()['status'] == 'rejected'
    assert sql("SELECT COUNT(*) n FROM security_events WHERE type='Webhook rejected'")[0]['n'] == 1
    r = client.post('/internal/razorpay/event', json={'company': 'KG', 'signature_ok': True, 'body': _evt('evt_2', 'payment.captured', 'payment', pay)}, headers=H)
    assert r.get_json()['status'] == 'payment captured'
    p = sql("SELECT * FROM rzp_payments WHERE id='pay_TEST001'")[0]
    assert float(p['amount']) == 1299.0 and p['status'] == 'Captured' and p['env'] == 'Test' and p['webhook_verified'] == 1
    r = client.post('/internal/razorpay/event', json={'company': 'KG', 'signature_ok': True, 'body': _evt('evt_2', 'payment.captured', 'payment', pay)}, headers=H)
    assert r.get_json()['status'] == 'duplicate'
    assert sql("SELECT COUNT(*) n FROM rzp_payments WHERE id='pay_TEST001'")[0]['n'] == 1


def test_live_events_wait_for_activation(client):
    sql("UPDATE settings SET svalue=JSON_SET(svalue, '$.env', 'Live') WHERE skey='razorpay'")
    try:
        r = client.post('/internal/razorpay/event', json={'company': 'KG', 'signature_ok': True,
                                                         'body': _evt('evt_9', 'payment.captured', 'payment', {'id': 'pay_LIVE1', 'amount': 100})},
                        headers={'X-Internal-Key': 'test-internal-key'})
        assert r.get_json()['status'] == 'stored'
        assert not sql("SELECT * FROM rzp_payments WHERE id='pay_LIVE1'")
    finally:
        sql("UPDATE settings SET svalue=JSON_SET(svalue, '$.env', 'Test') WHERE skey='razorpay'")


def test_dashboard_and_alerts(client):
    sign_in(client, 'KZ-HO-001')
    d = client.get('/api/dashboard').get_json()
    assert d['stock'] and d['pending_approvals'] is not None
    assert client.get('/api/alerts').status_code == 200
    assert client.get('/api/roles').status_code == 200
    p = client.get('/api/roles/Store Cashier/permissions').get_json()
    assert p['permissions']['pos'] and p['limits']['disc'] == 5


def test_bad_signature_wins_over_duplicate(client):
    H = {'X-Internal-Key': 'test-internal-key'}
    body = _evt('evt_dupbad', 'payment.captured', 'payment', {'id': 'pay_DB1', 'amount': 100, 'created_at': 1793000000})
    assert client.post('/internal/razorpay/event', json={'company': 'KG', 'signature_ok': True, 'body': body}, headers=H).get_json()['status'] == 'payment captured'
    forged = body.replace('"amount": 100', '"amount": 900000')
    assert client.post('/internal/razorpay/event', json={'company': 'KG', 'signature_ok': False, 'body': forged}, headers=H).get_json()['status'] == 'rejected'
    assert float(sql("SELECT amount FROM rzp_payments WHERE id='pay_DB1'")[0]['amount']) == 1.0
