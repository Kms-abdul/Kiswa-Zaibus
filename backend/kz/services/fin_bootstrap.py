"""Reference data for Finance & tax and the stock-audit approval rules (release 2.3).

`bootstrap(conn)` is idempotent and uses only SQL on the given connection, so migration 0005 can run it on existing
databases and the seed runs it on fresh installs (after the companies exist). It never overwrites a value an
administrator has changed: rows are INSERT IGNORE and JSON settings are merged key by key."""
import datetime as dt
import json

from sqlalchemy import text

FIN_ACCOUNTS = [
    ('1121', 'Second Bank Account', 'A'), ('1311', 'Input CGST', 'A'), ('1312', 'Input SGST / UTGST', 'A'), ('1313', 'Input IGST', 'A'), ('1314', 'Input Cess', 'A'),
    ('1410', 'Inter-branch Control', 'A'), ('1500', 'Furniture & Fixtures', 'A'), ('1510', 'Computers & IT Equipment', 'A'), ('1520', 'Vehicles', 'A'),
    ('1530', 'Plant, Machinery & Office Equipment', 'A'), ('1590', 'Accumulated Depreciation', 'A'), ('1600', 'TDS Receivable', 'A'), ('1610', 'Advance Income Tax', 'A'),
    ('1700', 'Advances to Suppliers', 'A'), ('1710', 'Prepaid Expenses', 'A'), ('1720', 'Security Deposits', 'A'), ('1800', 'Employee Advances', 'A'),
    ('2130', 'Output Cess', 'L'), ('2140', 'GST Payable on Reverse Charge', 'L'), ('2210', 'Advances from Customers', 'L'), ('2300', 'TDS Payable', 'L'),
    ('2305', 'TCS Payable', 'L'), ('2310', 'Salaries Payable', 'L'), ('2320', 'PF & ESI Payable', 'L'), ('2325', 'Professional Tax Payable', 'L'),
    ('2330', 'Provisions & Accrued Expenses', 'L'), ('4100', 'Other Income', 'I'), ('4110', 'Interest Income', 'I'), ('4120', 'Gain on Sale of Assets', 'I'),
    ('4130', 'Stock Gain on Physical Verification', 'I'), ('5200', 'Depreciation', 'X'), ('5210', 'Impairment Loss', 'X'), ('5310', 'Professional Fees', 'X'),
    ('5320', 'Contract Labour', 'X'), ('5330', 'Commission', 'X'), ('5340', 'Interest Expense', 'X'), ('5350', 'Interest & Late Fee on Taxes', 'X'),
    ('5370', 'Repairs & Maintenance', 'X'), ('5380', 'Printing & Stationery', 'X'), ('5390', 'Travel & Conveyance', 'X'), ('5410', 'Staff Welfare', 'X'),
    ('5420', "Employer's PF & ESI", 'X'), ('5610', 'Stock Damage & Expiry Write-off', 'X'), ('5960', 'Loss on Sale of Assets', 'X')]

NEW_ROLES = [('Accounts Executive', 'Day-to-day vouchers, receipts and payments within limit', 0), ('Branch Accountant', 'Branch vouchers, cash book and bank for assigned locations', 0),
             ('Finance Manager', 'Reviews and approves finance vouchers within limit; GST and TDS returns', 2)]
OPS = 'view add edit submit cancel print pdf export import upload fin cost'
ALL = 'view add edit submit approve reject return cancel reverse delete restore print pdf export import upload audit fin cost master cfgrep'
FIN_PERMS = {
    'Director': ALL, 'Owner': ALL, 'Chairman': ALL, 'CFO': ALL, 'Finance Head': ALL,
    'Finance Manager': OPS + ' approve reject return reverse audit master', 'Accounts Manager': OPS + ' approve reject return reverse audit master',
    'Finance': OPS + ' approve reject return reverse audit', 'Accountant': OPS, 'Accounts Executive': OPS, 'Finance User': OPS, 'Branch Accountant': OPS,
    'Auditor': 'view print pdf export audit fin cost', 'Super Admin': 'view master audit export'}
NEW_ROLE_OTHER = {'dash': 'view fin', 'rep': 'view print pdf export fin cost', 'acc': OPS, 'appr': 'view'}
NEW_ROLE_LIMITS = {'Finance Manager': {'jv': 200000, 'pay': 200000, 'exp': 200000, 'po': 200000, 'adj': 50000},
                   'Accounts Executive': {'jv': 25000, 'pay': 25000, 'exp': 25000}, 'Branch Accountant': {'jv': 10000, 'pay': 10000, 'exp': 10000}}
# The internal Auditor may count stock audits it is named on (still read-only elsewhere; see core/rbac.RO_EXCEPTIONS)
SAUDIT_EXTRA = {'Auditor': 'edit submit upload import', 'Finance Manager': 'view print pdf export fin cost', 'Accounts Executive': 'view print pdf export fin cost'}

FINANCE_SETTINGS = {
    'gst': {'interest': 18, 'fee': 50, 'nil_fee': 20, 'fee_cap': 5000, 'b2cl_limit': 100000, 'ewb_limit': 50000, 'tolerance': 1},
    'tds': {'late_deduction': 1, 'late_payment': 1.5, 'fee_234e': 200, 'no_pan_rate': 20},
    'attach': {'EX': 0, 'PV': 50000, 'JV': 25000, 'PI': 0, 'CN': 0, 'DN': 0, 'AC': 0, 'GA': 0},
    'always_approve': ['PI', 'GA', 'RX', 'IC', 'AC'], 'dup_days': 7, 'three_way_tolerance': 2,
    'related_parties': [], 'series': {}}
STOCK_AUDIT_SETTINGS = {'cfo_upto': 25000, 'hi_item': 5000, 'write_off_damaged': True, 'cost_centre': '',
                        'gl': {'short': '5600', 'dmg': '5610', 'excess': '4130', 'inv': '1300'}}
COST_CENTRES = [('CC-STR', 'Retail stores', 'Profit centre'), ('CC-SCH', 'School counters', 'Profit centre'), ('CC-WH', 'Warehousing & logistics', 'Cost centre'),
                ('CC-ONL', 'Online store', 'Profit centre'), ('D-ACC', 'Finance & Accounts', 'Department'), ('D-ADM', 'Administration', 'Department'),
                ('D-SAL', 'Sales & Billing', 'Department'), ('D-PUR', 'Purchase', 'Department'), ('D-IT', 'IT & Maintenance', 'Department')]
# code, label, rate ind, rate other, single, year, monthly, slab, NR, TCS, no-PAN rate, stage, form, Income-tax Act 2025 ref, active
TDS_SECTIONS = [
    ('192', 'Salary', 0, 0, 0, 0, 0, 1, 0, 0, None, 'payment', '24Q', 's.392', 1),
    ('194C', 'Payment to contractors', 1, 2, 30000, 100000, 0, 0, 0, 0, None, 'invoice', '26Q', 's.393(1) Table', 1),
    ('194J(a)', 'Fees for technical services', 2, 2, 0, 50000, 0, 0, 0, 0, None, 'invoice', '26Q', 's.393(1) Table', 1),
    ('194J(b)', 'Fees for professional services', 10, 10, 0, 50000, 0, 0, 0, 0, None, 'invoice', '26Q', 's.393(1) Table', 1),
    ('194H', 'Commission or brokerage', 2, 2, 0, 20000, 0, 0, 0, 0, None, 'invoice', '26Q', 's.393(1) Table', 1),
    ('194I(a)', 'Rent: plant & machinery', 2, 2, 50000, 0, 1, 0, 0, 0, None, 'invoice', '26Q', 's.393(1) Table', 1),
    ('194I(b)', 'Rent: land, building, furniture', 10, 10, 50000, 0, 1, 0, 0, 0, None, 'invoice', '26Q', 's.393(1) Table', 1),
    ('194A', 'Interest other than securities', 10, 10, 0, 10000, 0, 0, 0, 0, None, 'invoice', '26Q', 's.393(1) Table', 1),
    ('194Q', 'Purchase of goods', 0.1, 0.1, 0, 5000000, 0, 0, 0, 0, 5, 'invoice', '26Q', 's.393(1) Table', 1),
    ('195', 'Payment to non-residents', 20, 20, 0, 0, 0, 0, 1, 0, None, 'payment', '27Q', 's.393(2)', 1),
    ('206C', 'TCS (where applicable)', 0.1, 0.1, 0, 0, 0, 0, 0, 1, None, 'invoice', '27EQ', 's.394', 0)]
SAC = [('997212', 'Rental of commercial property', 18), ('998231', 'Professional / consultancy services', 18), ('996511', 'Goods transport (GTA)', 5),
       ('998719', 'Repair & maintenance services', 18), ('998599', 'Security & support services', 18), ('997133', 'Insurance (non-life)', 18),
       ('998912', 'Printing services', 18), ('998315', 'IT infrastructure & hosting', 18), ('996331', 'Food & beverage services', 5)]
ASSET_CATEGORIES = [('FUR', 'Furniture & fittings', '1500', 10, 10), ('IT', 'Computers & data-processing units', '1510', 3, 40), ('VEH', 'Motor vehicles', '1520', 8, 15),
                    ('OE', 'Office equipment', '1530', 5, 15), ('PM', 'Plant & machinery', '1530', 15, 15)]
RULES = [('*', None, None, 500000, 'CFO'), ('*', None, None, 2000000, 'Director'), ('JV', '3000', None, 0, 'Director')]


def _fy(d):
    y = d.year if d.month >= 4 else d.year - 1
    return f'{y}-{(y + 1) % 100:02d}', dt.date(y, 4, 1), dt.date(y + 1, 3, 31)


def _merge(conn, key, defaults):
    row = conn.execute(text('SELECT svalue FROM settings WHERE skey=:k'), {'k': key}).first()
    cur = (json.loads(row[0]) if isinstance(row[0], str) else row[0]) if row else {}
    merged = {**defaults, **(cur or {})}
    for k, v in defaults.items():
        if isinstance(v, dict) and isinstance((cur or {}).get(k), dict):
            merged[k] = {**v, **cur[k]}
    s = json.dumps(merged)
    if row:
        conn.execute(text('UPDATE settings SET svalue=:v WHERE skey=:k'), {'v': s, 'k': key})
    else:
        conn.execute(text('INSERT INTO settings (skey, svalue) VALUES (:k, :v)'), {'k': key, 'v': s})


def bootstrap(conn, today=None):
    x = lambda sql, **p: conn.execute(text(sql), p)  # noqa: E731
    today = today or dt.date.today()
    companies = [r[0] for r in x('SELECT code FROM companies')]
    for code, name, grp in FIN_ACCOUNTS:
        for co in companies:
            x('INSERT IGNORE INTO accounts (company, code, name, grp, is_control, active) VALUES (:co, :c, :n, :g, 0, 1)', co=co, c=code, n=name, g=grp)
    for name, desc, rank in NEW_ROLES:
        x('INSERT IGNORE INTO roles (name, description, approval_rank, read_only, is_custom, active) VALUES (:n, :d, :r, 0, 0, 1)', n=name, d=desc, r=rank)
    roles = {r[0] for r in x('SELECT name FROM roles')}
    grant = lambda role, key, acts: [x('INSERT IGNORE INTO permissions (role, screen_key, action) VALUES (:r, :k, :a)', r=role, k=key, a=a)  # noqa: E731
                                     for a in acts.split()] if role in roles else None
    for role, acts in FIN_PERMS.items():
        grant(role, 'fin', acts)
    for role, _, _ in NEW_ROLES:
        for key, acts in NEW_ROLE_OTHER.items():
            grant(role, key, acts + (' approve reject return' if role == 'Finance Manager' and key in ('appr', 'acc') else ''))
    for role, acts in SAUDIT_EXTRA.items():
        grant(role, 'saudit', acts)
    for role, lims in NEW_ROLE_LIMITS.items():
        for k, v in lims.items():
            x('INSERT IGNORE INTO approval_limits (role, limit_key, amount) VALUES (:r, :k, :a)', r=role, k=k, a=v)
    _merge(conn, 'finance', FINANCE_SETTINGS)
    _merge(conn, 'stock_audit', STOCK_AUDIT_SETTINGS)
    for cid, name, kind in COST_CENTRES:
        x('INSERT IGNORE INTO cost_centres (id, name, kind, active) VALUES (:i, :n, :k, 1)', i=cid, n=name, k=kind)
    for s in TDS_SECTIONS:
        x('''INSERT IGNORE INTO tds_sections (code, label, rate_ind, rate_other, single_limit, year_limit, monthly, slab, non_resident, tcs, no_pan_rate, stage, form,
             new_ref, effective_from, active) VALUES (:a,:b,:c,:d,:e,:f,:g,:h,:i,:j,:k,:l,:m,:n,'2025-04-01',:o)''', **dict(zip('abcdefghijklmno', s)))
    for code, desc, rate in SAC:
        x("INSERT IGNORE INTO hsn_codes (code, kind, description, rate, uqc) VALUES (:c, 'SAC', :d, :r, 'OTH')", c=code, d=desc, r=rate)
    for r in x("SELECT DISTINCT hsn, name, gst_rate FROM item_categories WHERE hsn IS NOT NULL AND hsn<>''"):
        x("INSERT IGNORE INTO hsn_codes (code, kind, description, rate, uqc) VALUES (:c, 'HSN', :d, :r, :u)", c=r[0], d=r[1], r=r[2], u='PRS' if r[1] in ('Shoes', 'Socks') else 'NOS')
    for a in ASSET_CATEGORIES:
        x('INSERT IGNORE INTO asset_categories (id, name, account, life_years, it_rate) VALUES (:i, :n, :a, :l, :r)', i=a[0], n=a[1], a=a[2], l=a[3], r=a[4])
    code, f, t = _fy(today)
    pcode, pf, pt = _fy(f - dt.timedelta(days=1))
    for co in companies:
        x("INSERT IGNORE INTO fin_years (company, code, from_date, to_date, status) VALUES (:co, :c, :f, :t, 'Open')", co=co, c=code, f=f, t=t)
        x("INSERT IGNORE INTO fin_years (company, code, from_date, to_date, status) VALUES (:co, :c, :f, :t, 'Closed')", co=co, c=pcode, f=pf, t=pt)
    for r in x("SELECT code, gstin, state_code FROM companies WHERE gstin IS NOT NULL AND gstin<>''"):
        x("INSERT IGNORE INTO gst_registrations (company, state, gstin, from_date, kind, einvoice) VALUES (:co, :s, :g, '2017-07-01', 'Regular', 0)", co=r[0], s=r[2] or '36', g=r[1])
    for co in companies:
        if not x('SELECT COUNT(*) FROM bank_accounts WHERE company=:c', c=co).scalar():
            x("INSERT INTO bank_accounts (company, account, name, branch_id) VALUES (:c, '1120', 'Main bank account', :b)", c=co, b=f'{co}-HO')
    if not x('SELECT COUNT(*) FROM fin_approval_rules').scalar():
        for vt, acct, br, above, role in RULES:
            x('INSERT INTO fin_approval_rules (vtype, account, branch_id, above, role) VALUES (:v, :a, :b, :x, :r)', v=vt, a=acct, b=br, x=above, r=role)
    return {'companies': companies, 'accounts': len(FIN_ACCOUNTS)}


def unbootstrap(conn):
    x = lambda sql, **p: conn.execute(text(sql), p)  # noqa: E731
    x("DELETE FROM permissions WHERE screen_key='fin'")
    for role, acts in SAUDIT_EXTRA.items():
        for a in acts.split():
            x("DELETE FROM permissions WHERE role=:r AND screen_key='saudit' AND action=:a", r=role, a=a)
    for name, _, _ in NEW_ROLES:
        x('DELETE FROM permissions WHERE role=:r', r=name)
        x('DELETE FROM approval_limits WHERE role=:r', r=name)
        x('DELETE FROM roles WHERE name=:r AND NOT EXISTS (SELECT 1 FROM users WHERE main_role=:r)', r=name)
    x("DELETE FROM settings WHERE skey='finance'")
    codes = tuple(c for c, _, _ in FIN_ACCOUNTS)
    x('DELETE FROM accounts WHERE code IN :c AND NOT EXISTS (SELECT 1 FROM journal_lines l WHERE l.account=accounts.code)'.replace(':c', '(' + ','.join(f"'{c}'" for c in codes) + ')'))
