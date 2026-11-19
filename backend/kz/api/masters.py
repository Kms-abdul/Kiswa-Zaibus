"""Masters: items (ISBN control, price-change approval), categories, suppliers, customers, students, schools, packages, accounts."""
import re

from flask import Blueprint, g, jsonify, request

from .. import db
from ..core import audit
from ..core.approvals import handler, needs_second_person, request as mc_request
from ..core.isbn import check as isbn_check
from ..core.rbac import body, can, company_ok, login_required, require
from ..core.routing import route_role
from ..errors import bad, conflict, denied, not_found
from ..models import Customer, Item, Supplier
from ..services.common import money

bp = Blueprint('masters', __name__, url_prefix='/api')
ITEM_FIELDS = ('name', 'category', 'size', 'colour', 'gender', 'class_name', 'term', 'academic_year', 'school_id', 'hsn', 'gst_rate',
               'barcode', 'supplier_id', 'reorder_level', 'bin_location', 'uom', 'active')


def _companies(arg=None):
    u = g.user
    if arg:
        if not company_ok(u, arg):
            raise denied('You do not have access to this company.', 'company_denied')
        return [arg]
    return u['companies'] or ['']


def page_args():
    page = max(1, int(request.args.get('page', 1) or 1))
    size = min(200, max(1, int(request.args.get('size', 50) or 50)))
    return page, size


# ---------------------------------------------------------------- items
ITEM_SCREENS = ('inv', 'pos', 'pur', 'onl', 'msea')


def _need_item_access():
    if not any(can(g.user, 'view', k) for k in ITEM_SCREENS):
        raise denied('You do not have access to the item list.', 'forbidden')


@bp.get('/items')
@login_required
def list_items():
    # Counter staff search items by name while billing, buyers while raising requisitions: any of these screens may list items.
    _need_item_access()
    cos = _companies(request.args.get('company'))
    sql = 'SELECT * FROM items WHERE company IN :c'
    p = {'c': tuple(cos)}
    qs = (request.args.get('q') or '').strip()
    if qs:
        sql += ' AND (name LIKE :q OR id LIKE :q OR barcode=:e OR isbn13=:e OR category LIKE :q OR class_name=:e)'
        p['q'], p['e'] = f'%{qs}%', re.sub(r'[\s-]', '', qs)
    for f in ('category', 'school_id', 'class_name'):
        if request.args.get(f):
            sql += f' AND {f}=:{f}'
            p[f] = request.args[f]
    if request.args.get('active', '1') == '1':
        sql += ' AND active=1'
    page, size = page_args()
    total = db.val(sql.replace('SELECT *', 'SELECT COUNT(*)', 1), **p)
    rows = db.clean(db.q(sql + f' ORDER BY company, category, name, size LIMIT {size} OFFSET {(page - 1) * size}', **p))
    if not can(g.user, 'cost', 'inv'):
        for r in rows:
            r.pop('cost', None)
    return jsonify({'total': total, 'page': page, 'size': size, 'rows': rows})


@bp.get('/items/lookup')
@login_required
def lookup_item():
    """Barcode / ISBN / code scan at the counter: exact match only."""
    code = re.sub(r'[\s-]', '', request.args.get('code') or '')
    co = request.args.get('company')
    r = db.one('SELECT * FROM items WHERE company IN :c AND active=1 AND (barcode=:x OR isbn13=:x OR isbn=:x OR id=:x) LIMIT 1',
               c=tuple(_companies(co)), x=code)
    if not r:
        raise not_found(f'No item with code {code}.', 'unknown_item')
    r = db.clean(r)
    if not can(g.user, 'cost', 'inv'):
        r.pop('cost', None)
    return jsonify(r)


@bp.get('/items/<iid>')
@login_required
def get_item(iid):
    _need_item_access()
    it = db.session().get(Item, iid)
    if not it or not company_ok(g.user, it.company):
        raise not_found('Item not found.', 'unknown_item')
    out = it.as_dict()
    if not can(g.user, 'cost', 'inv'):
        out.pop('cost', None)
    from ..core.rbac import scope_sql
    where, p = scope_sql(g.user, 's.branch_id')
    out['stock'] = db.clean(db.q(f'SELECT s.branch_id, s.qty, s.reserved FROM stock_balance s WHERE s.item_id=:i AND {where}', i=iid, **p))
    return jsonify(out)


def _validate_item(d, company, existing=None):
    cat = db.one('SELECT * FROM item_categories WHERE company=:c AND name=:n', c=company, n=d.get('category') or (existing and existing.category))
    if not cat:
        raise bad('Unknown category for this company.', 'bad_category')
    out = {}
    if 'isbn' in d:
        if d['isbn']:
            clean, isbn13, err = isbn_check(d['isbn'])
            if err:
                raise bad(err, 'bad_isbn')
            dup = db.one('SELECT id, name FROM items WHERE company=:c AND isbn13=:i AND id<>:me', c=company, i=isbn13, me=existing.id if existing else '')
            if dup:
                raise conflict(f'This ISBN is already used by {dup["id"]} {dup["name"]}.', 'duplicate_isbn', item=dup['id'])
            out.update(isbn=clean, isbn13=isbn13, isbn_na_reason=None)
        else:
            out.update(isbn=None, isbn13=None)
    if cat['isbn_required'] and not (out.get('isbn13') or (existing and existing.isbn13)):
        na = (d.get('isbn_na_reason') or (existing and existing.isbn_na_reason) or '').strip()
        if not na:
            raise bad('ISBN is required for this category (or give the reason it is not available).', 'isbn_required')
        out['isbn_na_reason'] = na
    if d.get('barcode'):
        if db.val('SELECT COUNT(*) FROM items WHERE company=:c AND barcode=:b AND id<>:me', c=company, b=d['barcode'], me=existing.id if existing else ''):
            raise conflict('This barcode is already used by another item.', 'duplicate_barcode')
    for f in ITEM_FIELDS:
        if f in d:
            out[f] = d[f] if d[f] != '' else None
    if 'gst_rate' in out and out['gst_rate'] is not None and float(out['gst_rate']) not in (0, 5, 12, 18, 28):
        raise bad('GST rate must be 0, 5, 12, 18 or 28.', 'bad_gst_rate')
    return out


@bp.post('/items')
@require('master', 'inv')
def create_item():
    d = body()
    company = d.get('company')
    if not company or not company_ok(g.user, company):
        raise denied('Choose a company you have access to.', 'company_denied')
    if not str(d.get('name') or '').strip():
        raise bad('Name is required.', 'missing_name')
    fields = _validate_item(d, company)
    price = money(d.get('price'))
    if price < 0:
        raise bad('Price cannot be negative.', 'bad_price')
    n = (db.val("SELECT MAX(CAST(SUBSTRING_INDEX(id, '-', -1) AS UNSIGNED)) FROM items WHERE company=:c AND id REGEXP CONCAT('^', :c, '-[0-9]+$')", c=company) or 0) + 1
    it = Item(id=f'{company}-{n:04d}', company=company, price=price, unpriced=price == 0, cost=0, **{k: v for k, v in fields.items() if k != 'active'})
    it.name = d['name'].strip()
    db.add(it)
    audit.write('Item created', it.id, new=it.as_dict(), category='master', company=company)
    return jsonify(it.as_dict()), 201


@bp.put('/items/<iid>')
@require('master', 'inv')
def update_item(iid):
    it = db.session().get(Item, iid, with_for_update=True)
    if not it or not company_ok(g.user, it.company):
        raise not_found('Item not found.', 'unknown_item')
    d = body()
    if 'price' in d or 'cost' in d:
        raise bad('Prices change through a price-change request (approval), and cost only through purchases.', 'use_price_change')
    old = it.as_dict()
    for k, v in _validate_item(d, it.company, it).items():
        setattr(it, k, v)
    if d.get('name'):
        it.name = d['name'].strip()
    changed = {k: [old[k], v] for k, v in it.as_dict().items() if old.get(k) != v}
    audit.write('Item changed', iid, old={k: v[0] for k, v in changed.items()}, new={k: v[1] for k, v in changed.items()}, category='master', company=it.company)
    return jsonify(it.as_dict())


@handler('mc:price')
def apply_price(p, apr):
    out = []
    for r in p['rows']:
        it = db.session().get(Item, r['id'], with_for_update=True)
        if not it:
            continue
        old = it.price
        it.price = money(r['price'])
        it.unpriced = False
        if r.get('gst_rate') is not None:
            it.gst_rate = r['gst_rate']
        audit.write('Price changed', r['id'], old=str(old), new=str(it.price), reason=apr['reason'], category='master', status='Approved', company=it.company)
        out.append(r['id'])
    return f'Price changed for {len(out)} item(s)'


@bp.post('/items/price-change')
@require('edit', 'inv')
def price_change():
    d = body()
    rows = d.get('rows') or []
    reason = str(d.get('reason') or '').strip()
    if not rows:
        raise bad('No prices given.', 'invalid')
    if not reason:
        raise bad('Please give a reason for the price change.', 'reason_required')
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        raise bad('Prices must be a list of {id, price} rows.', 'invalid_input')
    clean, cos = [], set()
    for r in rows:
        it = db.session().get(Item, r.get('id'))
        if not it or not company_ok(g.user, it.company):
            raise bad(f'Unknown item {r.get("id")}.', 'unknown_item')
        try:
            price = round(float(r.get('price')), 2)
        except (TypeError, ValueError):
            raise bad(f'Price for {it.id} is not a number.', 'bad_price')
        if price <= 0:
            raise bad(f'Price for {it.id} must be more than zero.', 'bad_price')
        clean.append({'id': it.id, 'name': it.name, 'old': float(it.price), 'price': price, 'gst_rate': r.get('gst_rate')})
        cos.add(it.company)
    payload = {'rows': clean}
    if not needs_second_person('price', route_role('mc', None, 0, 'CFO')):
        return jsonify({'status': 'Applied', 'result': apply_price(payload, {'reason': reason})})
    res = mc_request('mc', f'Price change · {len(clean)} item(s)', payload, route_role('mc', None, 0, 'CFO'), sub_kind='price',
                     company=cos.pop() if len(cos) == 1 else None, reason=reason,
                     detail='; '.join(f"{r['name']}: Rs {r['old']} -> Rs {r['price']}" for r in clean[:6]),
                     signature='price:' + ','.join(f"{r['id']}={r['price']}" for r in clean))
    return jsonify(res), 202


# ---------------------------------------------------------------- simple masters
@bp.get('/categories')
@login_required
def categories():
    return jsonify(db.clean(db.q('SELECT * FROM item_categories WHERE company IN :c ORDER BY company, name', c=tuple(_companies(request.args.get('company'))))))


@bp.post('/categories')
@require('master', 'inv')
def add_category():
    d = body()
    co = d.get('company')
    if not company_ok(g.user, co) or not str(d.get('name') or '').strip():
        raise bad('Company and name are required.', 'invalid')
    if db.val('SELECT COUNT(*) FROM item_categories WHERE company=:c AND name=:n', c=co, n=d['name']):
        raise conflict('This category already exists.', 'duplicate_category')
    db.insert('item_categories', dict(company=co, name=d['name'].strip(), hsn=d.get('hsn'), gst_rate=d.get('gst_rate') or 0,
                                      max_disc=d.get('max_disc'), isbn_required=1 if d.get('isbn_required') else 0))
    audit.write('Category created', f"{co}:{d['name']}", new=d, category='master', company=co)
    return jsonify({'ok': True}), 201


@bp.get('/suppliers')
@login_required
def suppliers():
    return jsonify(db.clean(db.q('SELECT * FROM suppliers WHERE company IN :c ORDER BY name', c=tuple(_companies(request.args.get('company'))))))


@bp.post('/suppliers')
@require('master', 'pur')
def add_supplier():
    d = body()
    co = d.get('company')
    if not company_ok(g.user, co) or not str(d.get('name') or '').strip():
        raise bad('Company and name are required.', 'invalid')
    if d.get('gstin') and not re.fullmatch(r'\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]', d['gstin'].upper()):
        raise bad('GSTIN is not valid (15 characters, e.g. 36AADCD5521K1Z3).', 'bad_gstin')
    if db.val('SELECT COUNT(*) FROM suppliers WHERE company=:c AND name=:n', c=co, n=d['name'].strip()):
        raise conflict('A supplier with this name exists.', 'duplicate_supplier')
    n = (db.val("SELECT COUNT(*) FROM suppliers WHERE company=:c", c=co) or 0) + 1
    sid = f"{co[0]}S{n:03d}"
    while db.val('SELECT COUNT(*) FROM suppliers WHERE id=:i', i=sid):
        n += 1
        sid = f"{co[0]}S{n:03d}"
    s = Supplier(id=sid, company=co, name=d['name'].strip(), gstin=(d.get('gstin') or '').upper() or None, city=d.get('city'),
                 terms_days=int(d.get('terms_days') or 30), contact=d.get('contact'), bank_name=d.get('bank_name'),
                 bank_account=d.get('bank_account'), bank_ifsc=d.get('bank_ifsc'))
    db.add(s)
    audit.write('Supplier created', sid, new=s.as_dict(), category='master', company=co)
    return jsonify(s.as_dict()), 201


@bp.get('/customers')
@login_required
def customers():
    qs = (request.args.get('q') or '').strip()
    sql = 'SELECT * FROM customers WHERE company IN :c'
    p = {'c': tuple(_companies(request.args.get('company')))}
    if qs:
        sql += ' AND (name LIKE :q OR adm_no=:e OR mobile LIKE :q)'
        p['q'], p['e'] = f'%{qs}%', qs
    return jsonify(db.clean(db.q(sql + ' ORDER BY type, name LIMIT 200', **p)))


@bp.post('/customers')
@require('add', 'acc')
def add_customer():
    d = body()
    co = d.get('company')
    if not company_ok(g.user, co) or not str(d.get('name') or '').strip():
        raise bad('Company and name are required.', 'invalid')
    if d.get('type') not in ('Institution', 'Walk-in', 'Staff'):
        raise bad('Type must be Institution, Walk-in or Staff (students are created from the ERP).', 'bad_customer_type')
    n = (db.val('SELECT COUNT(*) FROM customers WHERE company=:c', c=co) or 0) + 1
    c = Customer(id=f'{co}-C{n:05d}', company=co, type=d['type'], name=d['name'].strip(), mobile=d.get('mobile'), gstin=d.get('gstin'),
                 credit_limit=money(d.get('credit_limit')))
    db.add(c)
    audit.write('Customer created', c.id, new=c.as_dict(), category='master', company=co)
    return jsonify(c.as_dict()), 201


@bp.get('/students')
@login_required
def students():
    if not (can(g.user, 'view', 'pos') or can(g.user, 'view', 'msea') or can(g.user, 'view', 'onl')):
        raise denied()
    qs = (request.args.get('q') or '').strip()
    sql = 'SELECT * FROM students WHERE 1=1'
    p = {}
    if qs:
        m = re.search(r'MSA\d{6}', qs.upper())
        if m:
            sql += ' AND adm_no=:a'
            p['a'] = m.group(0)
        else:
            sql += ' AND (name LIKE :q OR adm_no LIKE :q OR parent_mobile LIKE :q OR erp_ref=:e)'
            p['q'], p['e'] = f'%{qs}%', qs
    if request.args.get('category'):
        sql += ' AND category=:cat'
        p['cat'] = request.args['category']
    return jsonify(db.clean(db.q(sql + ' ORDER BY name LIMIT 100', **p)))


@bp.get('/students/<adm>')
@login_required
def student(adm):
    s = db.one('SELECT * FROM students WHERE adm_no=:a', a=adm)
    if not s:
        raise not_found('Student not found. Check the admission number or run the ERP sync.', 'unknown_student')
    s = db.clean(s)
    s['vouchers'] = db.clean(db.q("SELECT * FROM msea_vouchers WHERE adm_no=:a ORDER BY created_at DESC", a=adm))
    s['purchases'] = db.clean(db.q('''SELECT no, company, inv_date, total, status FROM invoices WHERE adm_no=:a ORDER BY id DESC LIMIT 20''', a=adm))
    return jsonify(s)


@bp.get('/schools')
@login_required
def schools():
    return jsonify(db.clean(db.q('SELECT * FROM schools ORDER BY name')))


@bp.get('/packages')
@login_required
def packages():
    cos = tuple(_companies(request.args.get('company')))
    rows = db.clean(db.q('SELECT * FROM packages WHERE company IN :c AND active=1 ORDER BY name', c=cos))
    for r in rows:
        r['lines'] = db.clean(db.q('SELECT * FROM package_lines WHERE package_id=:p ORDER BY line_no', p=r['id']))
    return jsonify(rows)


@bp.get('/accounts')
@login_required
def accounts():
    if not (can(g.user, 'view', 'acc') or can(g.user, 'fin', 'rep')):
        raise denied()
    return jsonify(db.clean(db.q('SELECT * FROM accounts WHERE company IN :c ORDER BY company, code', c=tuple(_companies(request.args.get('company'))))))
