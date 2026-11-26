"""End-to-end business flows. After every flow the books must balance and stock must equal its ledger."""
import pytest

from conftest import as_user, books_balance, ok, sql, stock_consistent

CASHIER, INCHARGE, BM, DIRECTOR, CFO, INV_MGR, MSEA_CO, CLERK = ('KZ-MSA-021', 'KZ-MSA-020', 'KZ-ABD-010', 'KZ-HO-001', 'KZ-HO-002',
                                                                 'KZ-WH-030', 'MSEA-OFF-004', 'MSEA-CLK-001')
STUDENT = 'MSA260104'


@pytest.fixture(autouse=True)
def invariants(app):
    yield
    for co, (d, c) in books_balance().items():
        assert d == c, f'{co} books do not balance: {d} vs {c}'
    assert stock_consistent() == (), 'stock_balance differs from stock_ledger'


def attach(app, ref, who=DIRECTOR):
    import io
    c = as_user(app, who)
    r = c.post('/api/evidence', data={'ref': ref, 'kind': 'Voucher', 'file': (io.BytesIO(f'evidence for {ref}'.encode()), 'proof.pdf')},
               content_type='multipart/form-data')
    assert r.status_code == 201, r.get_json()


def approve(app, no_or_id, who=DIRECTOR, action='approve', remarks='ok'):
    c = as_user(app, who)
    aid = no_or_id if isinstance(no_or_id, int) else sql('SELECT id FROM approvals WHERE no=%s', no_or_id)[0]['id']
    return c.post(f'/api/approvals/{aid}/decide', json={'action': action, 'remarks': remarks})


def open_shift(c, loc='MSA'):
    r = c.post('/api/pos/shift/open', json={'location': loc, 'opening': {'KG': 1000, 'ZE': 500}})
    if r.status_code == 409:
        return ok(c.get('/api/pos/shift'))
    return ok(r, 201)


# ---------------------------------------------------------------- POS
def test_cash_sale_needs_shift_and_posts_books(app):
    c = as_user(app, CASHIER)
    sale = {'branch_id': 'KG-MSA', 'adm_no': STUDENT, 'lines': [{'item_id': 'KG-MSA-SHT-24', 'qty': 2}], 'payments': [{'mode': 'cash', 'amount': 698}]}
    r = c.post('/api/pos/invoices', json=sale)
    assert r.status_code == 409 and r.get_json()['error'] == 'shift_required'
    open_shift(c)
    before = sql("SELECT qty FROM stock_balance WHERE branch_id='KG-MSA' AND item_id='KG-MSA-SHT-24'")[0]['qty']
    inv = ok(c.post('/api/pos/invoices', json=sale), 201)
    assert inv['no'].startswith('KG/INV/MSC/') and inv['total'] == 698.0 and inv['cgst'] + inv['sgst'] > 0
    assert sql("SELECT qty FROM stock_balance WHERE branch_id='KG-MSA' AND item_id='KG-MSA-SHT-24'")[0]['qty'] == before - 2
    j = sql('SELECT l.account, l.debit, l.credit FROM journal_lines l JOIN journals j ON j.id=l.journal_id WHERE j.ref=%s', inv['no'])
    accts = {x['account'] for x in j}
    assert {'1100', '4000', '2100', '2110', '5000', '1300'} <= accts


def test_sale_validations(app):
    c = as_user(app, CASHIER)
    open_shift(c)
    base = {'branch_id': 'KG-MSA', 'lines': [{'item_id': 'KG-MSA-SHT-24', 'qty': 1}], 'payments': [{'mode': 'cash', 'amount': 349}]}
    assert c.post('/api/pos/invoices', json=base).get_json()['error'] == 'student_required'
    assert c.post('/api/pos/invoices', json=dict(base, adm_no='MSA260101')).get_json()['error'] == 'student_blocked'
    assert c.post('/api/pos/invoices', json=dict(base, adm_no=STUDENT, payments=[{'mode': 'cash', 'amount': 300}])).get_json()['error'] == 'payment_mismatch'
    assert c.post('/api/pos/invoices', json=dict(base, adm_no=STUDENT, payments=[{'mode': 'upi', 'amount': 349}])).get_json()['error'] == 'payment_ref_required'
    big = dict(base, adm_no=STUDENT, lines=[{'item_id': 'KG-MSA-SHT-24', 'qty': 999}], payments=[{'mode': 'upi', 'amount': 348651, 'ref': 'U1'}])
    assert c.post('/api/pos/invoices', json=big).get_json()['error'] == 'negative_stock'
    assert c.post('/api/pos/invoices', json=dict(base, branch_id='KG-ABD', adm_no=STUDENT)).status_code == 403     # outside scope
    assert c.post('/api/pos/invoices', json=dict(base, branch_id='KG-WH', adm_no=STUDENT)).get_json()['error'] == 'branch_closed'


def test_discount_above_limit_is_held_then_approved(app):
    c = as_user(app, CASHIER)
    open_shift(c)
    sale = {'branch_id': 'KG-MSA', 'adm_no': STUDENT, 'lines': [{'item_id': 'KG-MSA-TRS-24', 'qty': 1, 'disc_pct': 8}],
            'payments': [{'mode': 'upi', 'amount': 413, 'ref': 'UPI123'}]}
    q = ok(c.post('/api/pos/quote', json={'company': 'KG', 'lines': sale['lines']}))
    sale['payments'][0]['amount'] = q['totals']['total']
    r = ok(c.post('/api/pos/invoices', json=sale), 202)
    assert r['held'] and r['approval']['required_role'] in ('Store In-Charge', 'Branch Manager')
    res = ok(approve(app, r['approval']['no'], INCHARGE))
    assert res['status'] == 'Approved' and 'Invoice' in res['result']
    assert sql('SELECT status FROM held_bills WHERE no=%s', r['hold_no'])[0]['status'] == 'Released'


def test_return_within_and_above_limit(app):
    c = as_user(app, CASHIER)
    open_shift(c)
    inv = ok(c.post('/api/pos/invoices', json={'branch_id': 'KG-MSA', 'adm_no': STUDENT, 'lines': [{'item_id': 'KG-MSA-TIE-Std', 'qty': 2}],
                                               'payments': [{'mode': 'cash', 'amount': 198}]}), 201)
    r = c.post(f"/api/pos/invoices/{inv['id']}/return", json={'lines': [{'line_no': 1, 'qty': 1}], 'refund_mode': 'cash', 'reason': 'Wrong size'})
    j = ok(r, 202)                                                   # cashier's refund limit is 0 -> In-Charge approves
    assert j['approval']['required_role'] == 'Store In-Charge'
    ok(approve(app, j['approval']['no'], INCHARGE))
    assert sql('SELECT status FROM invoices WHERE id=%s', inv['id'])[0]['status'] == 'Partly returned'
    ic = as_user(app, INCHARGE)
    open_shift(ic)
    r = ok(ic.post(f"/api/pos/invoices/{inv['id']}/return", json={'lines': [{'line_no': 1, 'qty': 1}], 'refund_mode': 'cash', 'reason': 'Damaged',
                                                                   'condition': 'Damaged'}), 201)
    assert r['credit_note'].startswith('KG/CN/')
    assert sql('SELECT status FROM invoices WHERE id=%s', inv['id'])[0]['status'] == 'Returned'
    assert ic.post(f"/api/pos/invoices/{inv['id']}/return", json={'lines': [{'line_no': 1, 'qty': 1}], 'reason': 'x'}).get_json()['error'] == 'bad_quantity'


def test_pending_delivery(app):
    c = as_user(app, CASHIER)
    open_shift(c)
    sale = {'branch_id': 'KG-MSA', 'adm_no': STUDENT, 'lines': [{'item_id': 'KG-MSA-SHT-24', 'qty': 2, 'delivered_qty': 1}], 'payments': [{'mode': 'cash', 'amount': 698}]}
    assert c.post('/api/pos/invoices', json=sale).get_json()['error'] == 'pending_reason_required'
    inv = ok(c.post('/api/pos/invoices', json=dict(sale, pending_reason='Size 24 out at counter')), 201)
    pend = ok(c.get('/api/pos/invoices?pending=1'))
    assert any(x['id'] == inv['id'] for x in pend)
    ok(c.post(f"/api/pos/invoices/{inv['id']}/deliver", json={'lines': [{'line_no': 1, 'qty': 1}]}))
    assert sql('SELECT delivered_qty FROM invoice_lines WHERE invoice_id=%s', inv['id'])[0]['delivered_qty'] == 2


def test_shift_close_with_difference(app):
    c = as_user(app, CASHIER)
    sh = open_shift(c)
    live = ok(c.get('/api/pos/shift'))['live']
    exp = live['KG']['expected']
    r = c.post(f"/api/pos/shift/{sh['id']}/close", json={'counted': {'KG': exp - 10, 'ZE': live['ZE']['expected']}})
    assert r.get_json()['error'] == 'reason_required'
    j = ok(c.post(f"/api/pos/shift/{sh['id']}/close", json={'counted': {'KG': exp - 10, 'ZE': live['ZE']['expected']}, 'reason': 'Short by 10'}))
    assert j['approval'] and j['shift']['status'] == 'Pending approval'
    ok(approve(app, j['approval']['no'], INCHARGE))
    assert sql('SELECT status FROM cash_shifts WHERE id=%s', sh['id'])[0]['status'] == 'Closed'
    assert sql("SELECT COUNT(*) n FROM journals WHERE ref=%s AND type='Cash difference'", sh['no'])[0]['n'] == 1


# ---------------------------------------------------------------- inventory
def test_transfer_flow(app):
    im = as_user(app, INV_MGR)
    t = ok(im.post('/api/inventory/transfers', json={'from_branch': 'KG-MYJ', 'to_branch': 'KG-MSA', 'reason': 'Top up counter',
                                                     'lines': [{'item_id': 'KG-MSA-SHT-24', 'qty': 20}, {'item_id': 'KG-MSA-TIE-Std', 'qty': 10}]}), 201)
    assert t['status'] == 'Pending approval'                     # Inventory Manager transfer limit is 0
    assert im.post(f"/api/inventory/transfers/{t['id']}/dispatch").get_json()['error'] == 'wrong_status'
    ok(approve(app, t['approval']['no'], BM if False else DIRECTOR))
    ok(im.post(f"/api/inventory/transfers/{t['id']}/dispatch"))
    r = ok(im.post(f"/api/inventory/transfers/{t['id']}/receive", json={'lines': [{'line_no': 1, 'received_qty': 19, 'damaged_qty': 1}]}))
    assert 'short' in r['note'] and 'damaged' in r['note']
    assert sql("SELECT COUNT(*) n FROM alerts WHERE alert_type='Transfer shortage'")[0]['n'] >= 1


def test_adjustment_physical_count(app):
    bm = as_user(app, BM)
    qty = sql("SELECT qty FROM stock_balance WHERE branch_id='KG-ABD' AND item_id='KG-MSA-TRS-24'")[0]['qty']
    a = ok(bm.post('/api/inventory/adjustments', json={'branch_id': 'KG-ABD', 'adj_type': 'Physical count', 'reason': 'Monthly count',
                                                       'lines': [{'item_id': 'KG-MSA-TRS-24', 'counted_qty': qty - 1}]}), 201)
    assert a['status'] == 'Posted'                               # 1 trouser at cost is within the Branch Manager's Rs 5,000
    big = ok(bm.post('/api/inventory/adjustments', json={'branch_id': 'KG-ABD', 'adj_type': 'Damaged', 'reason': 'Water damage',
                                                         'lines': [{'item_id': 'KG-MSA-TRS-24', 'qty': 30}]}), 201)
    assert big['status'] == 'Pending approval'
    assert approve(app, big['approval']['no'], CFO).get_json()['error'] == 'evidence_required'
    attach(app, big['no'], BM)
    ok(approve(app, big['approval']['no'], CFO))
    assert sql('SELECT status FROM stock_adjustments WHERE id=%s', big['id'])[0]['status'] == 'Posted'


# ---------------------------------------------------------------- MSEA sponsored issue
def test_sponsored_voucher_issue(app):
    mc = as_user(app, MSEA_CO)
    v = ok(mc.post('/api/msea/vouchers', json={'adm_no': 'MSA260116', 'company': 'KG', 'scope': 'Uniform', 'max_value': 2000,
                                               'entitlement': {'Shirt': 2, 'Tie': 1}}), 201)
    regular = sql("SELECT adm_no FROM students WHERE category='Regular' AND status='Active' AND adm_no NOT IN ('MSA260104', 'MSA260116') ORDER BY adm_no DESC LIMIT 1")[0]['adm_no']
    rv = ok(mc.post('/api/msea/vouchers', json={'adm_no': regular, 'company': 'ZE', 'max_value': 100, 'entitlement': {'Books': 1}}), 201)
    assert rv['no']                                                     # a Regular student qualifies (full fee paid -> school voucher)
    sql("UPDATE students SET status='Left' WHERE adm_no=%s", 'MSA260104')
    try:
        assert mc.post('/api/msea/vouchers', json={'adm_no': 'MSA260104', 'company': 'KG', 'max_value': 100,
                                                   'entitlement': {'Shirt': 1}}).get_json()['error'] == 'student_not_active'
    finally:
        sql("UPDATE students SET status='Active' WHERE adm_no=%s", 'MSA260104')
    ok(approve(app, v['approval']['no'], CFO))
    c = as_user(app, CASHIER)
    open_shift(c)
    over = c.post('/api/pos/invoices', json={'branch_id': 'KG-MSA', 'voucher_no': v['no'], 'lines': [{'item_id': 'KG-MSA-SHT-24', 'qty': 3}]})
    assert over.get_json()['error'] == 'voucher_check_failed'
    inv = ok(c.post('/api/pos/invoices', json={'branch_id': 'KG-MSA', 'voucher_no': v['no'], 'lines': [{'item_id': 'KG-MSA-SHT-24', 'qty': 2}]}), 201)
    assert inv['channel'] == 'sponsored' and inv['issue_no'] and inv['balance'] == inv['total']
    assert sql('SELECT status FROM msea_vouchers WHERE no=%s', v['no'])[0]['status'] == 'Partly used'
    bill = ok(as_user(app, CFO).get('/api/msea/billing'))
    assert any(b['company'] == 'KG' and b['outstanding'] >= inv['total'] for b in bill)
    # MSEA pays: receipt clears the sponsored invoice
    r = ok(as_user(app, CFO).post('/api/accounts/receipts', json={'company': 'KG', 'customer_id': 'KG-MSEA', 'amount': inv['total'], 'mode': 'bank', 'utr': 'UTR9'}), 201)
    assert r['applied'][0][0] == inv['no']
    assert sql('SELECT balance FROM invoices WHERE id=%s', inv['id'])[0]['balance'] == 0


# ---------------------------------------------------------------- purchasing
def test_purchase_to_payment(app):
    im = as_user(app, INV_MGR)
    po = ok(im.post('/api/purchasing/orders', json={'branch_id': 'KG-MYJ', 'lines': [{'item_id': 'KG-MSA-SHT-24', 'qty': 100, 'rate': 200},
                                                                                     {'item_id': 'KG-MSA-TIE-Std', 'qty': 50, 'rate': 40}], 'freight': 500}), 201)
    ok(im.post(f"/api/purchasing/orders/{po['id']}/quotes", json={'supplier_id': 'KS1', 'rates': [200, 40]}))
    ok(im.post(f"/api/purchasing/orders/{po['id']}/quotes", json={'supplier_id': 'KS2', 'rates': [195, 41]}))
    assert im.post(f"/api/purchasing/orders/{po['id']}/submit", json={'supplier_id': 'KS1'}).get_json()['error'] == 'reason_required'
    s = ok(im.post(f"/api/purchasing/orders/{po['id']}/submit", json={'supplier_id': 'KS1', 'reason': 'Better quality fabric'}))
    assert s['status'] == 'Pending approval'
    ok(approve(app, s['approval']['no'], CFO))
    cost_before = float(sql("SELECT cost FROM items WHERE id='KG-MSA-SHT-24'")[0]['cost'])
    g = ok(im.post(f"/api/purchasing/orders/{po['id']}/receive", json={'lines': [{'line_no': 1, 'received': 100, 'rejected': 2}, {'line_no': 2, 'received': 50}]}), 201)
    assert g['value'] > 0
    assert float(sql("SELECT cost FROM items WHERE id='KG-MSA-SHT-24'")[0]['cost']) != cost_before
    cfo = as_user(app, CFO)
    assert cfo.post(f"/api/purchasing/orders/{po['id']}/bill", json={'bill_no': 'DT/991', 'bill_total': 1}).get_json()['error'] == 'bill_mismatch'
    bill = ok(cfo.post(f"/api/purchasing/orders/{po['id']}/bill", json={'bill_no': 'DT/991'}), 201)
    assert cfo.post(f"/api/purchasing/orders/{po['id']}/bill", json={'bill_no': 'DT/991'}).get_json()['error'] == 'duplicate_bill'
    dn = ok(im.post(f"/api/purchasing/orders/{po['id']}/return", json={'line_no': 2, 'qty': 5, 'reason': 'Stitching fault'}), 201)
    assert dn['no'].startswith('KG/DN/')
    out = bill['amount'] - dn['amount']
    pay = ok(cfo.post('/api/purchasing/payments', json={'company': 'KG', 'supplier_id': 'KS1', 'amount': round(out, 2), 'mode': 'bank', 'reference': 'NEFT1'}), 201)
    assert pay['no'].startswith('KG/PV/')
    assert float(sql('SELECT amount - paid - adjusted o FROM supplier_bills WHERE id=%s', bill['id'])[0]['o']) == 0


# ---------------------------------------------------------------- accounts and cash custody
def test_expense_contra_journal_reverse(app):
    cfo = as_user(app, CFO)
    ok(cfo.post('/api/accounts/contra', json={'company': 'KG', 'kind': 'deposit', 'amount': 100, 'branch_id': 'KG-MSA'}), 201)
    e = ok(cfo.post('/api/accounts/expenses', json={'company': 'KG', 'account': '5500', 'amount': 1200, 'mode': 'bank', 'narration': 'Electricity Oct'}), 201)
    assert e['journal_no']
    jv = cfo.post('/api/accounts/journals', json={'company': 'KG', 'narration': 'Reclass', 'lines': [{'account': '5900', 'debit': 50}, {'account': '5500', 'credit': 50}]})
    j = ok(jv, 201)
    jv_bad = cfo.post('/api/accounts/journals', json={'company': 'KG', 'narration': 'x', 'lines': [{'account': '1300', 'debit': 5}, {'account': '5500', 'credit': 5}]})
    assert jv_bad.get_json()['error'] == 'control_account'
    r = ok(cfo.post('/api/accounts/reverse', json={'company': 'KG', 'journal_no': j['journal_no'], 'reason': 'Posted in error'}))
    assert r['reversal_no']
    assert cfo.post('/api/accounts/reverse', json={'company': 'KG', 'journal_no': j['journal_no'], 'reason': 'again'}).get_json()['error'] == 'already_reversed'


def test_handover_and_owner_instruction(app):
    c = as_user(app, CASHIER)
    open_shift(c)
    ok(c.post('/api/pos/invoices', json={'branch_id': 'KG-MSA', 'adm_no': STUDENT, 'lines': [{'item_id': 'KG-MSA-SHT-24', 'qty': 3}],
                                         'payments': [{'mode': 'cash', 'amount': 1047}]}), 201)
    clerk_id = sql("SELECT id FROM users WHERE emp_id=%s", CLERK)[0]['id']
    h = ok(c.post('/api/cash/handovers', json={'branch_id': 'KG-MSA', 'clerk_id': clerk_id, 'amount': 1000, 'denominations': {'500': 2}}), 201)
    cl = as_user(app, CLERK)
    assert c.post(f"/api/cash/handovers/{h['id']}/accept", json={}).status_code == 403
    ok(cl.post(f"/api/cash/handovers/{h['id']}/accept", json={'counted': 1000}))
    d = as_user(app, DIRECTOR)
    i = ok(d.post('/api/cash/instructions', json={'company': 'KG', 'clerk_id': clerk_id, 'type': 'Bank deposit', 'amount': 800, 'purpose': 'Deposit counter cash'}), 201)
    ok(approve(app, i['approval']['no'], CFO))
    iid = sql('SELECT id FROM owner_instructions WHERE no=%s', i['no'])[0]['id']
    assert cl.post(f'/api/cash/instructions/{iid}/execute', json={}).get_json()['error'] == 'payment_ref_required'
    ok(cl.post(f'/api/cash/instructions/{iid}/execute', json={'slip_no': 'HDFC-5521'}))
    ok(as_user(app, CFO).post(f'/api/cash/instructions/{iid}/verify', json={'ok': True}))
    assert sql('SELECT status FROM owner_instructions WHERE id=%s', iid)[0]['status'] == 'Closed'


def test_price_change_then_zaibus_sale(app):
    im = as_user(app, INV_MGR)
    item = sql("SELECT id FROM items WHERE company='ZE' AND id IN (SELECT item_id FROM stock_balance WHERE branch_id='ZE-MYJ' AND qty > 10) LIMIT 1")[0]['id']
    r = ok(im.post('/api/items/price-change', json={'rows': [{'id': item, 'price': 250}], 'reason': 'Publisher MRP'}), 202)
    ok(approve(app, r['no'], CFO))
    t = ok(im.post('/api/inventory/transfers', json={'from_branch': 'ZE-MYJ', 'to_branch': 'ZE-MSA', 'lines': [{'item_id': item, 'qty': 5}]}), 201)
    if t['approval']:                                            # books carry no cost yet -> value 0 is within any limit
        ok(approve(app, t['approval']['no'], DIRECTOR))
    ok(im.post(f"/api/inventory/transfers/{t['id']}/dispatch"))
    ok(im.post(f"/api/inventory/transfers/{t['id']}/receive", json={}))
    c = as_user(app, CASHIER)
    open_shift(c)
    inv = ok(c.post('/api/pos/invoices', json={'branch_id': 'ZE-MSA', 'adm_no': STUDENT, 'lines': [{'item_id': item, 'qty': 1}],
                                               'payments': [{'mode': 'card', 'amount': 250, 'ref': 'CARD77'}]}), 201)
    assert inv['no'].startswith('ZE/INV/') and inv['cgst'] == 0       # books carry 0% GST


def test_item_search_for_billing_roles(app):
    """Counter staff find items by name while billing even without the Stock screen (regression: 403 on /api/items)."""
    c = as_user(app, CASHIER)
    rows = ok(c.get('/api/items?company=KG&q=Blazer&size=5'))['rows']
    assert rows and 'cost' not in rows[0]
    ok(c.get('/api/items/' + rows[0]['id']))
    assert as_user(app, CLERK).get('/api/items?q=Blazer').status_code == 403   # Outdoor Clerk has no selling or stock screen
