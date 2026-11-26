"""MSEA sponsorship vouchers.

The MSEA Coordinator raises a voucher for a student (any category, e.g. a Regular student whose parent paid the full fee) (entitlement per category and a maximum value);
it is approved (Accounts Manager or above, by value) and then the counter issues items against it with POS
`voucher_no`. The bill goes to the MSEA receivable (1210) instead of being paid by the parent.
"""
import datetime as dt

from flask import g

from .. import db
from ..core import audit
from ..core.approvals import handler, on_reject, request as approval_request, role_for_limit
from ..core.numbering import next_no
from ..errors import bad, conflict, not_found
from ..models import MseaVoucher
from .common import money, parse_date, today


def create_voucher(d):
    u = g.user
    st = db.one('SELECT * FROM students WHERE adm_no=:a', a=d.get('adm_no'))
    if not st:
        raise not_found('Student not found.', 'unknown_student')
    # Any category may get a voucher: the school gives one when the parent has paid the full tuition fee, so Regular students qualify.
    # (The "sponsored" flag in Settings -> Student categories only decides who cannot buy online.) Only active students.
    if (st.get('status') or 'Active') != 'Active':
        raise conflict(f"{st['name']} is {st['status']} in the ERP; a voucher needs an active student.", 'student_not_active')
    co = d.get('company')
    if co not in u['companies']:
        raise bad('Choose the company (Kiswa for uniforms, Zaibus for books).', 'company_denied')
    ent = {k: int(v) for k, v in (d.get('entitlement') or {}).items() if int(v or 0) > 0}
    if not ent:
        raise bad('Enter the entitlement (items and quantities).', 'no_entitlement')
    max_value = money(d.get('max_value'))
    if max_value <= 0:
        raise bad('Enter the maximum value.', 'bad_amount')
    valid_to = parse_date(d.get('valid_to'), today() + dt.timedelta(days=60), 'Valid to')
    if valid_to < today():
        raise bad('Valid-to date is in the past.', 'bad_date')
    open_v = db.val("SELECT no FROM msea_vouchers WHERE adm_no=:a AND company=:c AND status IN ('Pending approval','Approved','Partly used')",
                    a=st['adm_no'], c=co)
    if open_v:
        raise conflict(f'{st["name"]} already has an open voucher ({open_v}) for this company.', 'duplicate_voucher')
    v = MseaVoucher(no=next_no(co, 'MSV'), adm_no=st['adm_no'], company=co, scope=(d.get('scope') or 'Sponsored issue')[:60], entitlement=ent,
                    used_qty={}, max_value=max_value, used_value=0, valid_to=valid_to, status='Pending approval',
                    remarks=(d.get('remarks') or '')[:300] or None, created_by=u['id'])
    db.add(v)
    role = role_for_limit(u, 'credit', max_value, txn_type='voucher') or 'Accounts Manager'
    a = approval_request('voucher', f'MSEA voucher {v.no} for {st["name"]} ({st["adm_no"]}) up to Rs {max_value}', {'no': v.no}, role,
                         company=co, amount=max_value, reason=v.remarks, ref=v.no)
    audit.write('MSEA voucher raised', v.no, new={'adm_no': v.adm_no, 'entitlement': ent, 'max_value': str(max_value)}, category='msea', company=co)
    return v, a


@handler('voucher')
def _approve(p, apr):
    v = db.session().get(MseaVoucher, p['no'], with_for_update=True)
    if v.status != 'Pending approval':
        raise conflict(f'{v.no} is {v.status}.', 'wrong_status')
    v.status = 'Approved'
    v.approved_by = g.user['name']
    return f'{v.no} approved'


@on_reject('voucher')
def _reject(p, apr, reason):
    v = db.session().get(MseaVoucher, p['no'], with_for_update=True)
    v.status = 'Rejected'


def cancel_voucher(no, reason):
    v = db.session().get(MseaVoucher, no, with_for_update=True)
    if not v:
        raise not_found('Voucher not found.', 'unknown_voucher')
    if money(v.used_value) > 0:
        raise conflict('Items were already issued against this voucher. Return them first.', 'voucher_in_use')
    if not (reason or '').strip():
        raise bad('Give a reason.', 'reason_required')
    v.status = 'Cancelled'
    audit.write('MSEA voucher cancelled', v.no, reason=reason, category='msea', company=v.company)
    return v
