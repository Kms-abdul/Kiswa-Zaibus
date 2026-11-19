"""Stock audit module: create → count (manual, scan, sheet) → submit → verify → review → CFO / Director decision (impact review; approve all,
approve some items, return for recount, reject) → automatic stock adjustment and journal → close. Segregation of duties, evidence rules,
high-value escalation, reopen, exports, reports and the dashboard (release 2.2, approval rules release 2.3).
After every test the books must balance and stock must equal its ledger."""
import io

import pytest

from conftest import as_user, books_balance, ok, sql, stock_consistent

AUDITOR, INCHARGE, DIRECTOR, CFO, INV_MGR, SUPER = 'KZ-MSA-021', 'KZ-MSA-020', 'KZ-HO-001', 'KZ-HO-002', 'KZ-WH-030', 'KZ-IT-001'
BRANCH = 'KG-MSA'


@pytest.fixture(autouse=True)
def invariants(app):
    yield
    for co, (d, c) in books_balance().items():
        assert d == c, f'{co} books do not balance: {d} vs {c}'
    assert stock_consistent() == (), 'stock_balance differs from stock_ledger'


def uid(emp):
    return sql('SELECT id FROM users WHERE emp_id=%s', emp)[0]['id']


def stocked(n=3, min_qty=3):
    return sql('''SELECT s.item_id, s.qty, i.cost, i.barcode FROM stock_balance s JOIN items i ON i.id=s.item_id
                  WHERE s.branch_id=%s AND s.qty >= %s AND i.cost > 0 ORDER BY s.item_id LIMIT %s''', BRANCH, min_qty, n)


def new_audit(app, items, **kw):
    c = as_user(app, INCHARGE)
    body = {'branch_id': BRANCH, 'audit_type': 'Item', 'items': [i['item_id'] for i in items], 'auditors': [uid(AUDITOR)],
            'supervisor_id': uid(INCHARGE), 'stock_area': 'Counter racks', 'instructions': 'Count every size.', **kw}
    return ok(c.post('/api/stock-audits', json=body), 201)


def attach(c, ref):
    r = c.post('/api/evidence', data={'ref': ref, 'kind': 'Photo', 'file': (io.BytesIO(f'photo {ref}'.encode()), 'shelf.jpg')},
                               content_type='multipart/form-data')
    assert r.status_code == 201, r.get_json()


def qty(item_id):
    return sql('SELECT qty FROM stock_balance WHERE branch_id=%s AND item_id=%s', BRANCH, item_id)[0]['qty']


def to_review(app, a, rows, reviewer=INV_MGR, note='Checked'):
    """Count, submit, verify and review: returns the 202 response body that carries the approval request."""
    c = as_user(app, AUDITOR)
    ok(c.post(f"/api/stock-audits/{a['id']}/counts", json={'rows': rows}))
    ok(c.post(f"/api/stock-audits/{a['id']}/submit", json={}))
    ok(as_user(app, INCHARGE).post(f"/api/stock-audits/{a['id']}/verify", json={'ok': True}))
    return ok(as_user(app, reviewer).post(f"/api/stock-audits/{a['id']}/review", json={'action': 'approve', 'note': note}), 202)


def test_full_cycle_stock_moves_only_on_cfo_approval(app):
    items = stocked(3)
    a = new_audit(app, items)
    c = as_user(app, AUDITOR)
    rows = [{'line_no': 1, 'physical_qty': items[0]['qty'] - 1},                       # shortage 1
            {'line_no': 2, 'physical_qty': items[1]['qty']},                           # matched
            {'line_no': 3, 'physical_qty': items[2]['qty'] + 1, 'remarks': 'found behind carton'}]  # excess 1
    d = ok(c.post(f"/api/stock-audits/{a['id']}/counts", json={'rows': rows}))
    assert d['status'] == 'In Progress' and d['counted_count'] == 3 and d['matched_count'] == 1
    assert [ln['status'] for ln in d['lines']] == ['Shortage', 'Matched', 'Excess']
    assert d['next_step']['step'] == 'Count' and d['next_step']['mine']
    assert qty(items[0]['item_id']) == items[0]['qty']                                 # counting never changes stock
    # segregation: the auditor cannot verify; the supervisor cannot count
    ok(c.post(f"/api/stock-audits/{a['id']}/submit", json={}))
    assert c.post(f"/api/stock-audits/{a['id']}/verify", json={'ok': True}).status_code == 403
    assert as_user(app, INCHARGE).post(f"/api/stock-audits/{a['id']}/counts", json={'rows': rows}).get_json()['error'] == 'audit_locked'
    ok(as_user(app, INCHARGE).post(f"/api/stock-audits/{a['id']}/verify", json={'ok': True, 'note': 'Recounted two racks myself'}))
    r = ok(as_user(app, INV_MGR).post(f"/api/stock-audits/{a['id']}/review", json={'action': 'approve', 'note': 'Small variance'}), 202)
    assert r['approval']['required_role'] == 'CFO' and r['status'] == 'Under Review'
    assert qty(items[0]['item_id']) == items[0]['qty']                                 # review never changes stock either
    assert as_user(app, INV_MGR).post(f"/api/stock-audits/{a['id']}/post", json={}).get_json()['error'] == 'wrong_status'
    # the impact review the CFO sees before deciding
    im = ok(as_user(app, CFO).get(f"/api/stock-audits/{a['id']}/impact"))
    assert im['can_decide'] and im['approval']['items'] == 2 and im['totals']['short_qty'] == 1 and im['totals']['excess_qty'] == 1
    assert im['totals']['matched'] == 1 and {j['account'] for j in im['journal']} == {'5600', '1300', '4130'}
    assert sum(j['debit'] for j in im['journal']) == sum(j['credit'] for j in im['journal'])
    assert not ok(as_user(app, INV_MGR).get(f"/api/stock-audits/{a['id']}/impact"))['can_decide']     # reviewers cannot approve
    assert as_user(app, INV_MGR).post(f"/api/stock-audits/{a['id']}/decide", json={'mode': 'all'}).get_json()['error'] == 'not_approver'
    d = ok(as_user(app, CFO).post(f"/api/stock-audits/{a['id']}/decide", json={'mode': 'all', 'remarks': 'Agreed'}))
    assert d['status'] == 'Closed' and d['adjustment_no']                               # posted automatically on approval
    assert qty(items[0]['item_id']) == items[0]['qty'] - 1 and qty(items[2]['item_id']) == items[2]['qty'] + 1
    adj = sql('SELECT audit_no, adj_type, status, journal_no FROM stock_adjustments WHERE no=%s', d['adjustment_no'])[0]
    assert adj['audit_no'] == a['no'] and adj['adj_type'] == 'Stock audit' and adj['status'] == 'Posted' and adj['journal_no']
    moves = sql('SELECT qty FROM stock_ledger WHERE ref=%s ORDER BY qty', d['adjustment_no'])
    assert [m['qty'] for m in moves] == [-1, 1]
    jl = sql('SELECT account, debit, credit FROM journal_lines l JOIN journals j ON j.id=l.journal_id WHERE j.no=%s ORDER BY account', adj['journal_no'])
    assert {x['account'] for x in jl} == {'1300', '4130', '5600'}
    assert [ln['decision'] for ln in d['lines']] == ['Approved', 'Matched', 'Approved']
    rec = d['decisions'][0]
    assert rec['mode'] == 'Full approval' and rec['decided_role'] == 'CFO' and rec['adjustment_no'] == d['adjustment_no']
    # closed audits are locked and permanent
    assert c.post(f"/api/stock-audits/{a['id']}/cancel", json={'reason': 'x'}).get_json()['error'] == 'wrong_status'
    actions = [e['action'] for e in d['events']]
    assert actions[:2] == ['Created', 'Count started'] and 'Full approval by CFO' in actions


def test_evidence_required_and_high_value_escalates_to_director(app):
    items = stocked(1, min_qty=5)
    it = items[0]
    a = new_audit(app, items)
    c = as_user(app, AUDITOR)
    # three short and the rest damaged: needs a photo, and the value is pushed over the high-value threshold
    as_user(app, DIRECTOR).put('/api/settings/stock_audit', json={'value': {'high_value': 10, 'photo_above': 2000, 'due_days': 3, 'spot_size': 20,
                                                                            'max_lines': 5000}, 'reason': 'test escalation'})
    ok(c.post(f"/api/stock-audits/{a['id']}/counts", json={'rows': [{'line_no': 1, 'physical_qty': it['qty'] - 3, 'condition': 'Damaged', 'dmg_qty': 1}]}))
    r = c.post(f"/api/stock-audits/{a['id']}/submit", json={})
    assert r.get_json()['error'] == 'evidence_required'
    attach(c, f"{a['no']}/{it['item_id']}")      # same signed-in client: a second sign-in would end its session
    ok(c.post(f"/api/stock-audits/{a['id']}/submit", json={}))
    ok(as_user(app, INCHARGE).post(f"/api/stock-audits/{a['id']}/verify", json={'ok': True}))
    r = as_user(app, INV_MGR).post(f"/api/stock-audits/{a['id']}/review", json={'action': 'approve', 'note': 'please approve'})
    assert r.status_code == 202 and r.get_json()['approval']['required_role'] == 'Director' and r.get_json()['escalated']
    assert sql("SELECT COUNT(*) n FROM alerts WHERE alert_type='High stock variance' AND ref=%s", a['no'])[0]['n'] == 1
    assert as_user(app, CFO).post(f"/api/stock-audits/{a['id']}/post", json={}).get_json()['error'] == 'wrong_status'   # no posting before approval
    assert as_user(app, CFO).post(f"/api/stock-audits/{a['id']}/decide", json={'mode': 'all'}).status_code == 403          # above the CFO's limit
    # the Director approves from the general approvals inbox: the same rules apply and the adjustment posts at once
    apr = sql('SELECT id FROM approvals WHERE no=%s', r.get_json()['approval']['no'])[0]['id']
    ok(as_user(app, DIRECTOR).post(f'/api/approvals/{apr}/decide', json={'action': 'approve', 'remarks': 'Write off damaged stock'}))
    row = sql('SELECT status, adjustment_no FROM stock_audits WHERE id=%s', a['id'])[0]
    assert row['status'] == 'Closed' and row['adjustment_no']
    assert qty(it['item_id']) == it['qty'] - 4                                           # 3 short + 1 damaged written off
    moves = sql('SELECT qty, txn_type FROM stock_ledger WHERE ref=%s ORDER BY qty', row['adjustment_no'])
    assert sorted(m['qty'] for m in moves) == [-3, -1] and any('Write-off' in m['txn_type'] for m in moves)
    jno = sql('SELECT journal_no FROM stock_adjustments WHERE no=%s', row['adjustment_no'])[0]['journal_no']
    assert '5610' in {x['account'] for x in sql('SELECT account FROM journal_lines l JOIN journals j ON j.id=l.journal_id WHERE j.no=%s', jno)}
    as_user(app, DIRECTOR).put('/api/settings/stock_audit', json={'value': {'high_value': 25000, 'photo_above': 2000, 'due_days': 3, 'spot_size': 20,
                                                                            'max_lines': 5000}, 'reason': 'restore'})


def test_partial_approval_recount_and_reject(app):
    items = stocked(4, min_qty=4)
    a = new_audit(app, items)
    rows = [{'line_no': i + 1, 'physical_qty': it['qty'] - 1} for i, it in enumerate(items)]     # every item one short
    to_review(app, a, rows)
    cfo = as_user(app, CFO)
    bad_ = cfo.post(f"/api/stock-audits/{a['id']}/decide", json={'mode': 'partial', 'decisions': {'1': 'Maybe'}})
    assert bad_.get_json()['error'] == 'invalid'
    assert cfo.post(f"/api/stock-audits/{a['id']}/decide", json={'mode': 'return'}).get_json()['error'] == 'reason_required'
    # approve item 1, recount item 2, reject item 3, hold item 4
    d = ok(cfo.post(f"/api/stock-audits/{a['id']}/decide", json={'mode': 'partial', 'remarks': 'Item 2 looks wrong',
                                                                  'decisions': {'1': 'Approved', '2': 'Recount', '3': 'Rejected', '4': 'Pending'}}))
    assert d['status'] == 'In Progress' and d['adjustment_no']
    assert qty(items[0]['item_id']) == items[0]['qty'] - 1                                # only the approved item moved
    assert [qty(i['item_id']) for i in items[1:]] == [i['qty'] for i in items[1:]]
    by = {ln['line_no']: ln for ln in d['lines']}
    assert by[1]['decision'] == 'Approved' and by[2]['recount'] and by[3]['decision'] == 'Rejected' and not by[4]['decision']
    assert d['decisions'][-1]['mode'] == 'Partial approval'
    c = as_user(app, AUDITOR)
    assert c.post(f"/api/stock-audits/{a['id']}/counts", json={'rows': [{'line_no': 1, 'physical_qty': 0}]}).get_json()['error'] == 'line_decided'
    # recount item 2 (it was right after all), resubmit: only items 2 and 4 come back for decision
    ok(c.post(f"/api/stock-audits/{a['id']}/counts", json={'rows': [{'line_no': 2, 'physical_qty': items[1]['qty']}]}))
    ok(c.post(f"/api/stock-audits/{a['id']}/submit", json={}))
    ok(as_user(app, INCHARGE).post(f"/api/stock-audits/{a['id']}/verify", json={'ok': True}))
    r = ok(as_user(app, INV_MGR).post(f"/api/stock-audits/{a['id']}/review", json={'action': 'approve'}), 202)
    im = ok(cfo.get(f"/api/stock-audits/{a['id']}/impact"))
    assert im['approval']['items'] == 1 and [ln['line_no'] for ln in im['lines'] if ln['in_cycle']] == [4]
    # return for clarification from the approvals inbox, then reject the last item
    apr = sql('SELECT id FROM approvals WHERE no=%s', r['approval']['no'])[0]['id']
    ok(cfo.post(f'/api/approvals/{apr}/decide', json={'action': 'return', 'remarks': 'Photo please'}))
    assert sql('SELECT status FROM stock_audits WHERE id=%s', a['id'])[0]['status'] == 'In Progress'
    ok(c.post(f"/api/stock-audits/{a['id']}/counts", json={'rows': [{'line_no': 4, 'physical_qty': items[3]['qty'] - 1}]}))
    ok(c.post(f"/api/stock-audits/{a['id']}/submit", json={}))
    ok(as_user(app, INCHARGE).post(f"/api/stock-audits/{a['id']}/verify", json={'ok': True}))
    ok(as_user(app, INV_MGR).post(f"/api/stock-audits/{a['id']}/review", json={'action': 'approve'}), 202)
    d = ok(cfo.post(f"/api/stock-audits/{a['id']}/decide", json={'mode': 'reject', 'remarks': 'Not convinced; leave the books as they are'}))
    assert d['status'] == 'Closed'                                                        # item 1 was approved earlier, so the audit closes
    assert qty(items[3]['item_id']) == items[3]['qty']
    assert [x['mode'] for x in d['decisions']] == ['Partial approval', 'Returned for recount / clarification', 'Rejected']


def test_auditor_counts_and_auditors_can_be_added(app):
    items = stocked(1)
    a = new_audit(app, items)
    other = sql("SELECT id FROM users WHERE emp_id='KZ-MSA-022'") or sql("""SELECT u.id FROM users u WHERE u.status='Active' AND u.main_role IN
                ('Store Cashier','Inventory User','Auditor') AND u.id<>%s LIMIT 1""", uid(AUDITOR))
    inc = as_user(app, INCHARGE)
    assert inc.post(f"/api/stock-audits/{a['id']}/auditors", json={'user_id': uid(INCHARGE)}).get_json()['error'] == 'segregation'
    assert inc.post(f"/api/stock-audits/{a['id']}/auditors", json={'user_id': uid(AUDITOR)}).get_json()['error'] == 'duplicate'
    if other:
        r = inc.post(f"/api/stock-audits/{a['id']}/auditors", json={'user_id': other[0]['id']})
        assert r.status_code in (200, 400)
    aud = sql("SELECT id FROM users WHERE main_role='Auditor' AND status='Active' LIMIT 1")
    if aud:                                            # the read-only Auditor role may count and attach photos
        d = ok(inc.post(f"/api/stock-audits/{a['id']}/auditors", json={'user_id': aud[0]['id']}))
        assert aud[0]['id'] in d['auditors']


def test_scan_sheet_import_reject_reopen_and_cancel(app):
    items = stocked(2)
    a = new_audit(app, items)
    c = as_user(app, AUDITOR)
    # scanning adds one per scan; an item found on the shelf but not planned is added to the audit
    code = items[0]['barcode'] or items[0]['item_id']
    for _ in range(2):
        ok(c.post(f"/api/stock-audits/{a['id']}/scan", json={'code': code}))
    other = sql('''SELECT i.id FROM items i WHERE i.company='KG' AND i.id NOT IN %s ORDER BY i.id LIMIT 1''', tuple(i['item_id'] for i in items))[0]['id']
    s = ok(c.post(f"/api/stock-audits/{a['id']}/scan", json={'code': other}))
    assert s['lines_count'] == 3 and s['line']['physical_qty'] == 1
    # count sheet upload (CSV) overrides the scanned figures
    other_qty = (sql('SELECT qty FROM stock_balance WHERE branch_id=%s AND item_id=%s', BRANCH, other) or [{'qty': 0}])[0]['qty']
    csv = (f"item_code,physical_qty,condition,remarks\n{items[0]['item_id']},{items[0]['qty']},Good,\n{items[1]['item_id']},{items[1]['qty']},Good,\n"
           f"{other},{other_qty},Good,on the wrong rack\n")
    r = c.post(f"/api/stock-audits/{a['id']}/import", data={'file': (io.BytesIO(csv.encode()), 'count.csv')}, content_type='multipart/form-data')
    d = ok(r)
    assert d['imported_rows'] == 3 and d['counted_count'] == 3
    # downloads
    assert c.get(f"/api/stock-audits/{a['id']}/count-sheet").headers['Content-Type'].startswith('application/vnd.openxmlformats')
    ok(c.post(f"/api/stock-audits/{a['id']}/submit", json={}))
    ok(as_user(app, INCHARGE).post(f"/api/stock-audits/{a['id']}/verify", json={'ok': True}))
    ok(as_user(app, INV_MGR).post(f"/api/stock-audits/{a['id']}/review", json={'action': 'reject', 'note': 'Wrong rack audited'}))
    assert sql('SELECT status FROM stock_audits WHERE id=%s', a['id'])[0]['status'] == 'Rejected'
    assert as_user(app, INV_MGR).post(f"/api/stock-audits/{a['id']}/reopen", json={'reason': 'x'}).status_code == 403   # needs restore
    d = ok(as_user(app, SUPER).post(f"/api/stock-audits/{a['id']}/reopen", json={'reason': 'Recount the correct rack'}))
    assert d['status'] == 'In Progress' and d['reopen_count'] == 1
    assert c.post(f"/api/stock-audits/{a['id']}/cancel", json={'reason': 'x'}).status_code == 403          # counters cannot cancel
    ok(as_user(app, INCHARGE).post(f"/api/stock-audits/{a['id']}/cancel", json={'reason': 'Replaced by a full audit'}))
    pdf = as_user(app, DIRECTOR).get(f"/api/stock-audits/{a['id']}/export?format=pdf")
    assert pdf.status_code == 200 and pdf.data[:4] == b'%PDF'
    xl = as_user(app, DIRECTOR).get(f"/api/stock-audits/{a['id']}/export?format=xlsx")
    assert xl.status_code == 200 and xl.data[:2] == b'PK'


def test_create_rules(app):
    items = stocked(1)
    c = as_user(app, INCHARGE)
    base = {'branch_id': BRANCH, 'audit_type': 'Item', 'items': [items[0]['item_id']], 'auditors': [uid(AUDITOR)], 'supervisor_id': uid(INCHARGE)}
    assert c.post('/api/stock-audits', json={**base, 'supervisor_id': uid(AUDITOR)}).get_json()['error'] == 'segregation'
    assert c.post('/api/stock-audits', json={**base, 'auditors': []}).get_json()['error'] == 'no_auditors'
    assert c.post('/api/stock-audits', json={**base, 'audit_type': 'Category'}).get_json()['error'] == 'no_categories'
    spot = ok(c.post('/api/stock-audits', json={**base, 'audit_type': 'Spot', 'items': None, 'spot_size': 4}), 201)
    assert len(spot['lines']) == 4
    full = ok(c.post('/api/stock-audits', json={**base, 'audit_type': 'Full', 'items': None}), 201)
    assert full['lines_count'] == sql('''SELECT COUNT(*) n FROM stock_balance s JOIN items i ON i.id=s.item_id
                                         WHERE s.branch_id=%s AND i.active=1''', BRANCH)[0]['n']
    assert as_user(app, AUDITOR).post('/api/stock-audits', json=base).status_code == 403                # cashier cannot create


def test_search_reports_dashboard(app):
    d = as_user(app, DIRECTOR)
    rows = ok(d.get('/api/stock-audits?flag=shortage'))
    assert rows and all(r['shortage_value'] > 0 for r in rows)
    assert ok(d.get(f"/api/stock-audits?auditor={uid(AUDITOR)}&status=Closed"))
    meta = ok(d.get('/api/stock-audits/meta'))
    for key in meta['reports']:
        ok(d.get(f'/api/stock-audits/reports/{key}?from=2020-01-01'))
    x = d.get('/api/stock-audits/reports/item-variance?from=2020-01-01&format=xlsx')
    assert x.status_code == 200 and x.data[:2] == b'PK'
    dash = ok(d.get('/api/stock-audits/dashboard?from=2020-01-01'))
    assert dash['audits'] >= 2 and dash['shortage_value'] > 0 and 'trend' in dash
    # values are hidden from users without cost / finance rights on stock audits
    r = ok(as_user(app, AUDITOR).get('/api/stock-audits?status=Closed'))
    assert r and r[0]['shortage_value'] is None
