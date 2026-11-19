"""Discount schemes: pre-approved percentage discounts applied automatically (counter and, if enabled, the parent store).
The best (highest) active scheme that matches the item wins. A cashier's own discount up to the scheme's percentage needs
no approval; anything above it follows the normal discount limits."""
import datetime as dt
from decimal import Decimal

from .. import db


def active_for(company, on=None, online=False):
    on = on or dt.date.today()
    sql = '''SELECT id, name, scope, category, school_id, item_id, pct FROM discount_schemes WHERE company=:c AND active=1
             AND (valid_from IS NULL OR valid_from <= :d) AND (valid_to IS NULL OR valid_to >= :d)'''
    if online:
        sql += ' AND online=1'
    return db.q(sql, c=company, d=on)


def best(item, schemes):
    """item: Item ORM object or dict with id, category, school_id. Returns (pct: Decimal, name | None)."""
    g = (lambda k: getattr(item, k)) if not isinstance(item, dict) else item.get
    top = (Decimal(0), None)
    for s in schemes:
        if s['scope'] == 'category' and s['category'] != g('category'):
            continue
        if s['scope'] == 'school' and s['school_id'] != g('school_id'):
            continue
        if s['scope'] == 'item' and s['item_id'] != g('id'):
            continue
        if Decimal(str(s['pct'])) > top[0]:
            top = (Decimal(str(s['pct'])), s['name'])
    return top
