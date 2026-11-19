"""Shared helpers for the business services: money rounding, dates, branches, payment-mode accounts."""
import datetime as dt
from decimal import ROUND_HALF_UP, Decimal

from flask import g, has_request_context

from .. import db
from ..errors import bad, conflict, denied, not_found

TWO = Decimal('0.01')

# Where each payment mode lands in the books (prototype engine.js PAYACCT)
PAY_ACCOUNT = {'cash': '1100', 'upi': '1110', 'card': '1110', 'bank': '1120', 'online': '1115', 'credit': '1200', 'wallet': '2200'}
PAY_MODES = tuple(PAY_ACCOUNT)
HOME_STATE = '36'   # Telangana: intra-state supply -> CGST + SGST, otherwise IGST


def money(x) -> Decimal:
    return Decimal(str(x or 0)).quantize(TWO, ROUND_HALF_UP)


def rupee(x) -> Decimal:
    return Decimal(str(x or 0)).quantize(Decimal('1'), ROUND_HALF_UP)


def today():
    return dt.date.today()


def parse_date(s, default=None, field='date'):
    if not s:
        return default or today()
    try:
        return dt.date.fromisoformat(str(s)[:10])
    except ValueError:
        raise bad(f'{field} must be a date like 2026-11-04.', 'bad_date')


def user_id():
    return g.user['id'] if has_request_context() and g.get('user') else None


def ho_branch(company):
    return f'{company}-HO'


def branch(branch_id, *, must_be_open=True):
    b = db.one('''SELECT b.*, l.name, l.type, l.is_central_wh, l.active AS loc_active, l.closed_on
                  FROM branches b JOIN locations l ON l.code=b.location WHERE b.id=:i''', i=branch_id)
    if not b:
        raise not_found(f'Branch {branch_id} does not exist.', 'unknown_branch')
    if must_be_open and (not b['active'] or not b['loc_active']):
        raise conflict(f"{b['name']} is closed{' since ' + str(b['closed_on']) if b['closed_on'] else ''}. Choose another location.", 'branch_closed')
    return b


def check_scope(user, branch_id):
    from ..core.rbac import in_scope
    if not in_scope(user, branch_id):
        raise denied('This location is outside your access.', 'out_of_scope')


def check_company(user, company):
    if company not in user['companies']:
        raise denied('You do not have access to this company.', 'company_denied')


def check_lock(company, date):
    lock = db.val('SELECT books_locked_to FROM companies WHERE code=:c', c=company)
    if lock and date <= lock:
        name = db.val('SELECT name FROM companies WHERE code=:c', c=company)
        raise conflict(f'{name} books are locked up to {lock}. Use a later date, or ask Head Office to reopen the period.', 'books_locked')


def item(item_id, company=None):
    from ..models import Item
    it = db.session().get(Item, item_id)
    if not it or (company and it.company != company):
        raise not_found(f'Item {item_id} not found.', 'unknown_item')
    return it


def need(value, msg, code='invalid'):
    if not value:
        raise bad(msg, code)
    return value


def whole(v, field, default=None):
    """A whole-number quantity. 2.5 or '2.5' is refused instead of being cut down to 2; booleans and text are refused."""
    if v in (None, ''):
        if default is not None:
            return default
        raise bad(f'{field} is required.', 'bad_quantity')
    if isinstance(v, bool):
        raise bad(f'{field} must be a whole number.', 'bad_quantity')
    try:
        d = Decimal(str(v).strip())
    except Exception:  # noqa: BLE001
        raise bad(f'{field} must be a whole number.', 'bad_quantity') from None
    if not d.is_finite() or d != d.to_integral_value():
        raise bad(f'{field} must be a whole number (no fractions).', 'bad_quantity')
    if abs(d) > 10_000_000:
        raise bad(f'{field} is too large.', 'bad_quantity')
    return int(d)


def positive_int(v, field):
    n = whole(v, field)
    if n <= 0:
        raise bad(f'{field} must be more than zero.', 'bad_quantity')
    return n
