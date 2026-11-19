"""Finance & tax (release 2.3): shared masters and calculations — account groups, the 21 voucher types, GSTIN / PAN validation,
GST calculation (CGST + SGST / UTGST inside the state, IGST across states, cess), financial years and period locks."""
import datetime as dt
from decimal import ROUND_HALF_UP, Decimal

from .. import db
from ..core.security import setting
from ..errors import bad

HOME_STATE = '36'   # Telangana: both companies are registered here
GST_STATES = {'01': 'Jammu & Kashmir', '02': 'Himachal Pradesh', '03': 'Punjab', '04': 'Chandigarh', '05': 'Uttarakhand', '06': 'Haryana', '07': 'Delhi',
              '08': 'Rajasthan', '09': 'Uttar Pradesh', '10': 'Bihar', '11': 'Sikkim', '12': 'Arunachal Pradesh', '13': 'Nagaland', '14': 'Manipur',
              '15': 'Mizoram', '16': 'Tripura', '17': 'Meghalaya', '18': 'Assam', '19': 'West Bengal', '20': 'Jharkhand', '21': 'Odisha',
              '22': 'Chhattisgarh', '23': 'Madhya Pradesh', '24': 'Gujarat', '26': 'Dadra & Nagar Haveli and Daman & Diu', '27': 'Maharashtra',
              '29': 'Karnataka', '30': 'Goa', '31': 'Lakshadweep', '32': 'Kerala', '33': 'Tamil Nadu', '34': 'Puducherry', '35': 'Andaman & Nicobar',
              '36': 'Telangana', '37': 'Andhra Pradesh', '38': 'Ladakh', '97': 'Other territory'}
UT_NO_LEGISLATURE = ('04', '26', '31', '35', '38')   # UTGST instead of SGST

GROUPS = [('1100', '1109', 'Current Assets', 'Cash-in-hand'), ('1110', '1199', 'Current Assets', 'Bank accounts'),
          ('1200', '1299', 'Current Assets', 'Sundry debtors'), ('1300', '1309', 'Current Assets', 'Stock-in-hand'),
          ('1310', '1319', 'Current Assets', 'Duties & taxes (input credit)'), ('1400', '1499', 'Current Assets', 'Inter-company & inter-branch'),
          ('1500', '1589', 'Fixed Assets', 'Gross block'), ('1590', '1599', 'Fixed Assets', 'Accumulated depreciation'),
          ('1600', '1699', 'Current Assets', 'Tax assets (TDS, advance tax)'), ('1700', '1799', 'Current Assets', 'Loans & advances, deposits'),
          ('1800', '1899', 'Current Assets', 'Employee advances'), ('2000', '2099', 'Current Liabilities', 'Sundry creditors'),
          ('2100', '2199', 'Current Liabilities', 'Duties & taxes (GST)'), ('2200', '2299', 'Current Liabilities', 'Advances from customers'),
          ('2300', '2309', 'Current Liabilities', 'Duties & taxes (TDS / TCS)'), ('2310', '2399', 'Current Liabilities', 'Payroll payables & provisions'),
          ('2400', '2499', 'Current Liabilities', 'Inter-company'), ('3000', '3999', 'Capital', "Partners' capital"),
          ('4000', '4099', 'Income', 'Direct income (sales)'), ('4100', '4999', 'Income', 'Indirect income'),
          ('5000', '5199', 'Expenses', 'Direct expenses (cost of sales)'), ('5200', '5299', 'Expenses', 'Depreciation & impairment'),
          ('5300', '5999', 'Expenses', 'Indirect expenses')]

# code: (label, number series, party kind, party side, GST direction, TDS mode)
VTYPES = {
    'RV': ('Receipt Voucher', 'RV', 'cust', 'cr', 'adv', 'recv'), 'PV': ('Payment Voucher', 'PV', 'sup', 'dr', None, 'pay'),
    'CV': ('Contra Voucher', 'CV', None, None, None, None), 'JV': ('Journal Voucher', 'JNL', None, None, None, None),
    'SI': ('Sales Invoice', 'SIN', 'cust', 'dr', 'out', 'recv'), 'PI': ('Purchase Invoice', 'PIN', 'sup', 'cr', 'in', 'pay'),
    'CN': ('Credit Note', 'CRN', 'cust', 'cr', 'outRev', None), 'DN': ('Debit Note', 'DBN', 'sup', 'dr', 'inRev', None),
    'EX': ('Expense Voucher', 'EXP', 'sup', 'cr', 'in', 'pay'), 'SAL': ('Salary / Payroll Voucher', 'SAL', None, None, None, None),
    'SA': ('Stock Adjustment Voucher', 'ADJ', None, None, None, None), 'GA': ('GST Adjustment Voucher', 'GSA', None, None, None, None),
    'TD': ('TDS Deduction Voucher', 'TDD', 'sup', 'dr', None, 'pay'), 'TP': ('TDS Payment Voucher', 'TDP', None, None, None, None),
    'BR': ('Bank Reconciliation Voucher', 'BRV', None, None, None, None), 'AC': ('Asset Capitalisation Voucher', 'ACP', 'sup', 'cr', 'in', None),
    'DP': ('Depreciation Voucher', 'DEP', None, None, None, None), 'PR': ('Provision Voucher', 'PRV', None, None, None, None),
    'RX': ('Reversal Voucher', 'REV', None, None, None, None), 'IB': ('Inter-branch Transfer Voucher', 'IBT', None, None, None, None),
    'IC': ('Inter-company Transfer Voucher', 'ICT', None, None, None, None)}
LIMIT_KEY = {'PV': 'pay', 'TP': 'pay', 'EX': 'exp', 'SA': 'adj', 'SAL': 'pay', 'PI': 'po', 'AC': 'po'}
OUTPUT = {'igst': '2120', 'cgst': '2100', 'sgst': '2110', 'cess': '2130'}
INPUT = {'igst': '1313', 'cgst': '1311', 'sgst': '1312', 'cess': '1314'}
HEADS = ('igst', 'cgst', 'sgst', 'cess')
FINANCE_DEFAULTS = {'gst': {'interest': 18, 'fee': 50, 'nil_fee': 20, 'fee_cap': 5000, 'b2cl_limit': 100000, 'ewb_limit': 50000, 'tolerance': 1},
                    'tds': {'late_deduction': 1, 'late_payment': 1.5, 'fee_234e': 200, 'no_pan_rate': 20},
                    'attach': {'EX': 0, 'PV': 50000, 'JV': 25000, 'PI': 0, 'CN': 0, 'DN': 0, 'AC': 0, 'GA': 0},
                    'always_approve': ['PI', 'GA', 'RX', 'IC', 'AC'], 'dup_days': 7, 'three_way_tolerance': 2, 'related_parties': [], 'series': {}}
CLOSERS = ('CFO', 'Director', 'Finance Head', 'Owner', 'Chairman', 'Super Admin')


def cfg():
    c = setting('finance', {}) or {}
    out = {**FINANCE_DEFAULTS, **c}
    for k in ('gst', 'tds', 'attach'):
        out[k] = {**FINANCE_DEFAULTS[k], **(c.get(k) or {})}
    return out


def r2(x):
    return Decimal(str(x or 0)).quantize(Decimal('0.01'), ROUND_HALF_UP)


def group(code):
    for lo, hi, head, sub in GROUPS:
        if lo <= code <= hi:
            return head, sub
    return 'Other', 'Other'


def is_bank(a):
    return a.startswith('11') and '1110' <= a <= '1129' and a not in ('1110', '1115')


def is_cash(a):
    return a in ('1100', '1105')


_CH = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'


def _check_digit(g14):
    s = 0
    for i, ch in enumerate(g14):
        v = _CH.index(ch) * (2 if i % 2 else 1)
        s += v // 36 + v % 36
    return _CH[(36 - s % 36) % 36]


def gstin_check(g):
    import re
    g = str(g or '').strip().upper()
    if not g:
        return {'ok': False, 'msg': 'Empty'}
    if not re.fullmatch(r'[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]', g):
        return {'ok': False, 'msg': 'Format must be 2-digit state + PAN + entity + Z + check digit'}
    if g[:2] not in GST_STATES:
        return {'ok': False, 'msg': f'Unknown state code {g[:2]}'}
    cd = _check_digit(g[:14])
    return {'ok': True, 'state': g[:2], 'pan': g[2:12]} if cd == g[14] else {'ok': False, 'msg': f'Check digit should be {cd}'}


def gstin_fix(g14):
    g14 = str(g14).upper()[:14]
    return g14 + _check_digit(g14)


PAN_TYPES = {'P': 'Individual', 'C': 'Company', 'H': 'HUF', 'F': 'Firm / LLP', 'A': 'Association of persons', 'T': 'Trust', 'B': 'Body of individuals',
             'L': 'Local authority', 'J': 'Artificial juridical person', 'G': 'Government'}


def pan_check(p):
    import re
    p = str(p or '').strip().upper()
    if not p:
        return {'ok': False, 'msg': 'PAN not available'}
    if not re.fullmatch(r'[A-Z]{3}[PCHFATBLJG][A-Z][0-9]{4}[A-Z]', p):
        return {'ok': False, 'msg': 'Invalid PAN format'}
    return {'ok': True, 'kind': PAN_TYPES[p[3]], 'individual': p[3] in 'PH'}


def gst_calc(rows, pos=None, inter=None):
    """rows: [{hsn, taxable, rate, cess?}] → per-row split and totals."""
    if not rows:
        return None
    pos = pos or HOME_STATE
    inter = (pos != HOME_STATE) if inter is None else bool(inter)
    out = []
    for r in rows:
        tx = r2(r.get('taxable'))
        rate = Decimal(str(r.get('rate') or 0))
        tax = r2(tx * rate / 100)
        half = r2(tax / 2)
        out.append({**r, 'taxable': float(tx), 'rate': float(rate), 'igst': float(tax if inter else 0), 'cgst': float(0 if inter else half),
                    'sgst': float(0 if inter else tax - half), 'cess_amt': float(r2(tx * Decimal(str(r.get('cess') or 0)) / 100))})
    t = {k: float(r2(sum(Decimal(str(x[k2])) for x in out))) for k, k2 in (('taxable', 'taxable'), ('igst', 'igst'), ('cgst', 'cgst'), ('sgst', 'sgst'), ('cess', 'cess_amt'))}
    t['tax'] = float(r2(t['igst'] + t['cgst'] + t['sgst'] + t['cess']))
    return {'rows': out, 'inter': inter, 'ut': pos in UT_NO_LEGISLATURE, 'pos': pos, 't': t}


def fy_of(company, d):
    r = db.one('SELECT code, from_date, to_date, status FROM fin_years WHERE company=:c AND :d BETWEEN from_date AND to_date', c=company, d=d)
    if r:
        return r
    y = d.year if d.month >= 4 else d.year - 1
    return {'code': f'{y}-{(y + 1) % 100:02d}', 'from_date': dt.date(y, 4, 1), 'to_date': dt.date(y + 1, 3, 31), 'status': 'Open'}


def month_end(d):
    return (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)


def gst_filed_upto(company):
    p = db.val("SELECT MAX(period) FROM gst_filings WHERE company=:c AND ret='3B'", c=company)
    return month_end(dt.date(int(p[:4]), int(p[5:7]), 1)) if p else None


def lock_reason(company, d):
    """Why a date is closed for ordinary posting ('' when open): books lock, GSTR-3B filed, or financial year closed."""
    lock = db.val('SELECT books_locked_to FROM companies WHERE code=:c', c=company)
    if lock and d <= lock:
        return f'Books of {company} are closed up to {lock:%d-%m-%Y}'
    f = gst_filed_upto(company)
    if f and d <= f:
        return f'GSTR-3B is filed up to {f:%d-%m-%Y}'
    y = db.one('SELECT code, status FROM fin_years WHERE company=:c AND :d BETWEEN from_date AND to_date', c=company, d=d)
    if y and y['status'] == 'Closed':
        return f"Financial year {y['code']} is closed"
    return ''


def account_names(company):
    return {r['code']: r['name'] for r in db.q('SELECT code, name FROM accounts WHERE company=:c', c=company)}


def lock_company(company):
    """Serialise money-moving steps of one company (GST payment, depreciation run): the second of two simultaneous requests
    waits here and then sees the first one's rows through a locking read."""
    if not db.one('SELECT code FROM companies WHERE code=:c FOR UPDATE', c=company):
        raise bad('Unknown company.', 'invalid')


def need_ym(ym, label='month'):
    """'YYYY-MM' or a clear 400."""
    import re
    if not isinstance(ym, str) or not re.fullmatch(r'20\d\d-(0[1-9]|1[0-2])', ym):
        raise bad(f'Choose the {label} (YYYY-MM).', 'invalid_month')
    return ym


def txt(v):
    """Free text from a request field: None → '', numbers → their text, lists / objects refused."""
    if v is None:
        return ''
    if isinstance(v, (dict, list)):
        raise bad('A text field was sent as a list or object.', 'invalid_input')
    return str(v).strip()


def ho(company):
    return f'{company}-HO'
