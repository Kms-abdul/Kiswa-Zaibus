"""Finance & tax: TDS / TCS. From 1 April 2026 the Income-tax Act, 2025 applies — TDS in s.392 (salary) and s.393, TCS in s.394, and
the forms are renumbered (24Q → Form 138, 26Q → 140, 27Q → 144, 27EQ → 143, Form 16 → 130, Form 16A → 131, challan → Form 141).
The familiar section codes stay as labels with the new reference beside them; rates and thresholds live in the section master."""
import datetime as dt
import hashlib
import json
import re
from decimal import Decimal

from flask import g

from .. import db
from ..core import audit
from ..errors import bad, conflict, not_found
from .fin_common import cfg, fy_of, gstin_check, pan_check, r2

FORMS = {'24Q': ('Form 138', 'Salary TDS return (old 24Q)'), '26Q': ('Form 140', 'Non-salary TDS, residents (old 26Q)'),
         '27Q': ('Form 144', 'TDS on non-residents (old 27Q)'), '27EQ': ('Form 143', 'TCS return (old 27EQ)')}


def section(code):
    return db.one('SELECT * FROM tds_sections WHERE code=:c', c=code)


def party_tax(company, party):
    r = db.one('SELECT * FROM party_tax WHERE company=:c AND party=:p', c=company, p=party)
    if r and isinstance(r.get('ldc'), str):
        r['ldc'] = json.loads(r['ldc']) if r['ldc'] else None
    return r or {}


def _fy_sum(company, party, sec, d, field, exclude=None):
    fy = fy_of(company, d)
    return Decimal(str(db.val(f'''SELECT COALESCE(SUM({field}), 0) FROM tds_entries WHERE company=:c AND party=:p AND section=:s AND ded_date BETWEEN :a AND :b
                                 AND status<>'Reversed' AND (:x IS NULL OR voucher_no<>:x)''', c=company, p=party, s=sec, a=fy['from_date'], b=fy['to_date'], x=exclude) or 0))


def salary_tds(gross):
    """Monthly TDS on salary under the new regime: (annual gross − ₹75,000 standard deduction) on the slabs, rebate up to ₹12 lakh, 4 % cess."""
    annual = Decimal(str(gross)) * 12 - 75000
    if annual <= 1200000:
        return 0
    t, lo = Decimal(0), 0
    for hi, rate in ((400000, 0), (800000, 5), (1200000, 10), (1600000, 15), (2000000, 20), (2400000, 25), (10 ** 12, 30)):
        if annual > lo:
            t += (min(annual, Decimal(hi)) - lo) * Decimal(rate) / 100
        lo = hi
    return int(round(t * Decimal('1.04') / 12))


def calc(company, party, sec_code, base, d, exclude=None):
    """TDS for one bill: thresholds (single / year, monthly for rent), lower-deduction certificate, no-PAN higher rate (s.206AA),
    194Q above ₹50 lakh, catch-up when the year limit is crossed after earlier bills went without TDS."""
    s = section(sec_code)
    if not s:
        return {'amount': 0, 'rate': 0, 'note': 'Choose a TDS section'}
    if not s['active']:
        return {'amount': 0, 'rate': 0, 'note': f"{s['code']} is switched off in the section master"}
    if s['effective_from'] and d < s['effective_from']:
        return {'amount': 0, 'rate': 0, 'note': f"Section rate effective from {s['effective_from']:%d-%m-%Y}"}
    base = r2(base)
    if s['slab']:
        e = db.one('SELECT gross FROM employees WHERE id=:i', i=party)
        m = salary_tds(e['gross']) if e else 0
        return {'amount': m, 'rate': float(r2(Decimal(m) / base * 100)) if base else 0, 'note': 'Salary: estimated annual tax ÷ 12 (new regime)'}
    p = party_tax(company, party)
    pc = pan_check(p.get('pan'))
    rate = Decimal(str(s['rate_ind'] if pc['ok'] and pc['individual'] else s['rate_other']))
    note = f"{s['code']} @ {rate}%"
    ldc = p.get('ldc')
    if ldc and (ldc.get('section') or ldc.get('sec')) == s['code'] and str(ldc.get('from')) <= str(d) <= str(ldc.get('to')):
        used = _fy_sum(company, party, s['code'], d, 'base', exclude)
        if used + base <= Decimal(str(ldc.get('limit') or 0)):
            rate = Decimal(str(ldc['rate']))
            note = f"Lower deduction certificate {ldc.get('no')} @ {rate}%"
        else:
            note += f" (certificate {ldc.get('no')} limit exhausted)"
    nr = s['non_resident'] or p.get('resident') in (0, False)
    if not pc['ok'] and p.get('trc') and nr:
        note += ' · no PAN, but TRC and Form 10F on file: normal rate'
    elif not pc['ok']:
        rate = Decimal(str(s['no_pan_rate'] or max(cfg()['tds']['no_pan_rate'], float(rate) * 2)))
        note = f"PAN {'invalid' if p.get('pan') else 'not available'}: higher rate {rate}% (s.206AA)"
    if nr:
        note += ' · non-resident (Form 144)'
    prev = _fy_sum(company, party, s['code'], d, 'base', exclude)
    prev_ded = _fy_sum(company, party, s['code'], d, 'amount', exclude)
    single, year = Decimal(str(s['single_limit'] or 0)), Decimal(str(s['year_limit'] or 0))
    if s['monthly']:
        if base < single:
            return {'amount': 0, 'rate': float(rate), 'note': f'Below ₹{single:,.0f} per month threshold'}
        return {'amount': int(round(base * rate / 100)), 'rate': float(rate), 'note': note}
    if s['code'] == '194Q':
        over = max(Decimal(0), prev + base - year) - max(Decimal(0), prev - year)
        if over > 0:
            return {'amount': int(round(over * rate / 100)), 'rate': float(rate), 'note': note + f' on ₹{over:,.0f} above ₹50 lakh'}
        return {'amount': 0, 'rate': float(rate), 'note': f'Purchases ₹{prev + base:,.0f} this year, below ₹50 lakh'}
    if single or year:
        single_hit, year_hit = bool(single and base > single), bool(year and prev + base > year)
        if not single_hit and not year_hit:
            return {'amount': 0, 'rate': float(rate), 'note': f'Below threshold (single ₹{single:,.0f}, year ₹{year:,.0f}; ₹{prev + base:,.0f} so far)'}
        if year_hit and prev > 0 and prev_ded == 0:
            return {'amount': int(round((prev + base) * rate / 100)), 'rate': float(rate),
                    'note': note + f' · year total crossed ₹{year:,.0f}: TDS on ₹{prev + base:,.0f} including earlier bills'}
    return {'amount': int(round(base * rate / 100)), 'rate': float(rate), 'note': note}


def form_of(sec_code, party_resident=True):
    s = section(sec_code) or {}
    if s.get('tcs'):
        return '27EQ'
    if s.get('code') == '192':
        return '24Q'
    if s.get('non_resident') or party_resident in (0, False):
        return '27Q'
    return '26Q'


def quarter_of(d):
    return 'Q1' if 4 <= d.month <= 6 else 'Q2' if 7 <= d.month <= 9 else 'Q3' if d.month >= 10 else 'Q4'


def q_range(fy_from, q):
    y = fy_from.year
    return {'Q1': (dt.date(y, 4, 1), dt.date(y, 6, 30)), 'Q2': (dt.date(y, 7, 1), dt.date(y, 9, 30)), 'Q3': (dt.date(y, 10, 1), dt.date(y, 12, 31)),
            'Q4': (dt.date(y + 1, 1, 1), dt.date(y + 1, 3, 31))}[q]


def q_due(fy_from, q):
    y = fy_from.year
    return {'Q1': dt.date(y, 7, 31), 'Q2': dt.date(y, 10, 31), 'Q3': dt.date(y + 1, 1, 31), 'Q4': dt.date(y + 1, 5, 31)}[q]


def deposit_due(d):
    """TDS deducted in a month is deposited by the 7th of the next month (30 April for March)."""
    if d.month == 3:
        return dt.date(d.year, 4, 30)
    n = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=7)
    return n


def _months(a, b):
    if b <= a:
        return 0
    return max(1, (b.year - a.year) * 12 + b.month - a.month + 1)


def interest(e, pay_date=None):
    c = cfg()['tds']
    pd = pay_date or dt.date.today()
    amt = Decimal(str(e['amount']))
    late_ded = int(round(amt * Decimal(str(c['late_deduction'])) / 100 * _months(e['doc_date'], e['ded_date']))) \
        if e['doc_date'] and (e['ded_date'].year, e['ded_date'].month) > (e['doc_date'].year, e['doc_date'].month) else 0
    due = deposit_due(e['ded_date'])
    late_pay = int(round(amt * Decimal(str(c['late_payment'])) / 100 * _months(e['ded_date'], pd))) if pd > due else 0
    return {'late_deduction': late_ded, 'late_payment': late_pay, 'total': late_ded + late_pay, 'due': str(due), 'overdue': not e['challan_id'] and dt.date.today() > due}


def register(company, frm=None, to=None, party=None, sec=None, status=None):
    rows = db.clean(db.q('''SELECT * FROM tds_entries WHERE company=:c AND (:f IS NULL OR ded_date>=:f) AND (:t IS NULL OR ded_date<=:t)
                             AND (:p IS NULL OR party=:p) AND (:s IS NULL OR section=:s) AND (:st IS NULL OR status=:st) ORDER BY ded_date DESC, id DESC''',
                         c=company, f=frm, t=to, p=party, s=sec, st=status))
    for r in rows:
        e = dict(r, ded_date=dt.date.fromisoformat(str(r['ded_date'])[:10]), doc_date=dt.date.fromisoformat(str(r['doc_date'] or r['ded_date'])[:10]))
        r['interest'] = interest(e) if r['kind'] == 'Payable' and r['status'] == 'Deducted' else None
        r['form'] = FORMS[form_of(r['section'], (party_tax(company, r['party']) or {}).get('resident', True))][0]
    return rows


def pay_challan(company, ids, ch):
    """Deposit selected deductions: one TDS payment voucher (TP) for tax + interest, the challan record, entries linked to it.
    The challan becomes Paid when the voucher posts (immediately, or after approval)."""
    from . import fin_vouchers as fv
    es = db.q('''SELECT * FROM tds_entries WHERE company=:c AND id IN :i AND challan_id IS NULL AND kind='Payable' AND status='Deducted' FOR UPDATE''',
              c=company, i=tuple(int(x) for x in ids) or (0,))
    if not es:
        raise bad('Choose the deductions to pay.', 'nothing_selected')
    if not re.fullmatch(r'\d{7}', str(ch.get('bsr') or '')):
        raise bad('BSR code has 7 digits.', 'invalid')
    if not re.fullmatch(r'\d{1,5}', str(ch.get('serial') or '')):
        raise bad('Enter the challan serial number (up to 5 digits).', 'invalid')
    d = dt.date.fromisoformat(ch['date']) if ch.get('date') else dt.date.today()
    amt = r2(sum(Decimal(str(e['amount'])) for e in es))
    intr = r2(sum(interest(e, d)['total'] for e in es))
    hob = f'{company}-HO'
    lines = [{'account': '2300', 'branch': hob, 'debit': float(amt)}]
    if intr:
        lines.append({'account': '5350', 'branch': hob, 'debit': float(intr)})
    lines.append({'account': ch.get('bank') or '1120', 'branch': hob, 'credit': float(amt + intr)})
    cin = f"{ch['bsr']}{d:%d%m%Y}{int(ch['serial']):05d}"
    secs = sorted({e['section'] for e in es})
    cid = db.insert('tds_challans', dict(company=company, bsr=str(ch['bsr']), serial=str(ch['serial']), cdate=d, cin=cin, amount=amt, interest=intr,
                                         sections=json.dumps(secs), status='Pending'))
    db.run('UPDATE tds_entries SET challan_id=:c WHERE id IN :i', c=cid, i=tuple(e['id'] for e in es))
    v = fv.create({'vtype': 'TP', 'company': company, 'vdate': str(d), 'branch_id': hob, 'dept': 'D-ACC', 'ref': 'CIN ' + cin,
                   'narration': f"TDS deposited ({', '.join(secs)}) · {len(es)} deductions" + (f' · interest ₹{intr}' if intr else ''),
                   'lines': lines, 'options': {'on_post': 'tds_challan', 'challan_id': cid}})
    db.run('UPDATE tds_challans SET voucher_no=:v WHERE id=:i', v=v.no, i=cid)
    return fv.submit(v.id)


def on_challan_posted(v):
    cid = (v.options or {}).get('challan_id')
    db.run("UPDATE tds_challans SET status='Paid' WHERE id=:i", i=cid)
    db.run("UPDATE tds_entries SET status='Deposited' WHERE challan_id=:i", i=cid)


def on_challan_dropped(v):
    cid = (v.options or {}).get('challan_id')
    db.run("UPDATE tds_challans SET status='Cancelled' WHERE id=:i", i=cid)
    db.run('UPDATE tds_entries SET challan_id=NULL WHERE challan_id=:i', i=cid)


def quarter(company, fy_code, q, form):
    fy = db.one('SELECT * FROM fin_years WHERE company=:c AND code=:f', c=company, f=fy_code)
    if q not in ('Q1', 'Q2', 'Q3', 'Q4') or form not in FORMS:
        raise bad('Choose the quarter (Q1–Q4) and the form.', 'invalid')
    if not fy:
        raise not_found('Financial year not found.')
    a, b = q_range(fy['from_date'], q)
    es = [e for e in db.q("""SELECT e.*, c.status AS challan_status, c.cin FROM tds_entries e LEFT JOIN tds_challans c ON c.id=e.challan_id
                             WHERE e.company=:c AND e.kind='Payable' AND e.status<>'Reversed' AND e.amount>0 AND e.ded_date BETWEEN :a AND :b""",
                          c=company, a=a, b=b)
          if form_of(e['section'], (party_tax(company, e['party']) or {}).get('resident', True)) == form]
    R = db.clean(db.one('SELECT * FROM tds_returns WHERE company=:c AND fy=:f AND form=:m AND quarter=:q', c=company, f=fy_code, m=form, q=q))
    due = q_due(fy['from_date'], q)
    tot = r2(sum(Decimal(str(e['amount'])) for e in es))
    filed = dt.date.fromisoformat(str(R['filed_on'])[:10]) if R else dt.date.today()
    days = max(0, (filed - due).days)
    fee = min(tot, Decimal(days * cfg()['tds']['fee_234e'])) if es else 0
    und = [e for e in es if e['challan_status'] != 'Paid']
    return {'company': company, 'fy': fy_code, 'quarter': q, 'form': form, 'form_name': FORMS[form][0], 'form_label': FORMS[form][1], 'from': str(a), 'to': str(b),
            'due': str(due), 'total': float(tot), 'fee_234e': float(fee), 'late_days': days, 'filed': R, 'entries': db.clean(es),
            'undeposited': len(und), 'deductees': len({e['party'] for e in es})}


def file_return(company, fy_code, q, form, token, filed_on=None):
    Q = quarter(company, fy_code, q, form)
    if not Q['entries']:
        raise conflict(f"No deductions in this quarter for {FORMS[form][0]}.", 'nothing_to_file')
    if Q['undeposited']:
        raise conflict(f"{Q['undeposited']} deduction(s) are not deposited yet. Pay the challan first.", 'undeposited')
    if Q['filed']:
        raise conflict('This return is already filed.', 'duplicate')
    if not re.fullmatch(r'\d{15}', str(token or '')):
        raise bad('Enter the 15-digit token / provisional receipt number.', 'invalid')
    db.insert('tds_returns', dict(company=company, fy=fy_code, form=form, quarter=q, filed_on=filed_on or dt.date.today(), token=str(token),
                                  fee=Q['fee_234e'], filed_by=g.user['id']))
    audit.write('TDS return filed', f'{FORMS[form][0]} {q} {fy_code}', new=token, category='finance', company=company)
    return quarter(company, fy_code, q, form)


def issue_certificate(company, fy_code, q, party):
    fy = db.one('SELECT * FROM fin_years WHERE company=:c AND code=:f', c=company, f=fy_code)
    if not fy or q not in ('Q1', 'Q2', 'Q3', 'Q4') or not party:
        raise bad('Choose the financial year, quarter and party.', 'invalid')
    a, b = q_range(fy['from_date'], q)
    if db.one('SELECT 1 FROM tds_certificates WHERE company=:c AND fy=:f AND quarter=:q AND party=:p', c=company, f=fy_code, q=q, p=party):
        raise conflict('Certificate already issued.', 'duplicate')
    es = db.q("""SELECT * FROM tds_entries WHERE company=:c AND party=:p AND kind='Payable' AND status<>'Reversed' AND amount>0 AND ded_date BETWEEN :a AND :b""",
              c=company, p=party, a=a, b=b)
    if not es:
        raise conflict('No deductions for this party in the quarter.', 'nothing_to_issue')
    form = form_of(es[0]['section'], (party_tax(company, party) or {}).get('resident', True))
    if not db.one('SELECT 1 FROM tds_returns WHERE company=:c AND fy=:f AND form=:m AND quarter=:q', c=company, f=fy_code, m=form, q=q):
        raise conflict('File the quarterly return first; the certificate is downloaded from TRACES after processing.', 'return_not_filed')
    key = f'{company}|{fy_code}|{q}|{party}'
    no = ('F130' if form == '24Q' else 'F131') + '-' + hashlib.sha256(key.encode()).hexdigest()[:8].upper()
    amt = r2(sum(Decimal(str(e['amount'])) for e in es))
    db.insert('tds_certificates', dict(company=company, fy=fy_code, quarter=q, party=party, cert_no=no, issued_on=dt.date.today(), amount=amt, issued_by=g.user['id']))
    audit.write('TDS certificate issued', no, new=party, category='finance', company=company)
    return {'cert_no': no, 'amount': float(amt), 'form': 'Form 130' if form == '24Q' else 'Form 131'}


def thresholds(company):
    """Suppliers against their section thresholds this year (near / crossed), so TDS is not missed."""
    today = dt.date.today()
    fy = fy_of(company, today)
    out = []
    for p in db.q('''SELECT t.party, t.pan, t.section, s.name FROM party_tax t JOIN suppliers s ON s.id=t.party WHERE t.company=:c AND t.section IS NOT NULL''', c=company):
        sec = section(p['section'])
        if not sec or sec['slab']:
            continue
        credited = Decimal(str(db.val('''SELECT COALESCE(SUM(l.credit), 0) FROM journal_lines l JOIN journals j ON j.id=l.journal_id
                                         WHERE j.company=:c AND j.txn_date>=:f AND l.account='2000' AND l.party=:p''', c=company, f=fy['from_date'], p=p['party']) or 0))
        base = _fy_sum(company, p['party'], sec['code'], today, 'base')
        ded = _fy_sum(company, p['party'], sec['code'], today, 'amount')
        lim = Decimal(str(sec['year_limit'] or sec['single_limit'] or 0))
        use = max(base, credited)
        st = 'No threshold' if not lim else ('Crossed · deducting' if ded else 'Crossed · check TDS') if use > lim else 'Near threshold' if use >= lim * Decimal('0.8') else 'Below'
        out.append({'party': p['party'], 'name': p['name'], 'section': sec['code'], 'pan': p['pan'], 'single': float(sec['single_limit']), 'year': float(sec['year_limit']),
                    'credited': float(use), 'deducted': float(ded), 'pct': int(use / lim * 100) if lim else 0, 'status': st})
    return out


def pan_issues(company):
    out = []
    for p in db.q('SELECT t.party, t.pan, t.section, s.name, s.gstin FROM party_tax t JOIN suppliers s ON s.id=t.party WHERE t.company=:c', c=company):
        c = pan_check(p['pan'])
        if not c['ok']:
            out.append({'kind': 'Supplier', 'id': p['party'], 'name': p['name'], 'pan': p['pan'] or '', 'issue': c['msg'], 'section': p['section']})
        elif p['gstin'] and gstin_check(p['gstin'])['ok'] and p['gstin'][2:12] != p['pan']:
            out.append({'kind': 'Supplier', 'id': p['party'], 'name': p['name'], 'pan': p['pan'], 'issue': 'PAN differs from the PAN inside GSTIN', 'section': p['section']})
    for e in db.q('SELECT id, name, pan FROM employees WHERE company=:c AND active=1', c=company):
        c = pan_check(e['pan'])
        if not c['ok']:
            out.append({'kind': 'Employee', 'id': e['id'], 'name': e['name'], 'pan': e['pan'] or '', 'issue': c['msg'], 'section': '192'})
    return out
