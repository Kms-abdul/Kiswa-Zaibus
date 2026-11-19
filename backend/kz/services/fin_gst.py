"""Finance & tax: GST. Every figure is read from the posted books (sales invoices, credit notes, purchase invoices, debit notes and finance
vouchers), so returns, registers and the ledger agree. Set-off follows rule 88A (IGST credit first). Government portals (GSTN, IRP,
e-way bill) are not connected in this version: IRN / e-way numbers are generated locally in the official format and marked as such."""
import datetime as dt
import hashlib
import json
import re
from decimal import Decimal

from flask import g

from .. import db
from ..core import audit, notify
from ..core.rbac import can
from ..errors import bad, conflict, denied, not_found
from .fin_common import HEADS, HOME_STATE, INPUT, OUTPUT, cfg, fy_of, gstin_fix, ho, lock_company, month_end, need_ym, r2, txt

RATES = (0, 0.25, 3, 5, 12, 18, 28, 40)
ZERO = {'taxable': 0.0, 'igst': 0.0, 'cgst': 0.0, 'sgst': 0.0, 'cess': 0.0}


def near_rate(x):
    return min(RATES, key=lambda r: abs(r - x))


def period(ym):
    a = dt.date(int(ym[:4]), int(ym[5:7]), 1)
    return a, month_end(a)


def fp(ym):
    return ym[5:7] + ym[:4]


def next_ym(ym):
    a = period(ym)[1] + dt.timedelta(days=1)
    return f'{a:%Y-%m}'


def gstin_of(company):
    return db.val('SELECT gstin FROM gst_registrations WHERE company=:c ORDER BY id LIMIT 1', c=company) or db.val('SELECT gstin FROM companies WHERE code=:c', c=company)


def _row(hsn, rate, taxable, tax, inter, qty=0, uqc='NOS', cess=0):
    tax, taxable = r2(tax), r2(taxable)
    half = r2(tax / 2)
    return {'hsn': hsn or '', 'rate': float(rate), 'taxable': float(taxable), 'igst': float(tax if inter else 0), 'cgst': float(0 if inter else half),
            'sgst': float(0 if inter else tax - half), 'cess': float(r2(cess)), 'qty': qty or 0, 'uqc': uqc or 'NOS'}


def docs(company, frm, to):
    """Every GST document in the period, outward and inward, built from the books."""
    out = []
    for inv in db.q('''SELECT i.id, i.no, i.inv_date, i.branch_id, i.customer_id, i.place_of_supply, i.igst, i.total, i.status, c.name, c.gstin
                       FROM invoices i LEFT JOIN customers c ON c.id=i.customer_id WHERE i.company=:c AND i.inv_date BETWEEN :a AND :b''', c=company, a=frm, b=to):
        grp = {}
        for ln in db.q('''SELECT l.hsn, l.gst_rate, l.taxable, l.tax, l.qty, it.category FROM invoice_lines l LEFT JOIN items it ON it.id=l.item_id
                          WHERE l.invoice_id=:i''', i=inv['id']):
            k = (ln['hsn'] or '', float(ln['gst_rate'] or 0))
            x = grp.setdefault(k, {'tx': Decimal(0), 't': Decimal(0), 'q': 0, 'uqc': 'PRS' if re.search('shoe|sock', ln['category'] or '', re.I) else 'NOS'})
            x['tx'] += Decimal(str(ln['taxable'] or 0))
            x['t'] += Decimal(str(ln['tax'] or 0))
            x['q'] += ln['qty'] or 0
        inter = float(inv['igst'] or 0) > 0
        out.append({'dir': 'out', 'type': 'INV', 'no': inv['no'], 'date': inv['inv_date'], 'branch': inv['branch_id'], 'party': inv['customer_id'],
                    'name': inv['name'] or inv['customer_id'] or 'Walk-in', 'gstin': inv['gstin'] or '', 'pos': inv['place_of_supply'] or HOME_STATE,
                    'val': float(inv['total']), 'rows': [_row(k[0], k[1], x['tx'], x['t'], inter, x['q'], x['uqc']) for k, x in grp.items()],
                    'src': 'Sales', 'status': inv['status']})
    for j in db.q("""SELECT id, no, ref, txn_date, type, narration FROM journals WHERE company=:c AND txn_date BETWEEN :a AND :b
                     AND type IN ('Credit note','Purchase invoice','Debit note','Expense')""", c=company, a=frm, b=to):
        ls = db.q('SELECT account, branch_id, party, debit, credit FROM journal_lines WHERE journal_id=:i', i=j['id'])
        net = lambda accts: sum(Decimal(str(x['debit'])) - Decimal(str(x['credit'])) for x in ls if x['account'] in accts)  # noqa: E731
        br = ls[0]['branch_id'] if ls else None
        if j['type'] == 'Credit note':
            tx, tax = net(('4000', '4010', '4020')), net(('2100', '2110', '2120'))
            if abs(tx) < Decimal('0.01'):
                continue
            inv = db.one('''SELECT i.no, i.inv_date, i.customer_id, i.place_of_supply, c.name, c.gstin FROM credit_notes n JOIN invoices i ON i.id=n.invoice_id
                            LEFT JOIN customers c ON c.id=i.customer_id WHERE n.no=:n''', n=j['ref']) or {}
            hsn = db.val('SELECT l.hsn FROM invoice_lines l JOIN invoices i ON i.id=l.invoice_id WHERE i.no=:n LIMIT 1', n=inv.get('no')) if inv else ''
            out.append({'dir': 'out', 'type': 'CN', 'no': j['ref'], 'date': j['txn_date'], 'branch': br, 'party': inv.get('customer_id'), 'name': inv.get('name') or '',
                        'gstin': inv.get('gstin') or '', 'pos': inv.get('place_of_supply') or HOME_STATE, 'val': float(tx + tax), 'against': inv.get('no'),
                        'rows': [_row(hsn, near_rate(float(tax / tx * 100)), tx, tax, net(('2120',)) > 0)], 'src': 'Returns'})
        else:
            inp = ('1310', '1311', '1312', '1313')
            tax = net(inp)
            if j['type'] == 'Expense' and not tax:
                continue
            pl = next((x for x in ls if x['account'] == '2000' and x['party']), {})
            sign = -1 if j['type'] == 'Debit note' else 1
            tx = sum(Decimal(str(x['debit'] if sign > 0 else x['credit'])) for x in ls if x['account'] not in inp + ('2000',) and (x['debit'] if sign > 0 else x['credit']))
            if tx < Decimal('0.01'):
                continue
            s = db.one('SELECT name, gstin FROM suppliers WHERE id=:i', i=pl.get('party')) or {}
            bill = db.val('SELECT bill_no FROM supplier_bills WHERE journal_no=:n', n=j['no'])
            out.append({'dir': 'in', 'type': 'DN' if sign < 0 else 'INV', 'no': bill or j['ref'], 'our_ref': j['ref'], 'date': j['txn_date'], 'branch': br,
                        'party': pl.get('party'), 'name': s.get('name') or '', 'gstin': s.get('gstin') or '', 'pos': HOME_STATE, 'val': float(tx + abs(tax)),
                        'rows': [_row('', near_rate(float(abs(tax) / tx * 100)), tx, abs(tax), net(('1313',)) != 0)], 'itc': 'Eligible', 'rcm': False, 'src': 'Purchases'})
    for v in db.q("""SELECT * FROM fin_vouchers WHERE company=:c AND status IN ('Posted','Reversed') AND vdate BETWEEN :a AND :b AND gst IS NOT NULL""", c=company, a=frm, b=to):
        gst = json.loads(v['gst']) if isinstance(v['gst'], str) else v['gst']
        calc = (gst or {}).get('calc')
        if not calc:
            continue
        from .fin_common import VTYPES
        d = gst.get('dir') or VTYPES[v['vtype']][4]
        out.append({'dir': 'out' if d in ('out', 'outRev', 'adv') else 'in', 'type': {'CN': 'CN', 'DN': 'DN', 'RV': 'ADV'}.get(v['vtype'], 'INV'),
                    'no': (v['ref'] or v['no']) if v['vtype'] in ('PI', 'EX', 'AC', 'DN') else v['no'], 'our_ref': v['no'], 'date': v['vdate'], 'branch': v['branch_id'],
                    'party': v['party_id'], 'name': v['party_name'] or '', 'gstin': v['gstin'] or '', 'pos': calc.get('pos') if calc.get('inter') else HOME_STATE,
                    'val': float(r2(calc['t']['taxable'] + calc['t']['tax'])), 'itc': gst.get('itc') or 'Eligible', 'rcm': bool(gst.get('rcm')), 'src': VTYPES[v['vtype']][0],
                    'rows': [{'hsn': r.get('hsn') or '', 'rate': r['rate'], 'taxable': r['taxable'], 'igst': r['igst'], 'cgst': r['cgst'], 'sgst': r['sgst'],
                              'cess': r.get('cess_amt', 0), 'qty': float(r.get('qty') or 0), 'uqc': r.get('uqc') or ('OTH' if str(r.get('hsn', '')).startswith('99') else 'NOS')}
                             for r in calc['rows']], 'against': v['ref']})
    einv = {r['doc_no']: r for r in db.clean(db.q('SELECT * FROM einvoices WHERE company=:c', c=company))}
    for d in out:
        d['taxable'] = float(r2(sum(Decimal(str(r['taxable'])) for r in d['rows'])))
        for h in HEADS:
            d[h] = float(r2(sum(Decimal(str(r[h])) for r in d['rows'])))
        d['tax'] = float(r2(d['igst'] + d['cgst'] + d['sgst'] + d['cess']))
        d['sign'] = -1 if d['type'] == 'CN' or (d['dir'] == 'in' and d['type'] == 'DN') else 1
        d['state'] = d['gstin'][:2] if d['gstin'] else d['pos']
        d['einvoice'] = einv.get(d['no'])
        d['date'] = str(d['date'])
    out.sort(key=lambda d: (d['date'], d['no']))
    return out


# ---------- liability, credit and set-off ----------
def month_books(company, ym):
    """Output tax raised, input credit taken and reverse-charge tax for a month, excluding the set-off / payment entries themselves."""
    a, b = period(ym)
    rows = db.q('''SELECT l.account, SUM(l.debit-l.credit) AS v FROM journal_lines l JOIN journals j ON j.id=l.journal_id
                   WHERE j.company=:c AND j.txn_date BETWEEN :a AND :b AND l.account IN ('2100','2110','2120','2130','2140','1310','1311','1312','1313','1314')
                   AND j.ref NOT IN (SELECT voucher_no FROM gst_challans WHERE company=:c AND voucher_no IS NOT NULL) GROUP BY l.account''', c=company, a=a, b=b)
    m = {r['account']: float(r['v'] or 0) for r in rows}
    L = {h: float(r2(-m.get(OUTPUT[h], 0))) for h in HEADS}
    C = {h: m.get(INPUT[h], 0) for h in HEADS}
    C['cgst'] += m.get('1310', 0) / 2
    C['sgst'] += m.get('1310', 0) / 2
    return {'liability': L, 'credit': {h: float(r2(x)) for h, x in C.items()}, 'rcm': float(r2(-m.get('2140', 0)))}


def setoff(company, ym):
    """Carry the electronic credit ledger from the start of the year and set off under rule 88A; what is left is paid in cash."""
    fy = fy_of(company, period(ym)[0])
    bal = dict.fromkeys(HEADS, 0.0)
    m = f"{fy['from_date']:%Y-%m}"
    res = None
    while m <= ym:
        b = month_books(company, m)
        cr = {h: round(bal[h] + b['credit'][h], 2) for h in HEADS}
        L = dict(b['liability'])
        use = {'igst': {'igst': 0, 'cgst': 0, 'sgst': 0}, 'cgst': {'cgst': 0, 'igst': 0}, 'sgst': {'sgst': 0, 'igst': 0}, 'cess': {'cess': 0}}

        def take(f, t):
            x = max(0, min(cr[f], L[t]))
            cr[f], L[t] = round(cr[f] - x, 2), round(L[t] - x, 2)
            use[f][t] = round(use[f][t] + x, 2)
        for f, t in (('igst', 'igst'), ('igst', 'cgst'), ('igst', 'sgst'), ('cgst', 'cgst'), ('cgst', 'igst'), ('sgst', 'sgst'), ('sgst', 'igst'), ('cess', 'cess')):
            take(f, t)
        cash = {h: max(0, L[h]) for h in HEADS}
        cash['rcm'] = max(0, b['rcm'])
        cash['total'] = round(sum(cash.values()), 2)
        res = {'ym': m, 'books': b, 'opening': bal, 'credit': cr, 'use': use, 'cash': cash, 'liability': b['liability']}
        bal = {h: max(0, cr[h]) for h in HEADS}
        m = next_ym(m)
    res['closing'] = bal
    nx = next_ym(ym)
    due = dt.date(int(nx[:4]), int(nx[5:]), 20)
    res['due'] = str(due)
    paid = db.clean(db.one("SELECT * FROM gst_challans WHERE company=:c AND period=:p AND status='Paid'", c=company, p=ym))
    res['paid'] = paid
    pay_date = dt.date.fromisoformat(paid['cdate'][:10]) if paid else dt.date.today()
    late = max(0, (pay_date - due).days)
    c = cfg()['gst']
    res['interest'] = round(res['cash']['total'] * c['interest'] / 100 * late / 365)
    res['late_days'] = late
    filed = db.clean(db.one("SELECT * FROM gst_filings WHERE company=:c AND ret='3B' AND period=:p", c=company, p=ym))
    fdays = max(0, ((dt.date.fromisoformat(filed['filed_on'][:10]) if filed else dt.date.today()) - due).days)
    nil = sum(res['liability'].values()) + res['books']['rcm'] < 0.5
    res['fee'] = min(c['fee_cap'], fdays * (c['nil_fee'] if nil else c['fee']))
    res['fee_days'], res['filed'] = fdays, filed
    return res


def pay(company, ym, ch):
    """GST adjustment voucher: liabilities debited, credit ledgers used, RCM paid, interest / fee, bank credited; challan Paid on posting."""
    from . import fin_vouchers as fv
    need_ym(ym)
    lock_company(company)                              # a double click waits here, then finds the first challan below
    if db.one("SELECT 1 FROM gst_challans WHERE company=:c AND period=:p AND status<>'Cancelled' FOR UPDATE", c=company, p=ym):
        raise conflict(f'GST for {ym} is already paid or waiting for approval.', 'duplicate')
    s = setoff(company, ym)
    extra = s['interest'] + s['fee']
    if not (ch.get('cpin') or '').strip() and s['cash']['total'] + extra > 0:
        raise bad('Enter the CPIN of the challan.', 'invalid')
    hob = ho(company)
    L = [{'account': OUTPUT[h], 'debit': s['liability'][h], 'branch': hob} for h in HEADS if s['liability'][h] > 0.004]
    a, b = period(ym)
    legacy = float(db.val('''SELECT COALESCE(SUM(l.debit-l.credit), 0) FROM journal_lines l JOIN journals j ON j.id=l.journal_id
                             WHERE j.company=:c AND j.txn_date<=:b AND l.account='1310' ''', c=company, b=b) or 0)
    for f, tos in s['use'].items():
        used = round(sum(tos.values()), 2)
        if used <= 0.004:
            continue
        if f in ('cgst', 'sgst') and legacy > 0.004:      # the older combined input ledger (1310) is used before the split ledgers
            x = min(legacy, used)
            L.append({'account': '1310', 'credit': round(x, 2), 'branch': hob})
            legacy, used = round(legacy - x, 2), round(used - x, 2)
        if used > 0.004:
            L.append({'account': INPUT[f], 'credit': used, 'branch': hob})
    if s['cash']['rcm'] > 0.004:
        L.append({'account': '2140', 'debit': s['cash']['rcm'], 'branch': hob})
    if extra > 0:
        L.append({'account': '5350', 'debit': extra, 'branch': hob})
    cash = round(s['cash']['total'] + extra, 2)
    if cash > 0.004:
        L.append({'account': ch.get('bank') or '1120', 'credit': cash, 'branch': hob})
    d = dt.date.fromisoformat(ch['date']) if ch.get('date') else dt.date.today()
    cid = db.insert('gst_challans', dict(company=company, period=ym, cpin=(ch.get('cpin') or '').strip() or None, cin=(ch.get('cin') or '').strip() or None, cdate=d,
                                         bank_account=ch.get('bank') or '1120', cash=json.dumps(s['cash']), used=json.dumps(s['use']), interest=s['interest'],
                                         late_fee=s['fee'], status='Pending'))
    v = fv.create({'vtype': 'GA', 'company': company, 'vdate': str(d), 'branch_id': hob, 'dept': 'D-ACC', 'ref': 'GSTR-3B ' + fp(ym),
                   'narration': f'GST set-off and payment for {a:%b %Y}' + (f" · CPIN {ch['cpin']}" if ch.get('cpin') else ''),
                   'lines': L, 'options': {'on_post': 'gst_challan', 'challan_id': cid}})
    db.run('UPDATE gst_challans SET voucher_no=:v WHERE id=:i', v=v.no, i=cid)
    return fv.submit(v.id, direct=bool(ch.get('direct')))


def _challan_posted(v):
    db.run("UPDATE gst_challans SET status='Paid', journal_no=:j WHERE id=:i", j=v.journal_no, i=(v.options or {}).get('challan_id'))


def _challan_dropped(v):
    db.run("UPDATE gst_challans SET status='Cancelled' WHERE id=:i", i=(v.options or {}).get('challan_id'))


def file_return(company, ret, ym, arn, filed_on=None):
    need_ym(ym)
    arn = txt(arn)
    if not can(g.user, 'submit', 'fin'):
        raise denied('Your role cannot record return filing.', 'forbidden')
    if ret not in ('1', '3B', '9', '9C'):
        raise bad('Return must be GSTR-1, 3B, 9 or 9C.', 'invalid')
    if not (arn or '').strip():
        raise bad('Enter the ARN (acknowledgement) from the GST portal.', 'invalid')
    if ret == '3B':
        s = setoff(company, ym)
        if s['cash']['total'] + s['interest'] + s['fee'] > 0.5 and not s['paid']:
            raise conflict(f"Pay the GST of ₹{s['cash']['total'] + s['interest'] + s['fee']:,.0f} for {ym} first.", 'unpaid')
    lock_company(company)
    if db.one('SELECT 1 FROM gst_filings WHERE company=:c AND ret=:r AND period=:p FOR UPDATE', c=company, r=ret, p=ym):
        raise conflict(f'GSTR-{ret} for {ym} is already filed.', 'duplicate')
    db.insert('gst_filings', dict(company=company, ret=ret, period=ym, filed_on=filed_on or dt.date.today(), arn=arn.strip()[:20], filed_by=g.user['id']))
    audit.write('GST return filed', f'GSTR-{ret} {fp(ym)}', new=arn, category='finance', company=company)
    if ret == '3B':
        notify.alert('GST return filed', f'GSTR-3B {ym} filed ({arn}). Entries dated up to {period(ym)[1]:%d-%m-%Y} now need CFO / Director approval.',
                     ref=f'GSTR-3B {ym}', company=company, severity='info')


# ---------- e-invoice & e-way bill ----------
def einvoice_applies(company, d):
    on = db.val('SELECT einvoice FROM gst_registrations WHERE company=:c AND state=:s', c=company, s=HOME_STATE)
    return d['dir'] == 'out' and bool(d['gstin']) and bool(on) and d['type'] in ('INV', 'CN', 'DN')


def _find(company, no):
    d = next((x for x in docs(company, dt.date(2000, 1, 1), dt.date(2999, 12, 31)) if x['no'] == no and x['dir'] == 'out'), None)
    if not d:
        raise not_found('Document not found.')
    return d


def einvoice(company, no):
    d = _find(company, no)
    if not einvoice_applies(company, d):
        raise conflict('E-invoice applies to B2B documents of a company with e-invoicing on.', 'not_applicable')
    e0 = db.one('SELECT * FROM einvoices WHERE doc_no=:n', n=no)
    if e0 and e0['status'] == 'Generated':
        raise conflict('IRN already generated.', 'duplicate')
    fy = fy_of(company, dt.date.fromisoformat(d['date'][:10]))['code']
    typ = {'CN': 'CRN', 'DN': 'DBN'}.get(d['type'], 'INV')
    gstin = gstin_of(company)
    irn = hashlib.sha256((gstin + fy + typ + no.upper()).encode()).hexdigest()
    ack = str(int(irn[:12], 16))[:15].rjust(15, '1')
    main = (max(d['rows'], key=lambda r: r['taxable']) if d['rows'] else {}).get('hsn', '')
    now = dt.datetime.now()
    qr = json.dumps({'SellerGstin': gstin, 'BuyerGstin': d['gstin'], 'DocNo': no, 'DocTyp': typ, 'DocDt': dt.date.fromisoformat(d['date'][:10]).strftime('%d/%m/%Y'),
                     'TotInvVal': d['val'], 'ItemCnt': len(d['rows']), 'MainHsnCode': main, 'Irn': irn, 'IrnDt': now.strftime('%Y-%m-%d %H:%M:%S')})
    if e0:
        db.run("UPDATE einvoices SET irn=:i, ack_no=:a, ack_at=:t, status='Generated', signed_qr=:q, local=1, cancel_reason=NULL WHERE doc_no=:n",
               i=irn, a=ack, t=now, q=qr, n=no)
    else:
        db.insert('einvoices', dict(doc_no=no, company=company, irn=irn, ack_no=ack, ack_at=now, status='Generated', signed_qr=qr, local=1))
    audit.write('E-invoice IRN generated', no, new=irn[:16] + '…', category='finance', company=company)
    return db.clean(db.one('SELECT * FROM einvoices WHERE doc_no=:n', n=no))


def einvoice_cancel(no, reason):
    e = db.one('SELECT * FROM einvoices WHERE doc_no=:n', n=no)
    if not e or e['status'] != 'Generated' or e['company'] not in g.user['companies']:
        raise conflict('No active IRN.', 'wrong_status')
    if not (reason or '').strip():
        raise bad('Give the reason (duplicate, data entry mistake, order cancelled or other).', 'reason_required')
    if dt.datetime.now() - e['ack_at'] > dt.timedelta(hours=24):
        raise conflict('An IRN can be cancelled only within 24 hours. Issue a credit note instead.', 'too_late')
    db.run("UPDATE einvoices SET status='Cancelled', cancel_reason=:r WHERE doc_no=:n", r=reason.strip()[:120], n=no)
    audit.write('E-invoice cancelled', no, new=reason, category='finance', company=e['company'])


def eway(company, no, o):
    _find(company, no)
    d = dt.date.fromisoformat(o['date']) if o.get('date') else dt.date.today()
    if o.get('generate'):
        n12 = str(int(hashlib.sha256((no + str(dt.datetime.now())).encode()).hexdigest()[:12], 16))[:12].rjust(12, '3')
    else:
        n12 = re.sub(r'\D', '', str(o.get('no') or ''))
    if len(n12) != 12:
        raise bad('An e-way bill number has 12 digits.', 'invalid')
    km = int(o.get('km') or 0)
    ewb = {'no': n12, 'date': str(d), 'vehicle': (o.get('vehicle') or '').upper(), 'km': km, 'valid': str(d + dt.timedelta(days=max(1, -(-max(km, 1) // 200)))),
           'local': bool(o.get('generate'))}
    if db.one('SELECT 1 FROM einvoices WHERE doc_no=:n', n=no):
        db.run('UPDATE einvoices SET ewb=:e WHERE doc_no=:n', e=json.dumps(ewb), n=no)
    else:
        db.insert('einvoices', dict(doc_no=no, company=company, status='Not required', ewb=json.dumps(ewb), local=1))
    audit.write('E-way bill recorded', no, new=n12, category='finance', company=company)
    return ewb


# ---------- GSTR-2B / 2A reconciliation ----------
def _norm(s):
    return re.sub(r'^0+', '', re.sub(r'[^A-Z0-9]', '', str(s or '').upper()))


def portal_import(company, kind, ym, table, source='Imported file'):
    """table: rows of cells from the portal download (header row with GSTIN, invoice number, date, taxable value, IGST, CGST, SGST, cess)."""
    hi = next((i for i, r in enumerate(table) if any(re.search('gstin', str(c), re.I) for c in r)), -1)
    if hi < 0:
        raise bad('The file needs a GSTIN column (GSTIN of supplier, Invoice number, Invoice date, Taxable value, IGST, CGST, SGST, Cess).', 'invalid_file')
    H = [str(c).lower() for c in table[hi]]

    def ix(*names):
        return next((i for i, h in enumerate(H) if any(n in h for n in names)), -1)
    c = {'g': ix('gstin'), 'n': ix('trade', 'name'), 'no': ix('invoice number', 'invoice no', 'document number', 'inv'), 'd': ix('date'), 't': ix('taxable'),
         'i': ix('integrated', 'igst'), 'c': ix('central', 'cgst'), 's': ix('state', 'sgst', 'utgst'), 'x': ix('cess')}
    if c['no'] < 0 or c['t'] < 0:
        raise bad('Could not find the invoice number and taxable value columns.', 'invalid_file')

    def num(r, k):
        try:
            return float(str(r[c[k]]).replace(',', '')) if c[k] >= 0 and r[c[k]] not in (None, '') else 0.0
        except (ValueError, IndexError):
            return 0.0

    def date(s):
        s = str(s or '').strip()
        m = re.fullmatch(r'(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})', s)
        try:
            return dt.date(int(m[3]), int(m[2]), int(m[1])) if m else dt.date.fromisoformat(s[:10])
        except ValueError:
            return None
    db.run('DELETE FROM gst_portal_rows WHERE company=:c AND kind=:k AND period=:p', c=company, k=kind, p=ym)
    n = 0
    for r in table[hi + 1:]:
        if len(r) <= max(c['g'], c['no']) or not r[c['g']] or not r[c['no']]:
            continue
        db.insert('gst_portal_rows', dict(company=company, kind=kind, period=ym, gstin=str(r[c['g']]).strip().upper()[:15], name=str(r[c['n']])[:120] if c['n'] >= 0 else None,
                                          inv_no=str(r[c['no']]).strip()[:30], inv_date=date(r[c['d']]) if c['d'] >= 0 else None, taxable=num(r, 't'), igst=num(r, 'i'),
                                          cgst=num(r, 'c'), sgst=num(r, 's'), cess=num(r, 'x'), source=source[:40], loaded_at=dt.datetime.now()))
        n += 1
    audit.write(f'GSTR-{kind} imported', f'{company} {fp(ym)}', new=f'{n} rows', category='finance', company=company)
    return n


def portal_sample(company, kind, ym):
    """Demo rows shaped like the portal download (the portal is not connected): the books' inward invoices with typical differences."""
    a, b = period(ym)
    rows = [d for d in docs(company, a, b) if d['dir'] == 'in' and d['gstin'] and not d['rcm']]
    table = [['GSTIN of supplier', 'Trade name', 'Invoice number', 'Invoice date', 'Taxable value', 'Integrated tax', 'Central tax', 'State/UT tax', 'Cess']]
    for k, d in enumerate(rows):
        if k == 1:
            continue
        cg, sg = (d['cgst'] - 90, d['sgst'] - 90) if k == 0 else (d['cgst'], d['sgst'])
        table.append([d['gstin'], d['name'], d['no'], d['date'], d['taxable'], d['igst'], cg, sg, d['cess']])
    table.append([gstin_fix('36AAFCS9911J1Z'), 'Swift Logistics Pvt Ltd', f'SL/2627/0{400 + len(table)}', f'{ym}-12', 12000, 0, 1080, 1080, 0])
    return portal_import(company, kind, ym, table, 'Sample (portal not connected)')


def reconcile(company, kind, ym):
    a, b = period(ym)
    books = [d for d in docs(company, a, b) if d['dir'] == 'in' and d['gstin'] and not d['rcm'] and d['type'] != 'DN']
    pr = db.clean(db.q('SELECT * FROM gst_portal_rows WHERE company=:c AND kind=:k AND period=:p ORDER BY id', c=company, k=kind, p=ym))
    tol = cfg()['gst']['tolerance']
    used, out = set(), []
    for d in books:
        k = next((i for i, r in enumerate(pr) if i not in used and r['gstin'] == d['gstin'] and _norm(r['inv_no']) == _norm(d['no'])), -1)
        if k < 0:
            out.append({'status': f'In books, not in {kind}', 'books': d, 'portal': None, 'diff': d['tax']})
            continue
        used.add(k)
        p = pr[k]
        pt = round(p['igst'] + p['cgst'] + p['sgst'] + p['cess'], 2)
        dd, dtx = round(d['tax'] - pt, 2), round(d['taxable'] - p['taxable'], 2)
        out.append({'status': 'Matched' if abs(dd) <= tol and abs(dtx) <= tol else 'Amount mismatch', 'books': d, 'portal': p, 'diff': dd, 'diff_taxable': dtx})
    for i, p in enumerate(pr):
        if i not in used:
            out.append({'status': f'In {kind}, not in books', 'books': None, 'portal': p, 'diff': -round(p['igst'] + p['cgst'] + p['sgst'] + p['cess'], 2)})
    bad_ = [r for r in out if r['status'] != 'Matched']
    return {'rows': out, 'loaded': bool(pr), 'source': pr[0]['source'] if pr else '', 'loaded_at': pr[0]['loaded_at'] if pr else None,
            'mismatches': len(bad_), 'itc_at_risk': round(sum(max(0, r['diff']) for r in bad_ if r['books']), 2)}


def reconcile_alert(company, kind, ym):
    R = reconcile(company, kind, ym)
    bad_ = [r for r in R['rows'] if r['status'] != 'Matched']
    if bad_:
        names = list(dict.fromkeys((r['books'] or r['portal']).get('name') or (r['books'] or r['portal'])['gstin'] for r in bad_))
        notify.alert('GST mismatch', f"GSTR-{kind} {ym}: {len(bad_)} mismatch(es) with {len(names)} supplier(s) — {', '.join(names[:3])}. "
                                     f"ITC at risk ₹{R['itc_at_risk']:,.0f}.", ref=f'GSTR-{kind} {ym}', company=company, severity='warn')
    return len(bad_)


# ---------- returns ----------
def _add(o, r, s=1):
    for h in ('taxable',) + HEADS:
        o[h] = round(o[h] + s * r[h], 2)


def gstr1(company, ym):
    a, b = period(ym)
    ds = [d for d in docs(company, a, b) if d['dir'] == 'out' and d['type'] != 'ADV']
    lim = cfg()['gst']['b2cl_limit']
    big_inter = lambda d: not d['gstin'] and d['pos'] != HOME_STATE and d['val'] > lim  # noqa: E731
    b2b = [d for d in ds if d['type'] == 'INV' and d['gstin']]
    b2cl = [d for d in ds if d['type'] == 'INV' and big_inter(d)]
    b2cs, nil = {}, {'intra_b2c': 0, 'intra_b2b': 0, 'inter_b2c': 0, 'inter_b2b': 0}
    for d in ds:
        if d['gstin'] or big_inter(d):
            continue
        for r in d['rows']:
            if r['rate'] == 0:
                nil[('intra' if d['pos'] == HOME_STATE else 'inter') + '_b2c'] += d['sign'] * r['taxable']
                continue
            x = b2cs.setdefault((d['pos'], r['rate']), {'pos': d['pos'], 'rate': r['rate'], **ZERO})
            _add(x, r, d['sign'])
    for d in b2b:
        for r in d['rows']:
            if r['rate'] == 0:
                nil[('intra' if d['pos'] == HOME_STATE else 'inter') + '_b2b'] += r['taxable']
    cdnr = [d for d in ds if d['type'] != 'INV' and d['gstin']]
    cdnur = [d for d in ds if d['type'] == 'CN' and big_inter(d)]
    desc = {r['code']: r['description'] for r in db.q('SELECT code, description FROM hsn_codes')}

    def hsn(f):
        m = {}
        for d in filter(f, ds):
            for r in d['rows']:
                x = m.setdefault((r['hsn'], r['rate']), {'hsn': r['hsn'] or 'NA', 'description': desc.get(r['hsn'], ''), 'uqc': r['uqc'], 'qty': 0, 'rate': r['rate'], **ZERO})
                x['qty'] += d['sign'] * (r['qty'] or 0)
                _add(x, r, d['sign'])
        return list(m.values())
    series = {}
    for d in ds:
        mm = re.search(r'(\d+)$', d['no'])
        n = int(mm[1]) if mm else 0
        s = d['no'][:mm.start()] if mm else d['no']
        x = series.setdefault(('Credit note' if d['type'] == 'CN' else 'Invoice', s), {'nature': 'Credit note' if d['type'] == 'CN' else 'Invoice', 'series': s,
                                                                                         'from': n, 'to': n, 'count': 0, 'cancelled': 0})
        x['from'], x['to'], x['count'] = min(x['from'], n), max(x['to'], n), x['count'] + 1
        x['cancelled'] += d.get('status') == 'Cancelled'
    return {'gstin': gstin_of(company), 'fp': fp(ym), 'b2b': b2b, 'b2cl': b2cl, 'b2cs': list(b2cs.values()), 'cdnr': cdnr, 'cdnur': cdnur,
            'nil': {k: round(x, 2) for k, x in nil.items()}, 'hsn_b2b': hsn(lambda d: bool(d['gstin'])), 'hsn_b2c': hsn(lambda d: not d['gstin']),
            'docs': list(series.values()), 'filed': db.clean(db.one("SELECT * FROM gst_filings WHERE company=:c AND ret='1' AND period=:p", c=company, p=ym))}


def _gdate(s):
    return f'{s[8:10]}-{s[5:7]}-{s[:4]}'


def gstr1_json(company, ym):
    G = gstr1(company, ym)
    it = lambda rows: [{'num': k + 1, 'itm_det': {'txval': r['taxable'], 'rt': r['rate'], 'iamt': r['igst'], 'camt': r['cgst'], 'samt': r['sgst'],  # noqa: E731
                                                  'csamt': r['cess']}} for k, r in enumerate(rows)]
    by, cn, pl = {}, {}, {}
    for d in G['b2b']:
        by.setdefault(d['gstin'], []).append({'inum': d['no'], 'idt': _gdate(d['date']), 'val': d['val'], 'pos': d['pos'], 'rchrg': 'N', 'inv_typ': 'R', 'itms': it(d['rows'])})
    for d in G['cdnr']:
        cn.setdefault(d['gstin'], []).append({'ntty': 'C' if d['type'] == 'CN' else 'D', 'nt_num': d['no'], 'nt_dt': _gdate(d['date']), 'val': d['val'], 'pos': d['pos'],
                                              'rchrg': 'N', 'inv_typ': 'R', 'itms': it(d['rows'])})
    for d in G['b2cl']:
        pl.setdefault(d['pos'], []).append({'inum': d['no'], 'idt': _gdate(d['date']), 'val': d['val'], 'itms': it(d['rows'])})
    hs = lambda rows: [{'num': k + 1, 'hsn_sc': h['hsn'], 'desc': h['description'], 'uqc': h['uqc'], 'qty': h['qty'], 'rt': h['rate'], 'txval': h['taxable'],  # noqa: E731
                        'iamt': h['igst'], 'camt': h['cgst'], 'samt': h['sgst'], 'csamt': h['cess']} for k, h in enumerate(rows)]
    n = G['nil']
    return {'gstin': G['gstin'], 'fp': G['fp'], 'version': 'GST3.2.1', 'hash': 'hash',
            'b2b': [{'ctin': k, 'inv': v} for k, v in by.items()], 'b2cl': [{'pos': k, 'inv': v} for k, v in pl.items()],
            'b2cs': [{'sply_ty': 'INTRA' if x['pos'] == HOME_STATE else 'INTER', 'pos': x['pos'], 'typ': 'OE', 'txval': x['taxable'], 'rt': x['rate'], 'iamt': x['igst'],
                      'camt': x['cgst'], 'samt': x['sgst'], 'csamt': x['cess']} for x in G['b2cs']],
            'cdnr': [{'ctin': k, 'nt': v} for k, v in cn.items()],
            'nil': {'inv': [{'sply_ty': 'INTRB2C', 'expt_amt': 0, 'nil_amt': n['inter_b2c'], 'ngsup_amt': 0}, {'sply_ty': 'INTRAB2B', 'expt_amt': 0, 'nil_amt': n['intra_b2b'], 'ngsup_amt': 0},
                            {'sply_ty': 'INTRB2B', 'expt_amt': 0, 'nil_amt': n['inter_b2b'], 'ngsup_amt': 0}, {'sply_ty': 'INTRAB2C', 'expt_amt': 0, 'nil_amt': n['intra_b2c'], 'ngsup_amt': 0}]},
            'hsn': {'hsn_b2b': hs(G['hsn_b2b']), 'hsn_b2c': hs(G['hsn_b2c'])},
            'doc_issue': {'doc_det': [{'doc_num': 1, 'docs': [{'num': k + 1, 'from': f"{d['series']}{d['from']}", 'to': f"{d['series']}{d['to']}", 'totnum': d['count'],
                                                                 'cancel': d['cancelled'], 'net_issue': d['count'] - d['cancelled']} for k, d in enumerate(G['docs'])]}]}}


def gstr3b(company, ym):
    a, b = period(ym)
    z = lambda: dict(ZERO)  # noqa: E731
    t31a, t31c, t31d, t5, itc_r, itc_o, itc_b, inter = z(), z(), z(), z(), z(), z(), z(), {}
    for d in docs(company, a, b):
        for r in d['rows']:
            if d['dir'] == 'out':
                _add(t31c if r['rate'] == 0 else t31a, r, d['sign'])
                if not d['gstin'] and d['pos'] != HOME_STATE and r['rate'] > 0:
                    x = inter.setdefault(d['pos'], {'pos': d['pos'], 'taxable': 0, 'igst': 0})
                    x['taxable'], x['igst'] = round(x['taxable'] + d['sign'] * r['taxable'], 2), round(x['igst'] + d['sign'] * r['igst'], 2)
            elif r['rate'] == 0:
                _add(t5, r)
            elif d['rcm']:
                _add(t31d, r)
                _add(itc_r if d['itc'] == 'Eligible' else itc_b, r)
            else:
                _add(itc_o if d['itc'] == 'Eligible' else itc_b, r, d['sign'] if d['itc'] == 'Eligible' else 1)
    s = setoff(company, ym)
    return {'gstin': gstin_of(company), 'ym': ym, 't31a': t31a, 't31c': t31c, 't31d': t31d, 't32': list(inter.values()), 'itc_rcm': itc_r, 'itc_other': itc_o,
            'itc_blocked': itc_b, 't5': t5, 'setoff': s}


def gstr3b_json(company, ym):
    G = gstr3b(company, ym)
    t = lambda o: {'txval': o['taxable'], 'iamt': o['igst'], 'camt': o['cgst'], 'samt': o['sgst'], 'csamt': o['cess']}  # noqa: E731
    i = lambda o: {'iamt': o['igst'], 'camt': o['cgst'], 'samt': o['sgst'], 'csamt': o['cess']}  # noqa: E731
    net = {h: round(G['itc_rcm'][h] + G['itc_other'][h], 2) for h in HEADS}
    s = G['setoff']
    return {'gstin': G['gstin'], 'ret_period': fp(ym),
            'sup_details': {'osup_det': t(G['t31a']), 'osup_zero': t(ZERO), 'osup_nil_exmp': {'txval': G['t31c']['taxable']}, 'isup_rev': t(G['t31d']), 'osup_nongst': {'txval': 0}},
            'inter_sup': {'unreg_details': [{'pos': x['pos'], 'txval': x['taxable'], 'iamt': x['igst']} for x in G['t32']]},
            'itc_elg': {'itc_avl': [{'ty': 'IMPG', **i(ZERO)}, {'ty': 'IMPS', **i(ZERO)}, {'ty': 'ISRC', **i(G['itc_rcm'])}, {'ty': 'ISD', **i(ZERO)}, {'ty': 'OTH', **i(G['itc_other'])}],
                        'itc_rev': [], 'itc_net': i(net), 'itc_inelg': [{'ty': 'RUL', **i(G['itc_blocked'])}]},
            'inward_sup': {'isup_details': [{'ty': 'GST', 'inter': 0, 'intra': G['t5']['taxable']}]},
            'intr_ltfee': {'intr_details': {'iamt': 0, 'camt': s['interest'] / 2, 'samt': s['interest'] / 2, 'csamt': 0}, 'ltfee_details': {'camt': s['fee'] / 2, 'samt': s['fee'] / 2}}}


def gstr9(company, fy_code):
    fy = db.one('SELECT * FROM fin_years WHERE company=:c AND code=:f', c=company, f=fy_code) or fy_of(company, dt.date.today())
    end = min(fy['to_date'], dt.date.today())
    rows, m = [], f"{fy['from_date']:%Y-%m}"
    while m <= f'{end:%Y-%m}':
        G = gstr3b(company, m)
        rows.append({'ym': m, 'out': G['t31a'], 'nil': G['t31c']['taxable'], 'rcm': G['t31d'], 'itc': {h: round(G['itc_rcm'][h] + G['itc_other'][h], 2) for h in HEADS},
                     'blocked': G['itc_blocked'], 'cash': G['setoff']['cash']['total'], 'filed': bool(G['setoff']['filed'])})
        m = next_ym(m)
    T = lambda f, h: round(sum(f(r)[h] for r in rows), 2)  # noqa: E731
    return {'fy': fy_code, 'from': str(fy['from_date']), 'to': str(fy['to_date']), 'rows': rows,
            'out': {h: T(lambda r: r['out'], h) for h in ('taxable', 'igst', 'cgst', 'sgst')}, 'nil': round(sum(r['nil'] for r in rows), 2),
            'rcm': {'taxable': T(lambda r: r['rcm'], 'taxable'), 'tax': round(sum(T(lambda r: r['rcm'], h) for h in ('igst', 'cgst', 'sgst')), 2)},
            'itc': {h: T(lambda r: r['itc'], h) for h in ('igst', 'cgst', 'sgst')}, 'blocked': round(sum(T(lambda r: r['blocked'], h) for h in ('igst', 'cgst', 'sgst')), 2),
            'cash': round(sum(r['cash'] for r in rows), 2)}


def gstr9c(company, fy_code):
    G = gstr9(company, fy_code)
    end = min(dt.date.fromisoformat(G['to']), dt.date.today())
    bal = {r['account']: float(r['v']) for r in db.q('''SELECT l.account, SUM(l.debit-l.credit) v FROM journal_lines l JOIN journals j ON j.id=l.journal_id
                                                       WHERE j.company=:c AND j.txn_date BETWEEN :a AND :b GROUP BY l.account''', c=company, a=G['from'], b=end)}
    books = round(-(bal.get('4000', 0) + bal.get('4010', 0) + bal.get('4020', 0)), 2)
    adv = float(db.val("""SELECT COALESCE(SUM(taxable), 0) FROM fin_vouchers WHERE company=:c AND vtype='RV' AND status='Posted' AND tax>0 AND vdate BETWEEN :a AND :b""",
                       c=company, a=G['from'], b=end) or 0)
    ret = round(G['out']['taxable'] + G['nil'], 2)
    items = [['Turnover as per audited books (sales less returns, incl. inter-company)', books], ['Add: advances on which GST was paid, not yet invoiced', adv],
             ['Less: other income not a supply (interest etc.)', 0], ['Adjusted turnover', round(books + adv, 2)],
             ['Turnover declared in GSTR-9 (taxable + nil-rated)', ret], ['Un-reconciled difference', round(books + adv - ret, 2)]]
    return {'gstr9': G, 'items': items, 'other_income': round(-(bal.get('4100', 0) + bal.get('4110', 0)), 2),
            'tax_books': round(-(bal.get('2100', 0) + bal.get('2110', 0) + bal.get('2120', 0)), 2),
            'itc_books': round(sum(bal.get(a, 0) for a in ('1310', '1311', '1312', '1313')), 2)}


def summary(company, ym):
    """The GST tab header: liability, credit, cash, due dates and filing status for a month."""
    s = setoff(company, ym)
    filed = {r['ret']: db.clean(r) for r in db.q('SELECT * FROM gst_filings WHERE company=:c AND period=:p', c=company, p=ym)}
    return {'ym': ym, 'setoff': s, 'filed': filed, 'challan': db.clean(db.one("SELECT * FROM gst_challans WHERE company=:c AND period=:p AND status<>'Cancelled'", c=company, p=ym)),
            'gstin': gstin_of(company)}


def _register_hooks():
    from . import fin_vouchers as fv
    fv.ON_POST['gst_challan'] = _challan_posted
    fv.ON_DROP['gst_challan'] = _challan_dropped


_register_hooks()
