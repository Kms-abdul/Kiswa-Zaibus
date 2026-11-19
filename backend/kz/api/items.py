"""Item master and stock enquiry. Price changes go through maker-checker ('price')."""
import re

from flask import Blueprint, g, jsonify, request

from .. import audit, db
from ..approvals import handler, mc_on, request as mc_request
from ..errors import bad, conflict, denied, not_found
from ..isbn import check as isbn_check
from ..rbac import body, can, company_ok, in_scope, require, scope_sql
from ..routing import route_role

bp = Blueprint('items', __name__, url_prefix='/api')


@bp.get('/items')
@require('view', 'inv')
def list_items():
    u = g.user
    co = request.args.get('company')
    cos = [co] if co else u['companies']
    if co and not company_ok(u, co):
        raise denied('You do not have access to this company.')
    sql = 'SELECT * FROM items WHERE company IN :c'
    p = {'c': tuple(cos) or ('',)}
    qs = (request.args.get('q') or '').strip()
    if qs:
        sql += ' AND (name LIKE :q OR id LIKE :q OR barcode=:e OR isbn13=:e OR category LIKE :q)'
        p['q'], p['e'] = f'%{qs}%', re.sub(r'[\s-]', '', qs)
    if request.args.get('category'):
        sql += ' AND category=:cat'
        p['cat'] = request.args['category']
    page = max(1, int(request.args.get('page', 1)))
    size = min(200, max(1, int(request.args.get('size', 50))))
    total = db.val(sql.replace('SELECT *', 'SELECT COUNT(*)', 1), **p)
    rows = db.clean(db.q(sql + f' ORDER BY company, category, name LIMIT {size} OFFSET {(page - 1) * size}', **p))
    if not can(u, 'cost', 'inv'):
        for r in rows:
            r.pop('cost', None)
    return jsonify({'total': total, 'page': page, 'size': size, 'rows': rows})


@bp.get('/items/<iid>')
@require('view', 'inv')
def get_item(iid):
    i = db.one('SELECT * FROM items WHERE id=:i', i=iid)
    if not i or not company_ok(g.user, i['company']):
        raise not_found('Item not found.')
    where, p = scope_sql(g.user, 's.branch_id')
    i = db.clean(i)
    if not can(g.user, 'cost', 'inv'):
        i.pop('cost', None)
    i['stock'] = db.clean(db.q(f'SELECT s.branch_id, s.qty, s.reserved FROM stock_balance s WHERE s.item_id=:i AND {where}', i=iid, **p))
    return jsonify(i)


@bp.post('/items')
@require('master', 'inv')
def create_item():
    d = body()
    u = g.user
    for k in ('company', 'name', 'category'):
        if not d.get(k):
            raise bad(f'{k.capitalize()} is required.', 'missing_' + k)
    if not company_ok(u, d['company']):
        raise denied('You do not have access to this company.')
    cat = db.one('SELECT * FROM item_categories WHERE company=:c AND name=:n', c=d['company'], n=d['category'])
    if not cat:
        raise bad('Unknown category for this company.', 'bad_category')
    clean = isbn13 = na = None
    if d.get('isbn'):
        clean, isbn13, err = isbn_check(d['isbn'])
        if err:
            raise bad(err, 'bad_isbn')
        dup = db.one('SELECT id, name FROM items WHERE company=:c AND isbn13=:i', c=d['company'], i=isbn13)
        if dup:
            raise conflict(f'This ISBN is already used by {dup["id"]} {dup["name"]}.', 'duplicate_isbn', item=dup['id'])
    elif cat['isbn_required']:
        na = (d.get('isbn_na_reason') or '').strip()
        if not na:
            raise bad('ISBN is required for this category (or give the reason it is not available).', 'isbn_required')
    price = float(d.get('price') or 0)
    if price < 0:
        raise bad('Price cannot be negative.')
    prefix = d['company']
    n = (db.val("SELECT MAX(CAST(SUBSTRING_INDEX(id, '-', -1) AS UNSIGNED)) FROM items WHERE company=:c AND id REGEXP CONCAT('^', :c, '-[0-9]+$')", c=prefix) or 0) + 1
    iid = f'{prefix}-{n:04d}'
    row = dict(id=iid, company=d['company'], name=d['name'].strip(), category=d['category'], size=d.get('size'), colour=d.get('colour'),
               gender=d.get('gender'), class_name=d.get('class_name'), term=d.get('term'), academic_year=d.get('academic_year'),
               school_id=d.get('school_id'), hsn=d.get('hsn'), gst_rate=float(d.get('gst_rate') or 0), price=price,
               barcode=d.get('barcode') or None, isbn=clean, isbn13=isbn13, isbn_na_reason=na, supplier_id=d.get('supplier_id'),
               reorder_level=int(d.get('reorder_level') or 0), unpriced=0 if price else 1)
    if row['barcode'] and db.val('SELECT COUNT(*) FROM items WHERE company=:c AND barcode=:b', c=row['company'], b=row['barcode']):
        raise conflict('This barcode is already used.', 'duplicate_barcode')
    db.insert('items', row)
    audit.write('Item created', iid, new=row, category='master', company=row['company'])
    return jsonify({'id': iid}), 201


@handler('mc:price')
def apply_price(p, apr):
    out = []
    for r in p['rows']:
        cur = db.one('SELECT id, name, price FROM items WHERE id=:i FOR UPDATE', i=r['id'])
        if not cur:
            continue
        db.run('UPDATE items SET price=:p, unpriced=0 WHERE id=:i', p=r['price'], i=r['id'])
        audit.write('Price changed', r['id'], old=str(cur['price']), new=str(r['price']), reason=apr['reason'], category='master', status='Approved')
        out.append(r['id'])
    return f'Price changed for {len(out)} item(s)'


@bp.post('/items/price-change')
@require('edit', 'inv')
def price_change():
    d = body()
    rows = d.get('rows') or []
    reason = (d.get('reason') or '').strip()
    if not rows:
        raise bad('No prices given.')
    if not reason:
        raise bad('Please give a reason for the price change.', 'reason_required')
    clean, cos = [], set()
    for r in rows:
        it = db.one('SELECT id, company, name, price FROM items WHERE id=:i', i=r.get('id'))
        if not it or not company_ok(g.user, it['company']):
            raise bad(f'Unknown item {r.get("id")}.')
        try:
            price = round(float(r.get('price')), 2)
        except (TypeError, ValueError):
            raise bad(f'Price for {it["id"]} is not a number.')
        if price <= 0:
            raise bad(f'Price for {it["id"]} must be more than zero.')
        clean.append({'id': it['id'], 'name': it['name'], 'old': float(it['price']), 'price': price})
        cos.add(it['company'])
    payload = {'rows': clean}
    if not mc_on('price'):
        return jsonify({'status': 'Applied', 'result': apply_price(payload, {'reason': reason})})
    res = mc_request('mc', f'Price change · {len(clean)} item(s)', payload, route_role('mc', None, 0, 'CFO'), sub_kind='price',
                     company=cos.pop() if len(cos) == 1 else None, reason=reason,
                     detail='; '.join(f"{r['name']}: ₹{r['old']} → ₹{r['price']}" for r in clean[:6]),
                     signature='price:' + ','.join(f"{r['id']}={r['price']}" for r in clean))
    return jsonify(res), 202


@bp.get('/stock')
@require('view', 'inv')
def stock():
    u = g.user
    br = request.args.get('branch')
    if br and not in_scope(u, br):
        raise denied('This location is outside your access.')
    where, p = scope_sql(u, 's.branch_id')
    sql = f'''SELECT s.branch_id, s.item_id, i.name, i.category, i.size, s.qty, s.reserved, i.reorder_level, i.price
              {', i.cost, ROUND(s.qty*i.cost,2) AS value' if can(u, 'cost', 'inv') else ''}
              FROM stock_balance s JOIN items i ON i.id=s.item_id WHERE {where} AND i.company IN :c'''
    p['c'] = tuple(u['companies']) or ('',)
    if br:
        sql += ' AND s.branch_id=:b'
        p['b'] = br
    qs = (request.args.get('q') or '').strip()
    if qs:
        sql += ' AND (i.name LIKE :q OR i.id LIKE :q OR i.barcode=:e)'
        p['q'], p['e'] = f'%{qs}%', qs
    if request.args.get('low') == '1':
        sql += ' AND i.reorder_level > 0 AND s.qty <= i.reorder_level'
    return jsonify(db.clean(db.q(sql + ' ORDER BY s.branch_id, i.name LIMIT 1000', **p)))
