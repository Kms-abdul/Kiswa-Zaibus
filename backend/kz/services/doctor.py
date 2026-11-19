"""`flask kz doctor`: integrity checks a developer runs first when numbers look wrong. Each check returns (name, ok, detail).
docs/TROUBLESHOOTING.md explains what to do for each failure."""
from .. import db
from ..core import audit


def run_checks():
    res = []
    ok, n, bad_id = audit.verify()
    res.append(('Audit chain', ok, f'{n} entries verified' if ok else f'chain breaks at audit_log.id={bad_id}'))
    for r in db.q('SELECT j.company, SUM(l.debit) d, SUM(l.credit) c FROM journal_lines l JOIN journals j ON j.id=l.journal_id GROUP BY j.company'):
        res.append((f"Books balance {r['company']}", r['d'] == r['c'], f"debit {r['d']} credit {r['c']}"))
    unbal = db.q('SELECT j.no, j.company, SUM(l.debit) d, SUM(l.credit) c FROM journals j JOIN journal_lines l ON l.journal_id=j.id GROUP BY j.id HAVING d <> c LIMIT 10')
    res.append(('Every voucher balances', not unbal, ', '.join(f"{x['company']} {x['no']}" for x in unbal) or 'all balanced'))
    empty = db.val('SELECT COUNT(*) FROM journals j WHERE NOT EXISTS (SELECT 1 FROM journal_lines l WHERE l.journal_id=j.id)')
    res.append(('Vouchers without lines', not empty, f'{empty} found'))
    mism = db.q('''SELECT b.branch_id, b.item_id, b.qty, COALESCE(SUM(l.qty),0) led FROM stock_balance b LEFT JOIN stock_ledger l
                   ON l.branch_id=b.branch_id AND l.item_id=b.item_id GROUP BY b.branch_id, b.item_id, b.qty HAVING b.qty <> led LIMIT 10''')
    res.append(('Stock balance = stock ledger', not mism, ', '.join(f"{x['branch_id']}/{x['item_id']} {x['qty']} vs {x['led']}" for x in mism) or 'all match'))
    neg = db.val('SELECT COUNT(*) FROM stock_balance WHERE qty < 0 OR reserved < 0 OR reserved > qty')
    res.append(('No negative or over-reserved stock', not neg, f'{neg} rows'))
    inv_nojv = db.val("SELECT COUNT(*) FROM invoices WHERE journal_no IS NULL")
    res.append(('Every invoice has its voucher', not inv_nojv, f'{inv_nojv} without journal_no'))
    inv_bal = db.q('''SELECT i.no FROM invoices i WHERE i.total <> (SELECT COALESCE(SUM(p.amount),0) FROM invoice_payments p WHERE p.invoice_id=i.id) LIMIT 10''')
    res.append(('Invoice payments add up', not inv_bal, ', '.join(x['no'] for x in inv_bal) or 'all add up'))
    for co in [r['code'] for r in db.q('SELECT code FROM companies')]:
        gl = db.val("SELECT COALESCE(SUM(l.debit-l.credit),0) FROM journal_lines l JOIN journals j ON j.id=l.journal_id WHERE j.company=:c AND l.account='1300'", c=co)
        sv = db.val('''SELECT COALESCE(SUM(s.qty * l2.unit_cost),0) FROM stock_balance s JOIN branches b ON b.id=s.branch_id
                       JOIN (SELECT item_id, branch_id, unit_cost FROM stock_ledger WHERE id IN (SELECT MAX(id) FROM stock_ledger GROUP BY item_id, branch_id)) l2
                       ON l2.item_id=s.item_id AND l2.branch_id=s.branch_id WHERE b.company=:c''', c=co)
        res.append((f'Inventory account vs stock (indicative) {co}', True, f'GL 1300 {gl} / stock at last cost {round(sv or 0, 2)}'))
    stuck = db.val("SELECT COUNT(*) FROM approvals WHERE status='Pending' AND requested_at < NOW() - INTERVAL 7 DAY")
    res.append(('Approvals pending over 7 days', not stuck, f'{stuck} waiting'))
    failed = db.val("SELECT COUNT(*) FROM outbox WHERE status='Failed' AND created_at > NOW() - INTERVAL 7 DAY")
    res.append(('Messages failed in the last 7 days', not failed, f'{failed} failed (check SMTP / SMS settings)'))
    rev = db.val('SELECT version_num FROM alembic_version')
    res.append(('Database migration', bool(rev), f'at revision {rev}'))
    return res
