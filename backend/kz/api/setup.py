"""Setup (master data that used to need SQL): companies, regions and zones, locations and their company branches, schools,
item categories, suppliers, customers, packages, chart of accounts, roles (permissions and approval limits), discount
schemes, custom fields and scheduled report e-mails.

Permissions
  companies, regions, zones, locations, schemes, custom fields, schedules ... edit on 'set'
  categories, suppliers, packages, schools ............................... master on 'inv'
  customers ............................................................. master on 'inv' or 'acc'
  chart of accounts ..................................................... master on 'acc'
  roles ................................................................. users on 'ctl' (+ maker-checker 'access')
Every change is written to the audit trail with its old and new values.
"""
import re
from decimal import Decimal

from flask import Blueprint, g, jsonify, request

from .. import db
from ..core import audit
from ..core.approvals import handler, mc_on, request as mc_request
from ..core.rbac import body, can, high_risk, login_required, require
from ..core.routing import route_role
from ..errors import bad, conflict, denied, not_found
from ..models import CustomField, DiscountScheme, Package, PackageLine, ReportSchedule
from ..services import report_schedules as rs
from ..services.common import money, parse_date
from ..services.ledger import CONTROL_ACCOUNTS

bp = Blueprint('setup', __name__, url_prefix='/api/setup')

# accounts the system posts to by itself: they can be renamed but never switched off (ledger.CONTROL_ACCOUNTS + cash, bank, GST, sales, COGS)
CONTROL = set(CONTROL_ACCOUNTS) | {'1100', '1110', '1115', '1120', '1310', '2100', '2110', '2120', '3000', '4000', '4010', '5000', '5100', '5600', '5700', '5850', '5950'}
SCREENS = [('dash', 'Dashboard'), ('pos', 'Billing (POS)'), ('msea', 'MSEA sponsorship'), ('onl', 'Online orders'), ('inv', 'Stock and items'),
           ('pur', 'Purchasing'), ('cash', 'Cash custody'), ('acc', 'Accounts'), ('rep', 'Reports'), ('appr', 'Approvals'), ('audit', 'Audit trail'),
           ('set', 'Settings and setup'), ('ctl', 'Control (users, security)'), ('ctl.int', 'ERP integration'), ('ctl.pay', 'Payments (Razorpay)'), ('saudit', 'Stock audits'), ('fin', 'Finance & tax')]
ACTIONS = ['view', 'add', 'edit', 'submit', 'approve', 'reject', 'return', 'cancel', 'reverse', 'delete', 'restore', 'print', 'pdf', 'export', 'import',
           'upload', 'audit', 'fin', 'cost', 'master', 'cfgrep', 'users', 'verify', 'review', 'post']
LIMITS = [('disc', 'Discount %'), ('po', 'Purchase order ₹'), ('pay', 'Supplier payment ₹'), ('refund', 'Refund ₹'), ('adj', 'Stock adjustment ₹'),
          ('exp', 'Expense ₹'), ('cash', 'Cash difference ₹'), ('credit', 'Credit sale ₹'), ('transfer', 'Stock transfer ₹'), ('jv', 'Manual journal ₹'),
          ('dn', 'Debit note ₹'), ('recon', 'Reconciliation write-off ₹')]


def need_any(*pairs):
    if not any(can(g.user, a, k) for a, k in pairs):
        raise denied()


def _text(d, k, n, required=False, label=None):
    v = d.get(k)
    if isinstance(v, (dict, list)):
        raise bad(f'{label or k.replace("_", " ").capitalize()} must be text.', 'invalid_input')
    v = str(v if v is not None else '').strip()
    if required and not v:
        raise bad(f'{label or k.replace("_", " ").capitalize()} is required.', 'missing_' + k)
    return v[:n] or None


def _changes(old, new):
    return {k: [old.get(k), v] for k, v in new.items() if old.get(k) != v}


def _update(table, keycols, keyvals, fields, what, ref, category='master'):
    where = ' AND '.join(f'{c}=:k_{c}' for c in keycols)
    kv = {f'k_{c}': v for c, v in zip(keycols, keyvals)}
    cur = db.one(f'SELECT * FROM {table} WHERE {where} FOR UPDATE', **kv)
    if not cur:
        raise not_found(f'{what} not found.')
    cur = db.clean(cur)
    ch = _changes(cur, fields)
    if ch:
        sets = ', '.join(f'`{k}`=:{k}' for k in ch)
        db.run(f'UPDATE {table} SET {sets} WHERE {where}', **{k: fields[k] for k in ch}, **kv)
        audit.write(f'{what} changed', ref, old={k: v[0] for k, v in ch.items()}, new={k: v[1] for k, v in ch.items()}, category=category)
    return dict(cur, **fields)


# ---------------------------------------------------------------- catalogue for the screens
@bp.get('/catalog')
@login_required
def catalog():
    return jsonify({'screens': SCREENS, 'actions': ACTIONS, 'limits': LIMITS, 'control_accounts': sorted(CONTROL),
                    'report_keys': list(rs.REPORTS.items()), 'periods': rs.PERIODS})


# ---------------------------------------------------------------- companies
@bp.get('/companies')
@login_required
def companies():
    return jsonify(db.clean(db.q('SELECT * FROM companies ORDER BY code')))


@bp.put('/companies/<code>')
@require('edit', 'set')
def company_update(code):
    d = body()
    f = {k: _text(d, k, n) for k, n in (('name', 120), ('short_name', 40), ('address', 300), ('phone', 30), ('email', 120)) if k in d}
    if 'gstin' in d:
        gst = str(d.get('gstin') or '').strip().upper()
        if gst and not re.fullmatch(r'\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]', gst):
            raise bad('GSTIN is not valid (15 characters, e.g. 36AAKFK4821L1Z7).', 'bad_gstin')
        f['gstin'] = gst or None
        if gst:
            f['state_code'] = gst[:2]
    if f.get('name') is None and 'name' in f:
        raise bad('Name is required.', 'missing_name')
    return jsonify(_update('companies', ['code'], [code], f, 'Company', code, 'settings'))


# ---------------------------------------------------------------- regions and zones
@bp.get('/regions')
@login_required
def regions():
    return jsonify({'regions': db.q('SELECT * FROM regions ORDER BY name'), 'zones': db.q('SELECT * FROM zones ORDER BY name')})


@bp.post('/regions')
@require('edit', 'set')
def region_add():
    d = body()
    rid = (_text(d, 'id', 10, True, 'Code') or '').upper()
    if db.val('SELECT COUNT(*) FROM regions WHERE id=:i', i=rid):
        raise conflict('A region with this code exists.', 'duplicate_code')
    db.insert('regions', dict(id=rid, name=_text(d, 'name', 80, True)))
    audit.write('Region added', rid, new=d.get('name'), category='settings')
    return jsonify({'id': rid}), 201


@bp.put('/regions/<rid>')
@require('edit', 'set')
def region_update(rid):
    return jsonify(_update('regions', ['id'], [rid], {'name': _text(body(), 'name', 80, True)}, 'Region', rid, 'settings'))


@bp.post('/zones')
@require('edit', 'set')
def zone_add():
    d = body()
    zid = (_text(d, 'id', 10, True, 'Code') or '').upper()
    if db.val('SELECT COUNT(*) FROM zones WHERE id=:i', i=zid):
        raise conflict('A zone with this code exists.', 'duplicate_code')
    if not db.val('SELECT COUNT(*) FROM regions WHERE id=:r', r=d.get('region_id')):
        raise bad('Choose the region.', 'unknown_region')
    db.insert('zones', dict(id=zid, name=_text(d, 'name', 80, True), region_id=d['region_id']))
    audit.write('Zone added', zid, new=d, category='settings')
    return jsonify({'id': zid}), 201


@bp.put('/zones/<zid>')
@require('edit', 'set')
def zone_update(zid):
    d = body()
    f = {'name': _text(d, 'name', 80, True)}
    if d.get('region_id'):
        if not db.val('SELECT COUNT(*) FROM regions WHERE id=:r', r=d['region_id']):
            raise bad('Choose the region.', 'unknown_region')
        f['region_id'] = d['region_id']
    return jsonify(_update('zones', ['id'], [zid], f, 'Zone', zid, 'settings'))


# ---------------------------------------------------------------- locations and branches
@bp.get('/locations')
@login_required
def locations():
    locs = db.clean(db.q('SELECT * FROM locations ORDER BY active DESC, name'))
    brs = db.clean(db.q('SELECT * FROM branches ORDER BY company'))
    for loc in locs:
        loc['branches'] = [b for b in brs if b['location'] == loc['code']]
    return jsonify(locs)


def _loc_fields(d):
    t = d.get('type')
    if t is not None and t not in ('Store', 'Counter', 'Warehouse', 'Godown', 'Office'):
        raise bad('Type must be Store, Counter, Warehouse, Godown or Office.', 'invalid')
    if d.get('zone_id') and not db.val('SELECT COUNT(*) FROM zones WHERE id=:z', z=d['zone_id']):
        raise bad('Unknown zone.', 'unknown_zone')
    f = {k: _text(d, k, n) for k, n in (('name', 120), ('public_name', 120), ('address', 300), ('contact', 120)) if k in d}
    if t:
        f['type'] = t
    if 'zone_id' in d:
        f['zone_id'] = d.get('zone_id') or None
    return f


def _add_branches(code, companies):
    added = []
    for co, series in (companies or {}).items():
        if not db.val('SELECT COUNT(*) FROM companies WHERE code=:c', c=co):
            raise bad(f'Unknown company {co}.', 'unknown_company')
        series = (series or code).strip().upper()[:10]
        if not re.fullmatch(r'[A-Z0-9]{2,10}', series):
            raise bad('Invoice series code must be 2-10 letters or digits.', 'invalid')
        bid = f'{co}-{code}'
        if db.val('SELECT COUNT(*) FROM branches WHERE id=:b', b=bid):
            db.run('UPDATE branches SET active=1 WHERE id=:b', b=bid)
            continue
        if db.val('SELECT COUNT(*) FROM branches WHERE company=:c AND code=:s', c=co, s=series):
            raise conflict(f'{co} already uses invoice series code {series}.', 'duplicate_code')
        db.insert('branches', dict(id=bid, company=co, location=code, code=series, active=1))
        added.append(bid)
    return added


@bp.post('/locations')
@require('edit', 'set')
def location_add():
    d = body()
    code = (_text(d, 'code', 10, True, 'Code') or '').upper()
    if not re.fullmatch(r'[A-Z0-9]{2,10}', code):
        raise bad('Code must be 2-10 capital letters or digits (it appears in bill numbers).', 'invalid')
    if db.val('SELECT COUNT(*) FROM locations WHERE code=:c', c=code):
        raise conflict('A location with this code exists.', 'duplicate_code')
    f = _loc_fields(d)
    if not f.get('name') or not f.get('type'):
        raise bad('Name and type are required.', 'missing_name')
    db.insert('locations', dict(code=code, active=1, is_central_wh=1 if d.get('is_central_wh') else 0, **f))
    added = _add_branches(code, d.get('companies') or {})
    audit.write('Location opened', code, new={**f, 'branches': added}, category='settings')
    return jsonify({'code': code, 'branches': added}), 201


@bp.put('/locations/<code>')
@require('edit', 'set')
def location_update(code):
    d = body()
    out = _update('locations', ['code'], [code], _loc_fields(d), 'Location', code, 'settings')
    added = _add_branches(code, d.get('companies') or {})
    if added:
        audit.write('Company added to location', code, new=added, category='settings')
    return jsonify(dict(out, branches_added=added))


@bp.post('/locations/<code>/close')
@require('edit', 'set')
@high_risk('delete')
def location_close(code):
    d = body()
    reason = _text(d, 'reason', 300, True, 'Reason')
    loc = db.one('SELECT * FROM locations WHERE code=:c FOR UPDATE', c=code)
    if not loc or not loc['active']:
        raise not_found('Open location not found.')
    left = db.q('''SELECT s.branch_id, COUNT(*) items, SUM(s.qty) units FROM stock_balance s JOIN branches b ON b.id=s.branch_id
                   WHERE b.location=:c AND (s.qty<>0 OR s.reserved<>0) GROUP BY s.branch_id''', c=code)
    if left:
        raise conflict('Stock is still held here: ' + ', '.join(f"{r['branch_id']} {int(r['units'])} units" for r in left) +
                       '. Transfer it out first (Transfers).', 'stock_remaining')
    if db.val("SELECT COUNT(*) FROM cash_shifts WHERE location=:c AND status IN ('Open','Pending approval')", c=code):
        raise conflict('A cash shift is still open here. Close it first.', 'shift_open')
    db.run('UPDATE locations SET active=0, closed_on=CURDATE(), closed_reason=:r, stock_moved_to=:m WHERE code=:c', r=reason, m=d.get('stock_moved_to'), c=code)
    db.run('UPDATE branches SET active=0 WHERE location=:c', c=code)
    audit.write('Location closed', code, old='Open', new='Closed', reason=reason, category='settings')
    return jsonify({'code': code, 'status': 'Closed'})


@bp.post('/locations/<code>/reopen')
@require('edit', 'set')
def location_reopen(code):
    reason = _text(body(), 'reason', 300, True, 'Reason')
    if not db.run('UPDATE locations SET active=1, closed_on=NULL, closed_reason=NULL WHERE code=:c AND active=0', c=code).rowcount:
        raise not_found('Closed location not found.')
    db.run('UPDATE branches SET active=1 WHERE location=:c', c=code)
    audit.write('Location reopened', code, old='Closed', new='Open', reason=reason, category='settings')
    return jsonify({'code': code, 'status': 'Open'})


# ---------------------------------------------------------------- schools
@bp.post('/schools')
@require('master', 'inv')
def school_add():
    d = body()
    sid = (_text(d, 'id', 10, True, 'School code') or '').upper()
    if db.val('SELECT COUNT(*) FROM schools WHERE id=:i', i=sid):
        raise conflict('A school with this code exists.', 'duplicate_code')
    db.insert('schools', dict(id=sid, name=_text(d, 'name', 160, True), board=_text(d, 'board', 40), area=_text(d, 'area', 80), active=1))
    audit.write('School added', sid, new=d.get('name'))
    return jsonify({'id': sid}), 201


@bp.put('/schools/<sid>')
@require('master', 'inv')
def school_update(sid):
    d = body()
    f = {k: _text(d, k, n) for k, n in (('name', 160), ('board', 40), ('area', 80)) if k in d}
    if 'active' in d:
        f['active'] = 1 if d['active'] else 0
    return jsonify(_update('schools', ['id'], [sid], f, 'School', sid))


# ---------------------------------------------------------------- categories, suppliers, customers (create lives in masters.py)
@bp.put('/categories/<co>/<path:name>')
@require('master', 'inv')
def category_update(co, name):
    d = body()
    f = {}
    if 'hsn' in d:
        f['hsn'] = _text(d, 'hsn', 10)
    if 'gst_rate' in d:
        if float(d['gst_rate'] or 0) not in (0, 5, 12, 18, 28, 40):
            raise bad('GST rate must be 0, 5, 12, 18, 28 or 40.', 'bad_gst_rate')
        f['gst_rate'] = float(d['gst_rate'] or 0)
    if 'max_disc' in d:
        f['max_disc'] = None if d['max_disc'] in (None, '') else float(d['max_disc'])
    for k in ('isbn_required', 'active'):
        if k in d:
            f[k] = 1 if d[k] else 0
    return jsonify(_update('item_categories', ['company', 'name'], [co, name], f, 'Item category', f'{co} {name}'))


@bp.put('/suppliers/<sid>')
@require('master', 'inv')
def supplier_update(sid):
    d = body()
    f = {k: _text(d, k, n) for k, n in (('name', 160), ('city', 80), ('contact', 160), ('bank_name', 120), ('bank_account', 40)) if k in d}
    if 'gstin' in d:
        gst = str(d.get('gstin') or '').strip().upper()
        if gst and not re.fullmatch(r'\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]', gst):
            raise bad('GSTIN is not valid.', 'bad_gstin')
        f['gstin'] = gst or None
    if 'bank_ifsc' in d:
        ifsc = str(d.get('bank_ifsc') or '').strip().upper()
        if ifsc and not re.fullmatch(r'[A-Z]{4}0[A-Z0-9]{6}', ifsc):
            raise bad('IFSC is not valid (e.g. HDFC0001234).', 'bad_ifsc')
        f['bank_ifsc'] = ifsc or None
    if 'terms_days' in d:
        f['terms_days'] = int(d['terms_days'] or 0)
    if 'active' in d:
        f['active'] = 1 if d['active'] else 0
    # bank details are a common fraud target: always record who changed them
    return jsonify(_update('suppliers', ['id'], [sid], f, 'Supplier', sid, 'purchase'))


@bp.put('/customers/<cid>')
@login_required
def customer_update(cid):
    need_any(('master', 'inv'), ('master', 'acc'))
    d = body()
    f = {k: _text(d, k, n) for k, n in (('name', 160), ('mobile', 20), ('gstin', 15)) if k in d}
    if 'credit_limit' in d:
        f['credit_limit'] = float(money(d['credit_limit']))
    cur = db.one('SELECT type FROM customers WHERE id=:i', i=cid)
    if cur and cur['type'] == 'Student' and set(f) - {'mobile'}:
        raise bad('Student customers follow the ERP record; only the mobile can be changed here.', 'erp_owned')
    return jsonify(_update('customers', ['id'], [cid], f, 'Customer', cid, 'accounts'))


# ---------------------------------------------------------------- packages
@bp.get('/packages')
@login_required
def packages_all():
    rows = db.clean(db.q('SELECT * FROM packages WHERE company IN :c ORDER BY company, name', c=tuple(g.user['companies']) or ('',)))
    lines = db.clean(db.q('SELECT * FROM package_lines ORDER BY package_id, line_no'))
    for p in rows:
        p['lines'] = [ln for ln in lines if ln['package_id'] == p['id']]
    return jsonify(rows)


def _package_lines(co, raw):
    out = []
    for n, ln in enumerate(raw or [], 1):
        q = int(ln.get('qty') or 0)
        if q <= 0:
            raise bad('Every package line needs a quantity.', 'bad_quantity')
        if ln.get('item_id'):
            if not db.val('SELECT COUNT(*) FROM items WHERE id=:i AND company=:c', i=ln['item_id'], c=co):
                raise bad(f"Item {ln['item_id']} is not a {co} item.", 'unknown_item')
            out.append(PackageLine(line_no=n, item_id=ln['item_id'], qty=q))
        elif ln.get('category'):
            if not db.val('SELECT COUNT(*) FROM item_categories WHERE company=:c AND name=:n', c=co, n=ln['category']):
                raise bad(f"Unknown category {ln['category']}.", 'bad_category')
            out.append(PackageLine(line_no=n, category=ln['category'], qty=q))
        else:
            raise bad('Each line needs an item or a category.', 'invalid')
    if not out:
        raise bad('Add at least one line.', 'no_lines')
    return out


@bp.post('/packages')
@require('master', 'inv')
def package_add():
    d = body()
    co = d.get('company')
    if co not in g.user['companies']:
        raise denied('Choose a company you have access to.', 'company_denied')
    disc = Decimal(str(d.get('disc_pct') or 0))
    if disc < 0 or disc > 50:
        raise bad('Package discount must be 0-50%.', 'bad_discount')
    n = (db.val('SELECT COUNT(*) FROM packages WHERE company=:c', c=co) or 0) + 1
    pid = f'PK-{co}-{n:03d}'
    while db.val('SELECT COUNT(*) FROM packages WHERE id=:i', i=pid):
        n += 1
        pid = f'PK-{co}-{n:03d}'
    p = Package(id=pid, company=co, name=_text(d, 'name', 160, True), school_id=d.get('school_id') or None, class_name=_text(d, 'class_name', 20),
                gender=d.get('gender') or None, disc_pct=disc, active=True)
    p.lines = _package_lines(co, d.get('lines'))
    db.add(p)
    audit.write('Package created', pid, new={'name': p.name, 'disc_pct': float(disc), 'lines': d.get('lines')}, company=co)
    return jsonify({'id': pid}), 201


@bp.put('/packages/<pid>')
@require('master', 'inv')
def package_update(pid):
    p = db.session().get(Package, pid, with_for_update=True)
    if not p or p.company not in g.user['companies']:
        raise not_found('Package not found.')
    d = body()
    old = {'name': p.name, 'disc_pct': float(p.disc_pct), 'active': p.active, 'lines': [(x.item_id or x.category, x.qty) for x in p.lines]}
    for k in ('name', 'class_name'):
        if k in d:
            setattr(p, k, _text(d, k, 160 if k == 'name' else 20, k == 'name'))
    for k in ('school_id', 'gender'):
        if k in d:
            setattr(p, k, d[k] or None)
    if 'disc_pct' in d:
        p.disc_pct = Decimal(str(d['disc_pct'] or 0))
    if 'active' in d:
        p.active = bool(d['active'])
    if 'lines' in d:
        p.lines.clear()
        db.session().flush()
        p.lines.extend(_package_lines(p.company, d['lines']))
    audit.write('Package changed', pid, old=old, new={'name': p.name, 'disc_pct': float(p.disc_pct), 'active': p.active,
                                                       'lines': [(x.item_id or x.category, x.qty) for x in p.lines]}, company=p.company)
    return jsonify({'id': pid})


# ---------------------------------------------------------------- chart of accounts
@bp.post('/accounts')
@require('master', 'acc')
def account_add():
    d = body()
    co, code = d.get('company'), str(d.get('code') or '').strip()
    if co not in g.user['companies']:
        raise denied('Choose a company you have access to.', 'company_denied')
    if not re.fullmatch(r'\d{4}', code):
        raise bad('Account code is 4 digits (1xxx assets, 2xxx liabilities, 3xxx equity, 4xxx income, 5-6xxx expenses).', 'invalid')
    grp = {'1': 'A', '2': 'L', '3': 'E', '4': 'I', '5': 'X', '6': 'X'}.get(code[0])
    if not grp:
        raise bad('Account code must start with 1-6.', 'invalid')
    if db.val('SELECT COUNT(*) FROM accounts WHERE company=:c AND code=:a', c=co, a=code):
        raise conflict('This account code exists.', 'duplicate_code')
    name = _text(d, 'name', 120, True)
    if db.val('SELECT COUNT(*) FROM accounts WHERE company=:c AND name=:n', c=co, n=name):
        raise conflict('An account with this name exists.', 'duplicate_name')
    db.insert('accounts', dict(company=co, code=code, name=name, grp=grp, is_control=0, active=1))
    audit.write('Account added', f'{co} {code}', new=name, category='accounts', company=co)
    return jsonify({'company': co, 'code': code, 'grp': grp}), 201


@bp.put('/accounts/<co>/<code>')
@require('master', 'acc')
def account_update(co, code):
    d = body()
    f = {}
    if 'name' in d:
        f['name'] = _text(d, 'name', 120, True)
    if 'active' in d:
        if not d['active']:
            if code in CONTROL:
                raise conflict('Control accounts cannot be switched off.', 'control_account')
            bal = db.val('''SELECT COALESCE(SUM(l.debit-l.credit),0) FROM journal_lines l JOIN journals j ON j.id=l.journal_id
                            WHERE j.company=:c AND l.account=:a''', c=co, a=code) or 0
            if abs(Decimal(str(bal))) > Decimal('0.004'):
                raise conflict(f'The account still has a balance of Rs {bal}. Move it with a journal first.', 'account_has_balance')
        f['active'] = 1 if d['active'] else 0
    return jsonify(_update('accounts', ['company', 'code'], [co, code], f, 'Account', f'{co} {code}', 'accounts'))


# ---------------------------------------------------------------- roles, permissions, approval limits
@bp.get('/roles')
@login_required
def roles_full():
    need_any(('view', 'ctl'), ('users', 'ctl'))
    rows = db.clean(db.q('''SELECT r.*, (SELECT COUNT(*) FROM users u WHERE u.main_role=r.name AND u.status='Active') AS users FROM roles r
                            ORDER BY approval_rank DESC, name'''))
    perms, limits = {}, {}
    for p in db.q('SELECT role, screen_key, action FROM permissions'):
        perms.setdefault(p['role'], {}).setdefault(p['screen_key'], []).append(p['action'])
    for x in db.q('SELECT role, limit_key, amount FROM approval_limits'):
        limits.setdefault(x['role'], {})[x['limit_key']] = float(x['amount'])
    top = {}
    for L in limits.values():
        for k, v in L.items():
            top[k] = max(top.get(k, 0), v)
    for r in rows:
        r['permissions'], r['limits'] = perms.get(r['name'], {}), limits.get(r['name'], {})
        if r['name'] == 'Super Admin':       # 2.7: shown as it works — every action on every screen, the highest limit of any role
            r['permissions'] = {k: list(ACTIONS) for k, _ in SCREENS}
            r['limits'] = {k: max(r['limits'].get(k, 0), top.get(k, 0)) for k, _ in LIMITS}
            r['all_rights'] = True
    return jsonify(rows)


def _role_payload(d, name):
    perms = {}
    for key, acts in (d.get('permissions') or {}).items():
        if key not in dict(SCREENS):
            raise bad(f'Unknown screen {key}.', 'invalid')
        bad_acts = set(acts) - set(ACTIONS)
        if bad_acts:
            raise bad(f'Unknown action(s): {", ".join(sorted(bad_acts))}.', 'invalid')
        perms[key] = sorted(set(acts))
    limits = {}
    for k, v in (d.get('limits') or {}).items():
        if k not in dict(LIMITS):
            raise bad(f'Unknown limit {k}.', 'invalid')
        limits[k] = float(money(v or 0))
    rank = float(d.get('approval_rank') or 0)
    if rank < 0 or rank > 10:
        raise bad('Approval rank is 0-10 (0 = cannot approve).', 'invalid')
    return {'name': name, 'description': str(d.get('description') or '').strip()[:200] or None, 'approval_rank': rank,
            'read_only': 1 if d.get('read_only') else 0, 'active': 0 if d.get('active') is False else 1, 'permissions': perms, 'limits': limits}


@handler('mc:role')
def apply_role(p, apr):
    name = p['name']
    exists = db.val('SELECT COUNT(*) FROM roles WHERE name=:n', n=name)
    old = None
    if exists:
        old = {'permissions': {}, 'limits': {}}
        for r in db.q('SELECT screen_key, action FROM permissions WHERE role=:n', n=name):
            old['permissions'].setdefault(r['screen_key'], []).append(r['action'])
        db.run('UPDATE roles SET description=:d, approval_rank=:r, read_only=:o, active=:a WHERE name=:n', d=p['description'], r=p['approval_rank'],
               o=p['read_only'], a=p['active'], n=name)
    else:
        db.insert('roles', dict(name=name, description=p['description'], approval_rank=p['approval_rank'], read_only=p['read_only'], is_custom=1, active=1))
    db.run('DELETE FROM permissions WHERE role=:n', n=name)
    for key, acts in p['permissions'].items():
        for a in acts:
            db.insert('permissions', dict(role=name, screen_key=key, action=a))
    db.run('DELETE FROM approval_limits WHERE role=:n', n=name)
    for k, v in p['limits'].items():
        db.insert('approval_limits', dict(role=name, limit_key=k, amount=v))
    audit.write('Role created' if not exists else 'Role permissions changed', name, old=old, new={'permissions': p['permissions'], 'limits': p['limits'],
                'rank': p['approval_rank']}, reason=apr.get('reason'), category='security', status='Approved')
    return f'Role {name} saved'


def _role_change(name, d):
    reason = str(d.get('reason') or '').strip()
    if not reason:
        raise bad('Give the reason for this change.', 'reason_required')
    payload = _role_payload(d, name)
    if not mc_on('access'):
        return {'status': 'Applied', 'result': apply_role(payload, {'reason': reason})}, 200
    return mc_request('mc', f'Role · {name}', payload, route_role('mc', None, 0, 'Director'), sub_kind='role', reason=reason,
                      super_admin_only=False, signature=f'role:{name}'), 202


@bp.post('/roles')
@require('users', 'ctl')
@high_risk('users')
def role_add():
    d = body()
    name = str(d.get('name') or '').strip()[:60]
    if not name:
        raise bad('Role name is required.', 'missing_name')
    if db.val('SELECT COUNT(*) FROM roles WHERE name=:n', n=name):
        raise conflict('A role with this name exists.', 'duplicate_name')
    if d.get('copy_from') and not d.get('permissions'):
        src = d['copy_from']
        d['permissions'] = {}
        for r in db.q('SELECT screen_key, action FROM permissions WHERE role=:n', n=src):
            d['permissions'].setdefault(r['screen_key'], []).append(r['action'])
        d['limits'] = {x['limit_key']: float(x['amount']) for x in db.q('SELECT limit_key, amount FROM approval_limits WHERE role=:n', n=src)}
    out, code = _role_change(name, d)
    return jsonify(out), (201 if code == 200 else code)


@bp.put('/roles/<path:name>')
@require('users', 'ctl')
@high_risk('users')
def role_update(name):
    if not db.val('SELECT COUNT(*) FROM roles WHERE name=:n', n=name):
        raise not_found('Role not found.')
    if name == 'Super Admin':
        raise conflict('The Super Admin role is fixed so that the system can always be administered.', 'protected_role')
    out, code = _role_change(name, body())
    return jsonify(out), code


# ---------------------------------------------------------------- discount schemes
@bp.get('/schemes')
@login_required
def schemes():
    return jsonify(db.clean(db.q('SELECT * FROM discount_schemes WHERE company IN :c ORDER BY active DESC, company, name', c=tuple(g.user['companies']) or ('',))))


def _scheme_fields(d, co):
    scope = d.get('scope')
    if scope not in ('all', 'category', 'school', 'item'):
        raise bad('Applies to must be all, category, school or item.', 'invalid')
    pct = Decimal(str(d.get('pct') or 0))
    if pct <= 0 or pct > 50:
        raise bad('Scheme discount must be more than 0 and at most 50%.', 'bad_discount')
    f = {'scope': scope, 'pct': pct, 'category': None, 'school_id': None, 'item_id': None,
         'valid_from': parse_date(d['valid_from']) if d.get('valid_from') else None, 'valid_to': parse_date(d['valid_to']) if d.get('valid_to') else None,
         'online': bool(d.get('online', True)), 'active': bool(d.get('active', True))}
    if f['valid_from'] and f['valid_to'] and f['valid_to'] < f['valid_from']:
        raise bad('End date is before the start date.', 'bad_date')
    if scope == 'category':
        if not db.val('SELECT COUNT(*) FROM item_categories WHERE company=:c AND name=:n', c=co, n=d.get('category')):
            raise bad('Choose the category.', 'bad_category')
        f['category'] = d['category']
    if scope == 'school':
        if not db.val('SELECT COUNT(*) FROM schools WHERE id=:s', s=d.get('school_id')):
            raise bad('Choose the school.', 'unknown_school')
        f['school_id'] = d['school_id']
    if scope == 'item':
        if not db.val('SELECT COUNT(*) FROM items WHERE id=:i AND company=:c', i=d.get('item_id'), c=co):
            raise bad('That item code does not exist for this company.', 'unknown_item')
        f['item_id'] = d['item_id']
    return f


@bp.post('/schemes')
@require('edit', 'set')
def scheme_add():
    d = body()
    co = d.get('company')
    if co not in g.user['companies']:
        raise denied('Choose a company you have access to.', 'company_denied')
    name = _text(d, 'name', 120, True)
    if db.val('SELECT COUNT(*) FROM discount_schemes WHERE name=:n', n=name):
        raise conflict('A scheme with this name exists.', 'duplicate_name')
    s = DiscountScheme(company=co, name=name, created_by=g.user['id'], **_scheme_fields(d, co))
    db.add(s)
    audit.write('Discount scheme created', name, new={**d}, category='settings', company=co)
    return jsonify({'id': s.id}), 201


@bp.put('/schemes/<int:sid>')
@require('edit', 'set')
def scheme_update(sid):
    s = db.session().get(DiscountScheme, sid, with_for_update=True)
    if not s or s.company not in g.user['companies']:
        raise not_found('Scheme not found.')
    d = body()
    old = s.as_dict()
    for k, v in _scheme_fields({**old, **d}, s.company).items():
        setattr(s, k, v)
    if d.get('name'):
        s.name = _text(d, 'name', 120, True)
    new = s.as_dict()
    audit.write('Discount scheme changed', s.name, old={k: str(old[k]) for k in old if old[k] != new[k]},
                new={k: str(new[k]) for k in new if old[k] != new[k]}, category='settings', company=s.company)
    return jsonify({'id': sid})


# ---------------------------------------------------------------- custom fields
ENTITIES = {'item': ('items', 'id'), 'customer': ('customers', 'id'), 'supplier': ('suppliers', 'id'), 'student': ('students', 'adm_no')}


@bp.get('/custom-fields')
@login_required
def custom_fields():
    sql = 'SELECT * FROM custom_fields'
    p = {}
    if request.args.get('entity'):
        sql += ' WHERE entity=:e'
        p['e'] = request.args['entity']
    return jsonify(db.clean(db.q(sql + ' ORDER BY entity, sort, id', **p)))


def _cf_fields(d):
    if d.get('entity') not in ENTITIES:
        raise bad('Field belongs to items, customers, suppliers or students.', 'invalid')
    if d.get('type', 'text') not in ('text', 'number', 'date', 'select'):
        raise bad('Type must be text, number, date or select.', 'invalid')
    opts = [o.strip() for o in (d.get('options') or []) if str(o).strip()] if isinstance(d.get('options'), list) else \
        [o.strip() for o in str(d.get('options') or '').split(',') if o.strip()]
    if d.get('type') == 'select' and not opts:
        raise bad('A drop-down field needs its options.', 'invalid')
    return {'entity': d['entity'], 'label': _text(d, 'label', 60, True), 'type': d.get('type', 'text'), 'options': opts or None,
            'required': bool(d.get('required')), 'sort': int(d.get('sort') or 0), 'active': bool(d.get('active', True))}


@bp.post('/custom-fields')
@require('edit', 'set')
def custom_field_add():
    f = CustomField(**_cf_fields(body()))
    db.add(f)
    audit.write('Custom field added', f'{f.entity}: {f.label}', new=f.as_dict(), category='settings')
    return jsonify({'id': f.id}), 201


@bp.put('/custom-fields/<int:fid>')
@require('edit', 'set')
def custom_field_update(fid):
    f = db.session().get(CustomField, fid, with_for_update=True)
    if not f:
        raise not_found('Field not found.')
    old = f.as_dict()
    for k, v in _cf_fields({**old, **body()}).items():
        setattr(f, k, v)
    audit.write('Custom field changed', f'{f.entity}: {f.label}', old=old, new=f.as_dict(), category='settings')
    return jsonify({'id': fid})


@bp.get('/custom-values/<entity>/<path:ref>')
@login_required
def custom_values(entity, ref):
    if entity not in ENTITIES:
        raise not_found('Unknown entity.')
    return jsonify({str(r['field_id']): r['value'] for r in db.q('''SELECT v.field_id, v.value FROM custom_values v JOIN custom_fields f ON f.id=v.field_id
                                                                  WHERE f.entity=:e AND v.ref=:r''', e=entity, r=ref)})


@bp.put('/custom-values/<entity>/<path:ref>')
@login_required
def custom_values_save(entity, ref):
    if entity not in ENTITIES:
        raise not_found('Unknown entity.')
    need_any(('master', 'inv'), ('master', 'acc'), ('edit', 'set'))
    table, col = ENTITIES[entity]
    if not db.val(f'SELECT COUNT(*) FROM {table} WHERE {col}=:r', r=ref):
        raise not_found(f'{entity.capitalize()} not found.')
    d = body()
    fields = {r['id']: r for r in db.clean(db.q('SELECT * FROM custom_fields WHERE entity=:e AND active=1', e=entity))}
    old = {str(r['field_id']): r['value'] for r in db.q('SELECT field_id, value FROM custom_values WHERE ref=:r AND field_id IN :f', r=ref,
                                                         f=tuple(fields) or (0,))}
    changed = {}
    for fid, fdef in fields.items():
        v = d.get(str(fid), old.get(str(fid)))
        v = None if v in (None, '') else str(v).strip()[:300]
        if fdef['required'] and not v:
            raise bad(f"{fdef['label']} is required.", 'missing_field')
        if v and fdef['type'] == 'number':
            try:
                float(v)
            except ValueError:
                raise bad(f"{fdef['label']} must be a number.", 'invalid') from None
        if v and fdef['type'] == 'select' and v not in (fdef['options'] or []):
            raise bad(f"{fdef['label']}: choose one of the options.", 'invalid')
        if v != old.get(str(fid)):
            changed[fdef['label']] = [old.get(str(fid)), v]
            db.run('INSERT INTO custom_values (field_id, ref, value) VALUES (:f, :r, :v) ON DUPLICATE KEY UPDATE value=:v, updated_at=NOW(6)', f=fid, r=ref, v=v)
    if changed:
        audit.write(f'{entity.capitalize()} custom fields changed', ref, old={k: v[0] for k, v in changed.items()}, new={k: v[1] for k, v in changed.items()})
    return jsonify({'changed': list(changed)})


# ---------------------------------------------------------------- scheduled report e-mails
@bp.get('/report-schedules')
@require('view', 'rep')
def schedules():
    rows = db.clean(db.q('SELECT s.*, u.name AS owner_name FROM report_schedules s LEFT JOIN users u ON u.id=s.owner_id ORDER BY s.active DESC, s.name'))
    return jsonify(rows)


@bp.post('/report-schedules')
@require('view', 'rep')
def schedule_add():
    need_any(('edit', 'set'), ('cfgrep', 'rep'))
    f = rs.validate(body())
    s = ReportSchedule(owner_id=g.user['id'], active=True, **f)
    db.add(s)
    audit.write('Report schedule created', s.name, new=f, category='settings')
    return jsonify({'id': s.id}), 201


@bp.put('/report-schedules/<int:sid>')
@require('view', 'rep')
def schedule_update(sid):
    need_any(('edit', 'set'), ('cfgrep', 'rep'))
    s = db.session().get(ReportSchedule, sid, with_for_update=True)
    if not s:
        raise not_found('Schedule not found.')
    d = body()
    old = s.as_dict()
    if set(d) - {'active'}:
        for k, v in rs.validate({**old, **d}).items():
            setattr(s, k, v)
    if 'active' in d:
        s.active = bool(d['active'])
    audit.write('Report schedule changed', s.name, old=old, new=s.as_dict(), category='settings')
    return jsonify({'id': sid})


@bp.post('/report-schedules/<int:sid>/run')
@require('view', 'rep')
def schedule_run(sid):
    need_any(('edit', 'set'), ('cfgrep', 'rep'))
    s = db.one('SELECT * FROM report_schedules WHERE id=:i', i=sid)
    if not s:
        raise not_found('Schedule not found.')
    return jsonify({'result': rs.run_one(rs.load(s))})
