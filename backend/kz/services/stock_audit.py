"""Stock audit: compare system stock with physically counted stock, review and approve the variance, post one adjustment.

Life cycle
    Draft ──start / first count──▶ In Progress ──submit (auditor)──▶ Submitted ──verify (location in-charge)──▶ Under Review
      │                              ▲  ▲                               │ return                          │ review (accounts / inventory)
      └─cancel─▶ Cancelled           │  └──────────── recount ──────────┘                                  ▼
                                     │                                          within reviewer's limit ─▶ Approved
                                     │                                          above limit / high value ─▶ approval request ─▶ Approved | Rejected
                                     └── reopen (Super Admin / Director, reason) ◀── Closed ◀── post adjustment (or no variance)

Rules
* Counting never changes stock. Each counted line captures the system quantity and moving-average cost at that moment.
* Variance quantity = physical - system; value = variance x unit cost. Shortage < 0 (red), excess > 0 (green).
* Auditors cannot verify, review or approve their own audit. Counts are locked once submitted.
* High-value shortage, excess or damaged stock (settings stock_audit.high_value) always goes to the Director and alerts management.
* Posting creates one stock adjustment (series ADJ, type 'Stock audit', audit_no set) whose stock moves and journal
  (5600 shrinkage / 1300 inventory) go through the same code as every other adjustment.
* Submitted, approved and closed audits are never deleted; reopening a closed audit needs 'restore' and a reason.
"""
import csv
import datetime as dt
import io
import json
import random
from decimal import Decimal

from flask import g

from .. import db
from ..core import audit, notify
from ..core.approvals import handler, on_reject, on_return, request as approval_request
from ..core.numbering import next_no
from ..core.rbac import can, in_scope
from ..errors import bad, conflict, denied, not_found
from ..models import StockAdjustment, StockAdjustmentLine, StockAudit, StockAuditEvent, StockAuditLine
from . import stock
from .common import branch, check_company, check_lock, check_scope, item, money, parse_date, today, whole
from .inventory import _post_adjustment

TYPES = {'Full': 'Full stock audit', 'Category': 'Category-wise audit', 'Item': 'Item-wise audit', 'Spot': 'Spot check'}
CONDITIONS = ('Good', 'Damaged', 'Expired', 'Missing', 'Excess', 'Returned')
OPEN = ('Draft', 'In Progress')
PENDING = ('Draft', 'In Progress', 'Submitted', 'Under Review')
LOCATION_TYPE = {'Warehouse': 'Warehouse', 'Godown': 'Godown', 'Store': 'Store', 'Counter': 'Branch', 'Office': 'Branch'}
DEFAULTS = {'high_value': 25000, 'photo_above': 2000, 'due_days': 3, 'spot_size': 20, 'max_lines': 5000,
            # release 2.3: who approves, which items are highlighted, write-off of damaged stock and the ledgers of the automatic journal
            'cfo_upto': 25000, 'hi_item': 5000, 'write_off_damaged': True, 'cost_centre': '',
            'gl': {'short': '5600', 'dmg': '5610', 'excess': '4130', 'inv': '1300'}}
APPROVER_ROLES = ('CFO', 'Director', 'Owner', 'Chairman', 'Super Admin')
DECISIONS = ('Approved', 'Pending', 'Recount', 'Rejected')

# Permission grants for screen 'saudit' (copied into migration 0004 for existing databases; seed.py adds them on fresh installs)
PERMS = {
    'Director': ['view', 'add', 'edit', 'submit', 'verify', 'review', 'approve', 'post', 'restore', 'cancel', 'print', 'pdf', 'export', 'upload', 'fin', 'cost'],
    'Owner': ['view', 'add', 'edit', 'submit', 'verify', 'review', 'approve', 'post', 'restore', 'cancel', 'print', 'pdf', 'export', 'upload', 'fin', 'cost'],
    'Chairman': ['view', 'add', 'verify', 'review', 'approve', 'post', 'restore', 'print', 'pdf', 'export', 'upload', 'fin', 'cost'],
    'Super Admin': ['view', 'restore', 'print', 'pdf', 'export'],
    'CFO': ['view', 'review', 'approve', 'post', 'print', 'pdf', 'export', 'upload', 'fin', 'cost'],
    'Finance Head': ['view', 'review', 'approve', 'post', 'print', 'pdf', 'export', 'upload', 'fin', 'cost'],
    'Accounts Manager': ['view', 'review', 'approve', 'post', 'print', 'pdf', 'export', 'upload', 'fin', 'cost'],
    'Finance': ['view', 'review', 'print', 'pdf', 'export', 'fin', 'cost'],
    'Accountant': ['view', 'print', 'pdf', 'export', 'fin', 'cost'],
    'Finance User': ['view', 'print', 'pdf', 'export', 'fin'],
    'Regional Director': ['view', 'add', 'verify', 'review', 'approve', 'cancel', 'print', 'pdf', 'export', 'upload', 'fin', 'cost'],
    'Zonal Director': ['view', 'add', 'verify', 'review', 'approve', 'cancel', 'print', 'pdf', 'export', 'upload', 'fin', 'cost'],
    'Company Manager': ['view', 'add', 'verify', 'review', 'approve', 'cancel', 'print', 'pdf', 'export', 'upload', 'fin', 'cost'],
    'Inventory Manager': ['view', 'add', 'edit', 'submit', 'review', 'post', 'cancel', 'print', 'pdf', 'export', 'upload', 'fin', 'cost'],
    'Warehouse Manager': ['view', 'add', 'edit', 'submit', 'verify', 'review', 'post', 'cancel', 'print', 'pdf', 'export', 'upload', 'cost'],
    'Branch Manager': ['view', 'add', 'edit', 'submit', 'verify', 'cancel', 'print', 'pdf', 'export', 'upload', 'cost'],
    'Store In-Charge': ['view', 'add', 'edit', 'submit', 'verify', 'cancel', 'print', 'pdf', 'export', 'upload', 'cost'],
    'Branch In-Charge': ['view', 'add', 'edit', 'submit', 'verify', 'cancel', 'print', 'pdf', 'export', 'upload'],
    'Store Manager': ['view', 'add', 'edit', 'submit', 'verify', 'cancel', 'print', 'pdf', 'export', 'upload'],
    'Inventory User': ['view', 'edit', 'submit', 'upload', 'print'],
    'Warehouse Picker': ['view', 'edit', 'submit', 'upload'],
    'Warehouse Quality Checker': ['view', 'edit', 'submit', 'upload'],
    'Sales Executive': ['view', 'edit', 'submit', 'upload'],
    'Store Cashier': ['view', 'edit', 'submit', 'upload'],
    'Auditor': ['view', 'edit', 'submit', 'upload', 'import', 'print', 'pdf', 'export', 'fin', 'cost'],   # counts only when named on the audit
    'Read-only Viewer': ['view'],
}


def cfg():
    from ..core.security import setting
    c = {**DEFAULTS, **(setting('stock_audit', {}) or {})}
    c['gl'] = {**DEFAULTS['gl'], **(c.get('gl') or {})}
    return c


def _uname(uid):
    return db.val('SELECT name FROM users WHERE id=:i', i=uid) if uid else None


def event(a, action, detail=None):
    u = g.get('user') or {}
    db.add(StockAuditEvent(audit_id=a.id, user_id=u.get('id'), user_name=u.get('name') or 'System', action=action,
                           detail=detail if isinstance(detail, str) or detail is None else json.dumps(detail, default=str)))


def tell(user_ids, subject, body, a, alert_type='Stock audit', severity='info'):
    """In-app alert + e-mail (or SMS when no e-mail) to each named user."""
    notify.alert(alert_type, f'{a.no}: {subject}', ref=a.no, company=a.company, branch=a.branch_id, severity=severity)
    for uid in {u for u in user_ids if u}:
        r = db.one("SELECT name, email, mobile FROM users WHERE id=:i AND status='Active'", i=uid)
        if not r:
            continue
        if r['email']:
            notify.queue('email', r['email'], f'Stock audit {a.no}: {subject}', f'{body}\n\nOpen Stock audits → {a.no}.', r['name'], alert_type)
        elif r['mobile']:
            notify.queue('sms', r['mobile'], None, f'Kiswa-Zaibus stock audit {a.no}: {subject}', r['name'], alert_type)


def users_with(action, branch_id, exclude=()):
    """Active users whose roles grant <action> on stock audits and who can see the branch."""
    from ..core.rbac import load_user
    out = []
    for r in db.q("SELECT id FROM users WHERE status='Active'"):
        u = load_user(r['id'])
        if u and r['id'] not in exclude and can(u, action, 'saudit') and in_scope(u, branch_id):
            out.append(r['id'])
    return out


# ---------------------------------------------------------------- create
def _scope_items(company, branch_id, audit_type, scope):
    base = '''SELECT i.id FROM items i LEFT JOIN stock_balance s ON s.item_id=i.id AND s.branch_id=:b
              WHERE i.company=:c AND i.active=1'''
    p = {'b': branch_id, 'c': company}
    if scope.get('bin_prefix'):
        base += ' AND i.bin_location LIKE :bp'
        p['bp'] = scope['bin_prefix'] + '%'
    if audit_type == 'Full':
        ids = [r['id'] for r in db.q(base + ' AND s.item_id IS NOT NULL ORDER BY i.category, i.name, i.size', **p)]
    elif audit_type == 'Category':
        cats = [c for c in scope.get('categories') or [] if c]
        if not cats:
            raise bad('Choose at least one category.', 'no_categories')
        ids = [r['id'] for r in db.q(base + ' AND i.category IN :cats AND (s.qty > 0 OR s.item_id IS NOT NULL) ORDER BY i.category, i.name, i.size',
                                     cats=tuple(cats), **p)]
    elif audit_type == 'Item':
        ids = [i for i in dict.fromkeys(scope.get('items') or []) if i]
        if not ids:
            raise bad('Choose the items to audit.', 'no_items')
        for i in ids:
            item(i, company)
    else:  # Spot
        if scope.get('items'):
            ids = [i for i in dict.fromkeys(scope['items']) if item(i, company)]
        else:
            pool = [r['id'] for r in db.q(base + ' AND s.qty > 0', **p)]
            n = max(1, min(int(scope.get('spot_size') or cfg()['spot_size']), len(pool) or 1))
            ids = sorted(random.SystemRandom().sample(pool, n)) if pool else []
    if not ids:
        raise bad('No items match this audit at that location.', 'no_items')
    if len(ids) > cfg()['max_lines']:
        raise bad(f'{len(ids)} items is more than one audit can hold ({cfg()["max_lines"]}). Split it by category.', 'too_many_lines')
    return ids


def create(d):
    u = g.user
    b = branch(d.get('branch_id'))
    check_company(u, b['company'])
    check_scope(u, b['id'])
    atype = d.get('audit_type')
    if atype not in TYPES:
        raise bad('Choose the audit type: Full, Category, Item or Spot.', 'invalid')
    adate = parse_date(d.get('audit_date'), today(), 'Audit date')
    auditors = [a for a in dict.fromkeys(d.get('auditors') or []) if a]
    if not auditors:
        raise bad('Name at least one auditor.', 'no_auditors')
    sup = d.get('supervisor_id')
    if not sup:
        raise bad('Choose the audit supervisor (location in-charge who verifies the count).', 'no_supervisor')
    if sup in auditors:
        raise bad('The supervisor cannot also be an auditor of the same audit.', 'segregation')
    from ..core.rbac import load_user
    for uid in auditors:
        au = load_user(uid)
        if not au or au['status'] != 'Active' or not can(au, 'edit', 'saudit') or not in_scope(au, b['id']):
            raise bad(f'{(au or {}).get("name", uid)} cannot count stock at {b["name"]} (needs the stock-audit count permission and that location).', 'bad_auditor')
    su = load_user(sup)
    if not su or su['status'] != 'Active' or not can(su, 'verify', 'saudit'):
        raise bad('The supervisor must be an active user allowed to verify stock audits.', 'bad_supervisor')
    scope = {k: d.get(k) for k in ('categories', 'items', 'spot_size', 'bin_prefix') if d.get(k)}
    ids = _scope_items(b['company'], b['id'], atype, scope)
    due = parse_date(d['due_date'], None, 'Due date') if d.get('due_date') else adate + dt.timedelta(days=int(cfg()['due_days']))
    if due < adate:
        raise bad('The due date is before the audit date.', 'bad_date')
    a = StockAudit(no=next_no(b['company'], 'SA', adate), company=b['company'], branch_id=b['id'], location=b['location'],
                   location_type=LOCATION_TYPE.get(b['type'], 'Branch'), audit_date=adate, due_date=due, audit_type=atype, scope=scope or None,
                   stock_area=str(d.get('stock_area') or '').strip()[:60] or None, auditors=auditors, supervisor_id=sup,
                   instructions=str(d.get('instructions') or '').strip()[:1000] or None, status='Draft', created_by=u['id'], lines_count=len(ids))
    a.lines = [StockAuditLine(line_no=n, item_id=i, condition='Good') for n, i in enumerate(ids, 1)]
    db.add(a)
    event(a, 'Created', {'type': TYPES[atype], 'lines': len(ids), 'auditors': [_uname(x) for x in auditors], 'supervisor': su['name']})
    audit.write('Stock audit created', a.no, new={'type': atype, 'location': b['id'], 'date': str(adate), 'lines': len(ids)}, category='stock',
                company=a.company, branch=a.branch_id)
    tell(auditors, f'assigned to you — {TYPES[atype]} at {b["name"]} on {adate:%d %b}',
         f'You are an auditor for {a.no} ({len(ids)} items) at {b["name"]}. Due {due:%d %b %Y}.\n{a.instructions or ""}', a, 'Stock audit assigned')
    tell([sup], f'you supervise {a.no} at {b["name"]}', f'You will verify the physical count of {a.no}.', a, 'Stock audit assigned')
    return a


# ---------------------------------------------------------------- read
def get(aid, lock=False):
    a = db.session().get(StockAudit, aid, with_for_update=lock)
    if not a:
        raise not_found('Stock audit not found.', 'unknown_audit')
    check_company(g.user, a.company)
    check_scope(g.user, a.branch_id)
    return a


def by_no(no):
    aid = db.val('SELECT id FROM stock_audits WHERE no=:n', n=no)
    if not aid:
        raise not_found('Stock audit not found.', 'unknown_audit')
    return get(aid)


def dmg_qty(ln):
    """Damaged / expired part of the physical count (all of it unless the auditor entered a smaller figure)."""
    if ln.condition not in ('Damaged', 'Expired') or ln.physical_qty is None:
        return 0
    return max(0, min(ln.physical_qty, ln.dmg_qty if ln.dmg_qty is not None else ln.physical_qty))


def needs_decision(ln):
    return not ln.decision and ln.physical_qty is not None and (ln.physical_qty != (ln.system_qty or 0) or dmg_qty(ln) > 0)


def line_view(a, ln, it, live, ev):
    cost = Decimal(str(ln.unit_cost if ln.unit_cost is not None else it['cost'] or 0))
    counted = ln.physical_qty is not None
    var = (ln.physical_qty - ln.system_qty) if counted and ln.system_qty is not None else None
    return {'line_no': ln.line_no, 'item_id': ln.item_id, 'name': it['name'], 'category': it['category'], 'sub': ' · '.join(x for x in (it['class_name'], it['size']) if x),
            'uom': it['uom'] or 'Nos', 'barcode': it['barcode'], 'bin': it['bin_location'], 'batch_no': ln.batch_no,
            'live_qty': live, 'system_qty': ln.system_qty, 'physical_qty': ln.physical_qty, 'variance_qty': var, 'unit_cost': float(cost),
            'system_value': float(money((ln.system_qty or 0) * cost)) if counted else None, 'physical_value': float(money(ln.physical_qty * cost)) if counted else None,
            'variance_value': float(money(var * cost)) if var is not None else None, 'condition': ln.condition, 'remarks': ln.remarks,
            'added_in_count': ln.added_in_count, 'counted_by': ln.counted_by, 'counted_at': ln.counted_at.isoformat(timespec='seconds') if ln.counted_at else None,
            'evidence': ev.get(f'{a.no}/{ln.item_id}', 0), 'dmg_qty': dmg_qty(ln), 'dmg_value': float(money(dmg_qty(ln) * cost)) if counted else None,
            'decision': ln.decision, 'decided_by': ln.decided_by, 'recount': bool(ln.recount), 'adjustment_no': ln.adjustment_no,
            'status': None if not counted else 'Matched' if var == 0 else 'Shortage' if var < 0 else 'Excess'}


def detail(a):
    u = g.user
    ids = [ln.item_id for ln in a.lines] or ['']
    items = {r['id']: r for r in db.q('SELECT id, name, category, class_name, size, uom, barcode, bin_location, cost FROM items WHERE id IN :i', i=tuple(ids))}
    live = {r['item_id']: r['qty'] for r in db.q('SELECT item_id, qty FROM stock_balance WHERE branch_id=:b', b=a.branch_id)}
    ev = {r['ref']: r['n'] for r in db.q("SELECT ref, COUNT(*) n FROM evidence WHERE ref LIKE :p AND status='Active' GROUP BY ref", p=a.no + '%')}
    lines = [line_view(a, ln, items.get(ln.item_id, {'name': ln.item_id, 'category': '', 'class_name': None, 'size': None, 'uom': 'Nos', 'barcode': None,
                                                     'bin_location': None, 'cost': 0}), live.get(ln.item_id, 0), ev) for ln in a.lines]
    show_cost = can(u, 'cost', 'saudit') or can(u, 'fin', 'saudit')
    if not show_cost:
        for ln in lines:
            for k in ('unit_cost', 'system_value', 'physical_value', 'variance_value'):
                ln[k] = None
    names = {r['id']: r['name'] for r in db.q('SELECT id, name FROM users WHERE id IN :i', i=tuple(set(a.auditors) | {a.supervisor_id, a.created_by,
                                                                                                                    a.verified_by or '', a.reviewed_by or '', a.approved_by or '', a.posted_by or '', a.submitted_by or ''}))}
    out = a.as_dict()
    for k in ('system_value', 'physical_value', 'shortage_value', 'excess_value', 'damaged_value'):
        out[k] = float(out[k] or 0) if show_cost else None
    b = db.one('SELECT l.name FROM branches b JOIN locations l ON l.code=b.location WHERE b.id=:b', b=a.branch_id)
    me = u['id']
    out.update({
        'type_label': TYPES[a.audit_type], 'location_name': b['name'] if b else a.location, 'company_name': db.val('SELECT name FROM companies WHERE code=:c', c=a.company),
        'auditor_names': [names.get(x, x) for x in a.auditors], 'supervisor_name': names.get(a.supervisor_id), 'created_by_name': names.get(a.created_by),
        'submitted_by_name': names.get(a.submitted_by), 'verified_by_name': names.get(a.verified_by), 'reviewed_by_name': names.get(a.reviewed_by),
        'approved_by_name': names.get(a.approved_by), 'posted_by_name': names.get(a.posted_by), 'lines': lines,
        'accuracy_pct': accuracy(a), 'overdue': a.status in PENDING and a.due_date < today(), 'show_cost': show_cost,
        'events': db.clean(db.q('SELECT at, user_name, action, detail FROM stock_audit_events WHERE audit_id=:i ORDER BY id', i=a.id)),
        'approval': db.clean(db.one('''SELECT p.no, p.status, p.required_role, u.name AS decided_by_name, p.decided_at FROM approvals p
                                        LEFT JOIN users u ON u.id=p.decided_by WHERE p.no=:n''', n=a.approval_no)) if a.approval_no else None,
        'adjustment': db.clean(db.one('SELECT no, value, journal_no, status, created_at FROM stock_adjustments WHERE no=:n', n=a.adjustment_no)) if a.adjustment_no else None,
        'evidence_count': sum(ev.values()),
        'can': {
            'count': a.status in OPEN and me in a.auditors and can(u, 'edit', 'saudit'),
            'submit': a.status in OPEN and me in a.auditors and can(u, 'submit', 'saudit'),
            'verify': a.status == 'Submitted' and me not in a.auditors and (me == a.supervisor_id or (can(u, 'verify', 'saudit') and float(u.get('rank') or 0) >= 2)),
            'review': a.status == 'Under Review' and not a.approval_no and me not in a.auditors and can(u, 'review', 'saudit'),
            'post': a.status == 'Approved' and can(u, 'post', 'saudit'),
            'reopen': a.status in ('Closed', 'Rejected') and can(u, 'restore', 'saudit'),
            'cancel': a.status in OPEN and (me == a.created_by or can(u, 'cancel', 'saudit')),
            'decide': False,
            'add_auditor': a.status in OPEN and (me == a.created_by or can(u, 'add', 'saudit')),
        },
    })
    nx = next_step(a, u)
    out['next_step'] = nx
    out['can']['decide'] = bool(nx and nx['step'].startswith('Approve') and nx['mine'])
    out['decisions'] = db.clean(db.q('SELECT d.id, d.mode, d.decided_role, d.decided_at, d.remarks, d.adjustment_no, d.journal_no, d.shortage, d.damaged, d.excess, '
                                     'd.net, u.name AS decided_by_name FROM stock_audit_decisions d LEFT JOIN users u ON u.id=d.decided_by WHERE d.audit_id=:i ORDER BY d.id',
                                     i=a.id))
    if not show_cost:
        for x in out['decisions']:
            for k in ('shortage', 'damaged', 'excess', 'net'):
                x[k] = None
    return out


def accuracy(a):
    return round(100 * a.matched_count / a.counted_count, 1) if a.counted_count else None


def recompute(a):
    sv = pv = sh = ex = dm = Decimal(0)
    counted = matched = 0
    for ln in a.lines:
        if ln.physical_qty is None:
            continue
        counted += 1
        c = Decimal(str(ln.unit_cost or 0))
        var = ln.physical_qty - (ln.system_qty or 0)
        sv += (ln.system_qty or 0) * c
        pv += ln.physical_qty * c
        if var < 0:
            sh += -var * c
        elif var > 0:
            ex += var * c
        elif not dmg_qty(ln):
            matched += 1
        dm += dmg_qty(ln) * c
    a.lines_count, a.counted_count, a.matched_count = len(a.lines), counted, matched
    a.system_value, a.physical_value, a.shortage_value, a.excess_value, a.damaged_value = (money(x) for x in (sv, pv, sh, ex, dm))


# ---------------------------------------------------------------- counting
def _need_counter(a):
    if a.status not in OPEN:
        raise conflict(f'{a.no} is {a.status}: counts are locked.', 'audit_locked')
    if g.user['id'] not in a.auditors or not can(g.user, 'edit', 'saudit'):
        raise denied('Only the auditors named on this audit can enter counts.', 'not_auditor')


def start(aid):
    a = get(aid, lock=True)
    _need_counter(a)
    if a.status == 'Draft':
        a.status, a.started_at = 'In Progress', dt.datetime.now()
        event(a, 'Count started')
        audit.write('Stock audit started', a.no, old='Draft', new='In Progress', category='stock', company=a.company, branch=a.branch_id)
    return a


def save_counts(aid, rows, mode='set'):
    """rows: [{line_no | item_id | code, physical_qty, condition?, remarks?, batch_no?}]. mode 'add' adds to the count (scanning)."""
    a = get(aid, lock=True)
    _need_counter(a)
    if a.status == 'Draft':
        a.status, a.started_at = 'In Progress', dt.datetime.now()
        event(a, 'Count started')
    by_line = {ln.line_no: ln for ln in a.lines}
    by_item = {ln.item_id: ln for ln in a.lines}
    now, changed, added = dt.datetime.now(), [], []
    for r in rows or []:
        ln = by_line.get(int(r['line_no'])) if r.get('line_no') else None
        code = (r.get('item_id') or r.get('code') or '').strip()
        if not ln and code:
            iid = code if db.val('SELECT COUNT(*) FROM items WHERE id=:c AND company=:co', c=code, co=a.company) else \
                db.val('SELECT id FROM items WHERE company=:co AND (barcode=:c OR isbn13=:c OR isbn=:c) LIMIT 1', c=code, co=a.company)
            if not iid:
                raise not_found(f'No {a.company} item has the code or barcode {code}.', 'unknown_item')
            ln = by_item.get(iid)
            if not ln:   # found on the shelf but not in the planned list: add it (an excess or a missed item)
                if len(a.lines) >= cfg()['max_lines']:
                    raise conflict('This audit is full. Start another audit for the remaining items.', 'too_many_lines')
                ln = StockAuditLine(line_no=max(by_line, default=0) + 1, item_id=iid, condition='Good', added_in_count=True)
                a.lines.append(ln)
                by_line[ln.line_no], by_item[iid] = ln, ln
                added.append(iid)
        if not ln:
            raise bad('Each count row needs the line number, item code or barcode.', 'invalid')
        if ln.decision in ('Approved', 'Rejected'):
            raise conflict(f'{ln.item_id} was {ln.decision.lower()} by {_uname(ln.decided_by)}; it can no longer change.', 'line_decided')
        if ln.decision == 'Matched':
            ln.decision = None
        if 'physical_qty' in r or mode == 'add':
            q = r.get('physical_qty', 1 if mode == 'add' else None)
            if q in (None, ''):
                ln.physical_qty = ln.system_qty = ln.unit_cost = ln.counted_by = ln.counted_at = None
            else:
                q = whole(q, f'Quantity for {ln.item_id}')
                if mode == 'add':
                    q = (ln.physical_qty or 0) + q
                if q < 0:
                    raise bad('A physical count cannot be negative.', 'bad_quantity')
                if q > 1_000_000:
                    raise bad(f'{q} looks wrong for {ln.item_id}. Check the count.', 'bad_quantity')
                it = item(ln.item_id, a.company)
                ln.physical_qty = q
                ln.system_qty, _ = stock.on_hand(a.branch_id, ln.item_id)      # system stock at the moment of counting
                ln.unit_cost = it.cost
                ln.counted_by, ln.counted_at = g.user['id'], now
        if r.get('condition'):
            if r['condition'] not in CONDITIONS:
                raise bad(f'Condition must be one of {", ".join(CONDITIONS)}.', 'invalid')
            ln.condition = r['condition']
        for k, n in (('remarks', 300), ('batch_no', 40)):
            if k in r:
                ln.__setattr__(k, (str(r[k] or '').strip()[:n]) or None)
        if 'dmg_qty' in r:
            v = r['dmg_qty']
            if v in (None, ''):
                ln.dmg_qty = None
            else:
                v = whole(v, 'Damaged quantity')
                if v < 0 or (ln.physical_qty is not None and v > ln.physical_qty):
                    raise bad('Damaged / expired quantity must be between 0 and the counted quantity.', 'bad_quantity')
                ln.dmg_qty = v
        ln.recount = False                       # a recount request is answered by counting the item again
        changed.append(ln.line_no)
    db.session().flush()
    recompute(a)
    if changed:
        event(a, 'Count saved', {'lines': len(set(changed)), 'added': added or None, 'mode': mode})
        audit.write('Stock audit count saved', a.no, new={'lines': len(set(changed)), 'items_added': added}, category='stock', company=a.company, branch=a.branch_id)
    return a


def import_sheet(aid, filename, data):
    """Count sheet upload: CSV or XLSX with columns item_code (or barcode), physical_qty, condition, remarks, batch_no."""
    name = (filename or '').lower()
    rows = []
    if name.endswith('.xlsx'):
        from openpyxl import load_workbook
        ws = load_workbook(io.BytesIO(data), read_only=True, data_only=True).active
        it = ws.iter_rows(values_only=True)
        head = [str(h or '').strip().lower() for h in next(it, [])]
        rows = [dict(zip(head, r)) for r in it]
    elif name.endswith('.csv') or name.endswith('.txt'):
        text = data.decode('utf-8-sig', errors='replace')
        rows = [{(k or '').strip().lower(): v for k, v in r.items()} for r in csv.DictReader(io.StringIO(text))]
    else:
        raise bad('Upload the count sheet as .xlsx or .csv.', 'bad_file')
    out = []
    for r in rows:
        code = str(r.get('item_code') or r.get('item') or r.get('barcode') or r.get('code') or '').strip()
        qty = r.get('physical_qty', r.get('physical', r.get('qty')))
        if not code and qty in (None, ''):
            continue
        row = {'code': code}
        if qty not in (None, ''):
            row['physical_qty'] = qty
        for k in ('condition', 'remarks', 'batch_no'):
            if r.get(k) not in (None, ''):
                row[k] = str(r[k]).strip().capitalize() if k == 'condition' else str(r[k]).strip()
        out.append(row)
    if not out:
        raise bad('The sheet has no rows. Use the downloaded count sheet as the template.', 'empty_file')
    a = save_counts(aid, out)
    event(a, 'Count sheet uploaded', {'file': filename, 'rows': len(out)})
    return a, len(out)


# ---------------------------------------------------------------- workflow
def submit(aid):
    a = get(aid, lock=True)
    if a.status not in OPEN:
        raise conflict(f'{a.no} is {a.status}.', 'wrong_status')
    if g.user['id'] not in a.auditors or not can(g.user, 'submit', 'saudit'):
        raise denied('Only an auditor of this audit can submit it.', 'not_auditor')
    missing = [ln.item_id for ln in a.lines if ln.physical_qty is None]
    if missing:
        raise conflict(f'{len(missing)} item(s) are not counted yet (e.g. {", ".join(missing[:3])}). Enter 0 for items that are not on the shelf.',
                       'count_incomplete')
    need = []
    lim = Decimal(str(cfg()['photo_above']))
    ev = {r['ref'] for r in db.q("SELECT DISTINCT ref FROM evidence WHERE ref LIKE :p AND status='Active'", p=a.no + '/%')}
    for ln in a.lines:
        var_val = abs((ln.physical_qty - (ln.system_qty or 0)) * Decimal(str(ln.unit_cost or 0)))
        if (ln.condition in ('Damaged', 'Expired', 'Missing') or var_val >= lim) and f'{a.no}/{ln.item_id}' not in ev:
            need.append(ln.item_id)
    if need:
        raise conflict(f'Attach a photo or document for {len(need)} line(s) — damaged / expired / missing items and variances of '
                       f'Rs {lim:,.0f} or more (e.g. {", ".join(need[:3])}).', 'evidence_required', items=need)
    recompute(a)
    a.status, a.submitted_by, a.submitted_at = 'Submitted', g.user['id'], dt.datetime.now()
    event(a, 'Submitted', _totals(a))
    audit.write('Stock audit submitted', a.no, old='In Progress', new=_totals(a), category='stock', company=a.company, branch=a.branch_id)
    tell([a.supervisor_id], 'submitted — please verify the physical count', f'{a.no} was submitted by {g.user["name"]}. {_summary(a)}', a, 'Stock audit submitted')
    return a


def _totals(a):
    return {'counted': a.counted_count, 'matched': a.matched_count, 'shortage': float(a.shortage_value), 'excess': float(a.excess_value),
            'damaged': float(a.damaged_value)}


def _summary(a):
    return (f'{a.counted_count} items counted, {a.matched_count} matched. Shortage Rs {a.shortage_value:,.2f}, excess Rs {a.excess_value:,.2f}, '
            f'damaged / expired Rs {a.damaged_value:,.2f}.')


def verify(aid, ok, note):
    a = get(aid, lock=True)
    u = g.user
    if a.status != 'Submitted':
        raise conflict(f'{a.no} is {a.status}.', 'wrong_status')
    if u['id'] in a.auditors:
        raise denied('Auditors cannot verify their own audit.', 'segregation')
    if not (u['id'] == a.supervisor_id or (can(u, 'verify', 'saudit') and float(u.get('rank') or 0) >= 2)):
        raise denied(f'Only the audit supervisor ({_uname(a.supervisor_id)}) or a manager can verify this count.', 'not_supervisor')
    note = (note or '').strip()
    if ok:
        a.status, a.verified_by, a.verified_at = 'Under Review', u['id'], dt.datetime.now()
        event(a, 'Verified by location in-charge', note or None)
        audit.write('Stock audit verified', a.no, old='Submitted', new='Under Review', reason=note or None, category='stock', company=a.company, branch=a.branch_id)
        tell(users_with('review', a.branch_id, exclude=a.auditors), 'verified — review the variance', f'{a.no}: {_summary(a)}', a, 'Stock audit for review')
    else:
        if not note:
            raise bad('Say what must be recounted.', 'reason_required')
        a.status = 'In Progress'
        event(a, 'Returned for recount', note)
        audit.write('Stock audit returned for recount', a.no, old='Submitted', new='In Progress', reason=note, category='stock', company=a.company, branch=a.branch_id)
        tell(a.auditors, 'returned for recount', f'{u["name"]}: {note}', a, 'Stock audit returned')
    return a


def review(aid, action, note):
    """action: approve (send for final approval, or approve within the reviewer's limit) | return (recount) | reject."""
    a = get(aid, lock=True)
    u = g.user
    if a.status != 'Under Review' or a.approval_no:
        raise conflict(f'{a.no} is {a.status}{" and waiting for approval " + a.approval_no if a.approval_no else ""}.', 'wrong_status')
    if u['id'] in a.auditors:
        raise denied('Auditors cannot review their own audit.', 'segregation')
    if not can(u, 'review', 'saudit'):
        raise denied()
    note = (note or '').strip()
    a.reviewed_by, a.reviewed_at, a.review_note = u['id'], dt.datetime.now(), note or None
    if action == 'return':
        if not note:
            raise bad('Say what must be recounted.', 'reason_required')
        a.status = 'In Progress'
        event(a, 'Returned for recount by reviewer', note)
        tell(a.auditors, 'returned for recount by accounts / inventory', note, a, 'Stock audit returned')
        return a, None
    if action == 'reject':
        if not note:
            raise bad('Give the reason for rejecting.', 'reason_required')
        a.status, a.rejected_reason = 'Rejected', note
        event(a, 'Rejected by reviewer', note)
        audit.write('Stock audit rejected', a.no, old='Under Review', new='Rejected', reason=note, category='stock', company=a.company, branch=a.branch_id)
        tell(a.auditors + [a.supervisor_id, a.created_by], 'rejected', note, a, 'Stock audit rejected', 'warn')
        return a, None
    if action != 'approve':
        raise bad('Action must be approve, return or reject.', 'invalid')
    for ln in a.lines:
        if not ln.decision and ln.physical_qty is not None and not needs_decision(ln):
            ln.decision = 'Matched'
    cyc = [ln.line_no for ln in a.lines if needs_decision(ln)]
    event(a, 'Reviewed', {'note': note or None, 'items_to_decide': len(cyc)})
    if not cyc:
        now = dt.datetime.now()
        a.status, a.closed_at = 'Closed', now
        event(a, 'Closed (no variance)', 'Every counted item matches the system; nothing to adjust')
        audit.write('Stock audit closed without variance', a.no, old='Under Review', new='Closed', category='stock', company=a.company, branch=a.branch_id)
        return a, None
    ap = request_decision(a, cyc, note)
    return a, ap


def cycle_totals(a, line_nos):
    sh = ex = dm = Decimal(0)
    sq = eq = 0
    for ln in a.lines:
        if ln.line_no not in line_nos:
            continue
        c = Decimal(str(ln.unit_cost or 0))
        var = ln.physical_qty - (ln.system_qty or 0)
        if var < 0:
            sh, sq = sh - var * c, sq - var
        elif var > 0:
            ex, eq = ex + var * c, eq + var
        dm += dmg_qty(ln) * c
    return {'shortage': money(sh), 'excess': money(ex), 'damaged': money(dm), 'short_qty': sq, 'excess_qty': eq}


def approver_for(t):
    c = cfg()
    gross = t['shortage'] + t['excess'] + t['damaged']
    high = max(t['shortage'], t['excess'], t['damaged']) >= Decimal(str(c['high_value']))
    return ('Director' if high or gross > Decimal(str(c['cfo_upto'])) else 'CFO'), high


def request_decision(a, line_nos, note):
    """Send the undecided items to the Director / CFO. Stock does not change until they approve."""
    t = cycle_totals(a, line_nos)
    role, high = approver_for(t)
    gross = money(t['shortage'] + t['excess'] + t['damaged'])
    if high:
        a.escalated = True
        tell(users_with('approve', a.branch_id, exclude=a.auditors),
             f"HIGH-VALUE variance — shortage Rs {t['shortage']:,.0f}, excess Rs {t['excess']:,.0f}, damaged Rs {t['damaged']:,.0f}: Director approval required",
             _summary(a), a, 'High stock variance', 'bad')
    detail = (f"{len(line_nos)} item(s): shortage -{t['short_qty']} units Rs {t['shortage']:,.2f}, excess +{t['excess_qty']} units Rs {t['excess']:,.2f}, "
              f"damaged / expired Rs {t['damaged']:,.2f}, net Rs {t['excess'] - t['shortage'] - t['damaged']:,.2f}. Open the impact review to decide.")
    ap = approval_request('saudit', f'Stock audit {a.no} at {a.branch_id}: {len(line_nos)} item(s) to decide', {'id': a.id, 'no': a.no, 'lines': line_nos}, role,
                          company=a.company, branch=a.branch_id, amount=gross, reason=note or f'Stock audit {a.no}', ref=a.no, detail=detail)
    a.approval_no, a.status = ap['no'], 'Under Review'
    event(a, f'Sent to {role} for approval'[:40], {'approval': ap['no'], 'items': len(line_nos), **{k: float(v) if isinstance(v, Decimal) else v for k, v in t.items()}})
    audit.write('Stock audit sent for approval', a.no, new=f"{ap['no']} → {role}", reason=note or None, category='stock', company=a.company, branch=a.branch_id)
    return ap


def _approver_ok(u):
    return any(r in APPROVER_ROLES for r in u['roles'])


def _post_lines(a, lines, u):
    """Post the approved items: item-wise stock moves (variance, then damaged / expired write-off), one stock adjustment and one
    journal with the configured ledgers. Returns (adjustment, journal_no, totals, snapshot)."""
    from . import ledger
    c = cfg()
    gl, cc = c['gl'], c.get('cost_centre') or None
    check_lock(a.company, today())
    for ln in lines:
        var = ln.physical_qty - (ln.system_qty or 0)
        dq = dmg_qty(ln) if c['write_off_damaged'] else 0
        on_hand, _ = stock.on_hand(a.branch_id, ln.item_id)
        if on_hand + var - dq < 0:
            raise conflict(f'{ln.item_id}: only {on_hand} in stock now; the approved change of {var - dq} would make it negative. Stock was sold or '
                           f'moved after the count — send this item for recount.', 'negative_stock')
    adj = StockAdjustment(no=next_no(a.company, 'ADJ'), company=a.company, branch_id=a.branch_id, txn_date=today(), adj_type='Stock audit',
                          reason=f'Stock audit {a.no} ({TYPES[a.audit_type]}, {a.audit_date:%d-%m-%Y}) approved by {u["name"]}', status='Approved',
                          created_by=u['id'], value=0, audit_no=a.no, approved_by=u['id'])
    sh = ex = dm = Decimal(0)
    snap, n = [], 0
    names = {r['id']: r['name'] for r in db.q('SELECT id, name FROM items WHERE id IN :i', i=tuple(ln.item_id for ln in lines))}
    for ln in lines:
        cost = Decimal(str(ln.unit_cost or 0))
        var = ln.physical_qty - (ln.system_qty or 0)
        dq = dmg_qty(ln) if c['write_off_damaged'] else 0
        before, _ = stock.on_hand(a.branch_id, ln.item_id)
        if var:
            n += 1
            adj.lines.append(StockAdjustmentLine(line_no=n, item_id=ln.item_id, qty=var, unit_cost=cost))
            stock.move(a.company, a.branch_id, ln.item_id, var, 'Adjustment · Stock audit', adj.no, today(), cost)
            if var < 0:
                sh += -var * cost
            else:
                ex += var * cost
        if dq:
            n += 1
            adj.lines.append(StockAdjustmentLine(line_no=n, item_id=ln.item_id, qty=-dq, unit_cost=cost))
            stock.move(a.company, a.branch_id, ln.item_id, -dq, f'Write-off · {ln.condition.lower()}', adj.no, today(), cost)
            dm += dq * cost
        after, _ = stock.on_hand(a.branch_id, ln.item_id)
        snap.append({'line_no': ln.line_no, 'item_id': ln.item_id, 'name': names.get(ln.item_id, ln.item_id), 'cost': float(cost), 'sys': ln.system_qty or 0,
                     'sys_value': float(money((ln.system_qty or 0) * cost)), 'phys': ln.physical_qty, 'phys_value': float(money(ln.physical_qty * cost)),
                     'var': var, 'var_value': float(money(var * cost)), 'dmg': dq, 'dmg_value': float(money(dq * cost)), 'condition': ln.condition,
                     'before': before, 'after': after})
    sh, ex, dm = money(sh), money(ex), money(dm)
    adj.value = money(sh + ex + dm)
    db.add(adj)
    jl = []
    if sh:
        jl += [{'account': gl['short'], 'branch': a.branch_id, 'debit': sh, 'cost_centre': cc}, {'account': gl['inv'], 'branch': a.branch_id, 'credit': sh, 'cost_centre': cc}]
    if dm:
        jl += [{'account': gl['dmg'], 'branch': a.branch_id, 'debit': dm, 'cost_centre': cc}, {'account': gl['inv'], 'branch': a.branch_id, 'credit': dm, 'cost_centre': cc}]
    if ex:
        jl += [{'account': gl['inv'], 'branch': a.branch_id, 'debit': ex, 'cost_centre': cc}, {'account': gl['excess'], 'branch': a.branch_id, 'credit': ex, 'cost_centre': cc}]
    j = ledger.post(a.company, today(), 'Stock adjustment', adj.no, f'Stock audit {a.no} at {a.branch_id} · approved by {u["name"]} ({u["main_role"]}) · '
                    f'shortage {sh}, damaged {dm}, excess {ex}', jl, skip_if_empty=True)
    adj.status, adj.journal_no = 'Posted', j.no if j else None
    audit.write('Stock adjustment posted', adj.no, new=f'{len(lines)} item(s) from {a.no}, Rs {adj.value}', reason=adj.reason, category='inventory',
                company=a.company, branch=a.branch_id)
    return adj, (j.no if j else None), {'shortage': sh, 'excess': ex, 'damaged': dm, 'net': money(ex - sh - dm)}, snap


def _record(a, apr, mode, u, remarks, adj=None, jno=None, tot=None, snap=None, rejected=(), recount=(), held=()):
    from ..models import StockAuditDecision
    nm = lambda nos: [{'line_no': ln.line_no, 'item_id': ln.item_id} for ln in a.lines if ln.line_no in nos]  # noqa: E731
    tot = tot or {}
    db.add(StockAuditDecision(audit_id=a.id, approval_no=(apr or {}).get('no'), mode=mode, decided_by=u['id'], decided_role=u['main_role'],
                              decided_at=dt.datetime.now(), remarks=(remarks or '')[:500] or None, adjustment_no=adj.no if adj else None, journal_no=jno,
                              shortage=tot.get('shortage', 0), damaged=tot.get('damaged', 0), excess=tot.get('excess', 0), net=tot.get('net', 0),
                              approved=snap or [], rejected=nm(rejected), recount=nm(recount), held=nm(held)))


@handler('saudit')
def _approve_handler(p, apr):
    """Director / CFO approval. p['sel'] = {line_no: Approved | Pending | Recount | Rejected}; without it every item in the cycle is approved."""
    u = g.user
    a = db.session().get(StockAudit, p['id'], with_for_update=True)
    if a.status != 'Under Review':
        raise conflict(f'{a.no} is {a.status}.', 'wrong_status')
    if u['id'] in a.auditors:
        raise denied('Auditors cannot approve their own audit.', 'segregation')
    if not _approver_ok(u):
        raise denied('Stock adjustments are approved only by the CFO or Director.', 'not_approver')
    by_no = {ln.line_no: ln for ln in a.lines}
    cyc = [n for n in (p.get('lines') or [ln.line_no for ln in a.lines if needs_decision(ln)]) if n in by_no and not by_no[n].decision]
    sel = {int(k): v for k, v in (p.get('sel') or {}).items()} or {n: 'Approved' for n in cyc}
    remarks = p.get('remarks') or ''
    ok_ = [n for n in cyc if sel.get(n, 'Pending') == 'Approved']
    rec = [n for n in cyc if sel.get(n) == 'Recount']
    rej = [n for n in cyc if sel.get(n) == 'Rejected']
    hold = [n for n in cyc if n not in ok_ + rec + rej]
    if not (ok_ or rec or rej):
        raise bad('Choose at least one item to approve, recount or reject.', 'nothing_decided')
    now = dt.datetime.now()
    adj = jno = None
    tot, snap = {}, []
    if ok_:
        adj, jno, tot, snap = _post_lines(a, [by_no[n] for n in ok_], u)
    for n in ok_:
        by_no[n].decision, by_no[n].decided_by, by_no[n].decided_at, by_no[n].adjustment_no, by_no[n].recount = 'Approved', u['id'], now, adj.no, False
    for n in rej:
        by_no[n].decision, by_no[n].decided_by, by_no[n].decided_at, by_no[n].recount = 'Rejected', u['id'], now, False
    for n in rec:
        by_no[n].recount = True
    mode = 'Full approval' if len(ok_) == len(cyc) else 'Partial approval'
    _record(a, apr, mode, u, remarks, adj, jno, tot, snap, rej, rec, hold)
    a.approved_by, a.approved_at = u['id'], now
    if adj:
        a.adjustment_no = adj.no if not a.adjustment_no else (a.adjustment_no + ', ' + adj.no)[:30]
    a.approval_no = None
    event(a, f'{mode} by {u["main_role"]}'[:40], {'approved': len(ok_), 'adjustment': adj.no if adj else None, 'journal': jno, 'rejected': len(rej),
                                             'recount': len(rec), 'held': len(hold), 'remarks': remarks or None})
    if adj:
        tell(a.auditors + [a.supervisor_id, a.created_by] + ([a.reviewed_by] if a.reviewed_by else []), f'stock adjustment {adj.no} posted after {u["main_role"]} approval',
             f'{len(ok_)} item(s), shortage Rs {tot["shortage"]}, damaged Rs {tot["damaged"]}, excess Rs {tot["excess"]}', a, 'Stock adjustment posted')
    recompute(a)
    if rec:
        a.status = 'In Progress'
        tell(a.auditors, f'{len(rec)} item(s) sent back for recount by {u["name"]}', remarks, a, 'Stock audit returned', 'warn')
    elif hold:
        request_decision(a, hold, 'Items held at the previous decision')
    else:
        a.status, a.closed_at, a.posted_by, a.posted_at = 'Closed', now, u['id'], now
    return f'{a.no}: {mode.lower()} — {len(ok_)} item(s) adjusted' + (f' ({adj.no})' if adj else '')


@on_return('saudit')
def _return_handler(p, apr, reason):
    _reject_handler(dict(p, ret=True), apr, reason)


@on_reject('saudit')
def _reject_handler(p, apr, reason):
    u = g.user
    a = db.session().get(StockAudit, p['id'], with_for_update=True)
    by_no = {ln.line_no: ln for ln in a.lines}
    cyc = [n for n in (p.get('lines') or []) if n in by_no and not by_no[n].decision]
    a.approval_no = None
    if p.get('ret'):
        for n in cyc:
            by_no[n].recount = True
        a.status = 'In Progress'
        _record(a, apr, 'Returned for recount / clarification', u, reason, recount=cyc)
        event(a, f'Returned for recount by {u["main_role"]}'[:40], reason)
        tell(a.auditors + [a.supervisor_id], 'returned for recount / clarification', reason or '', a, 'Stock audit returned', 'warn')
        return
    now = dt.datetime.now()
    for n in cyc:
        by_no[n].decision, by_no[n].decided_by, by_no[n].decided_at = 'Rejected', u['id'], now
    _record(a, apr, 'Rejected', u, reason, rejected=cyc)
    a.status = 'Closed' if any(ln.decision == 'Approved' for ln in a.lines) else 'Rejected'
    a.rejected_reason = reason
    if a.status == 'Closed':
        a.closed_at = now
    event(a, f'Adjustment rejected by {u["main_role"]}'[:40], reason)
    audit.write('Stock audit adjustment rejected', a.no, old='Under Review', new=a.status, reason=reason, category='stock', company=a.company, branch=a.branch_id)
    tell(a.auditors + [a.supervisor_id, a.created_by], 'adjustment rejected by the approver', reason or '', a, 'Stock audit rejected', 'warn')


def decide(aid, mode, decisions, remarks):
    """Impact-review decision by the Director / CFO. mode: all | partial | return | reject."""
    from ..core.approvals import decide as approval_decide
    a = get(aid, lock=True)
    u = g.user
    if a.status != 'Under Review' or not a.approval_no:
        raise conflict(f'{a.no} is not waiting for a decision.', 'wrong_status')
    if not _approver_ok(u):
        raise denied('Stock adjustments are approved only by the CFO or Director.', 'not_approver')
    apr = db.one('SELECT * FROM approvals WHERE no=:n', n=a.approval_no)
    p = json.loads(apr['payload']) if isinstance(apr['payload'], str) else dict(apr['payload'])
    remarks = (remarks or '').strip()
    if mode in ('return', 'reject'):
        if not remarks:
            raise bad('Give the remarks for ' + ('the recount / clarification.' if mode == 'return' else 'rejecting.'), 'reason_required')
        p['ret'] = mode == 'return'
    elif mode in ('all', 'partial'):
        cyc = [n for n in p.get('lines', [])]
        if mode == 'all':
            p['sel'] = {str(n): 'Approved' for n in cyc}
        else:
            sel = {str(k): v for k, v in (decisions or {}).items()}
            if any(v not in DECISIONS for v in sel.values()):
                raise bad('Each decision must be Approved, Pending, Recount or Rejected.', 'invalid')
            p['sel'] = {str(n): sel.get(str(n), 'Pending') for n in cyc}
        p['remarks'] = remarks
    else:
        raise bad('Mode must be all, partial, return or reject.', 'invalid')
    db.run('UPDATE approvals SET payload=:p WHERE id=:i', p=json.dumps(p), i=apr['id'])
    db.session().expire(a)
    r = approval_decide(apr['id'], 'approve' if mode in ('all', 'partial') else 'reject', remarks or ('Approved' if mode == 'all' else 'Partially approved'))
    return db.session().get(StockAudit, aid), r


def impact(a):
    """Everything the Director / CFO sees before deciding: item-wise variance and values, totals, location / category / condition
    breakdowns, the proposed journal, comments and evidence, plus the decision record so far."""
    d = detail(a)
    c = cfg()
    apr = db.one('SELECT id, no, required_role, requested_by, payload, requested_at FROM approvals WHERE no=:n AND status=\'Pending\'', n=a.approval_no) if a.approval_no else None
    cyc = []
    if apr:
        p = json.loads(apr['payload']) if isinstance(apr['payload'], str) else apr['payload']
        cyc = [n for n in p.get('lines', [])]
    rows = [ln for ln in d['lines'] if ln['physical_qty'] is not None]
    hi = c['hi_item']
    for ln in rows:
        cost = ln['unit_cost'] or 0
        net = (ln['physical_value'] or 0) - (ln['system_value'] or 0) - ((ln['dmg_value'] or 0) if c['write_off_damaged'] else 0)
        ln.update({'excess_qty': max(0, ln['variance_qty'] or 0), 'short_qty': max(0, -(ln['variance_qty'] or 0)),
                   'excess_value': round(max(0, ln['variance_qty'] or 0) * cost, 2) if d['show_cost'] else None,
                   'short_value': round(max(0, -(ln['variance_qty'] or 0)) * cost, 2) if d['show_cost'] else None, 'net_value': round(net, 2) if d['show_cost'] else None,
                   'high_value': abs(ln['variance_value'] or 0) >= hi or (ln['dmg_value'] or 0) >= hi, 'in_cycle': ln['line_no'] in cyc,
                   'colour': 'shortage' if (ln['variance_qty'] or 0) < 0 or ln['dmg_qty'] else 'excess' if (ln['variance_qty'] or 0) > 0 else 'zero'})
    var = [r for r in rows if r['variance_qty'] or r['dmg_qty']]
    T = lambda f: round(sum(f(r) or 0 for r in rows), 2)  # noqa: E731
    totals = {'audited': len(rows), 'matched': sum(1 for r in rows if not r['variance_qty'] and not r['dmg_qty']), 'short_qty': T(lambda r: r['short_qty']),
              'short_value': T(lambda r: r['short_value']), 'excess_qty': T(lambda r: r['excess_qty']), 'excess_value': T(lambda r: r['excess_value']),
              'dmg_qty': T(lambda r: r['dmg_qty']), 'dmg_value': T(lambda r: r['dmg_value']), 'system_value': T(lambda r: r['system_value']),
              'physical_value': T(lambda r: r['physical_value']), 'missing': sum(1 for r in rows if r['condition'] == 'Missing')}
    totals['net_value'] = round(totals['physical_value'] - totals['system_value'] - (totals['dmg_value'] if c['write_off_damaged'] else 0), 2)
    cats, conds = {}, {}
    for r in rows:
        x = cats.setdefault(r['category'] or 'Other', {'category': r['category'] or 'Other', 'items': 0, 'short_qty': 0, 'short_value': 0, 'excess_qty': 0, 'excess_value': 0, 'dmg_value': 0, 'net_value': 0})
        x['items'] += 1
        for k in ('short_qty', 'short_value', 'excess_qty', 'excess_value', 'dmg_value', 'net_value'):
            x[k] = round(x[k] + (r[k] or 0), 2)
    for name, f in (('Shortage', lambda r: r['short_qty']), ('Excess', lambda r: r['excess_qty'])):
        rs = [r for r in var if f(r)]
        conds[name] = {'items': len(rs), 'qty': sum(f(r) for r in rs), 'value': round(sum((r['short_value'] if name == 'Shortage' else r['excess_value']) or 0 for r in rs), 2)}
    for cond in ('Damaged', 'Expired', 'Missing', 'Returned'):
        rs = [r for r in var if r['condition'] == cond]
        conds[cond] = {'items': len(rs), 'qty': sum((r['dmg_qty'] or r['short_qty'] or r['excess_qty']) for r in rs),
                       'value': round(sum((r['dmg_value'] or r['short_value'] or r['excess_value'] or 0) for r in rs), 2)}
    sel = [r for r in rows if r['in_cycle']] or var
    gl = c['gl']
    js, jd, je = (round(sum(r['short_value'] or 0 for r in sel), 2), round(sum((r['dmg_value'] or 0) for r in sel), 2) if c['write_off_damaged'] else 0,
                  round(sum(r['excess_value'] or 0 for r in sel), 2))
    names = {r['code']: r['name'] for r in db.q('SELECT code, name FROM accounts WHERE company=:c', c=a.company)}
    journal = []
    for acct_d, acct_c, v, why in ((gl['short'], gl['inv'], js, 'Stock shortage / missing'), (gl['dmg'], gl['inv'], jd, 'Damaged / expired written off'),
                                   (gl['inv'], gl['excess'], je, 'Excess stock taken in (stock gain)')):
        if v:
            journal += [{'account': acct_d, 'name': names.get(acct_d, acct_d), 'debit': v, 'credit': 0, 'why': why},
                        {'account': acct_c, 'name': names.get(acct_c, acct_c), 'debit': 0, 'credit': v, 'why': why}]
    decisions = db.clean(db.q('SELECT d.*, u.name AS decided_by_name FROM stock_audit_decisions d LEFT JOIN users u ON u.id=d.decided_by WHERE audit_id=:i ORDER BY d.id', i=a.id))
    ev = db.clean(db.q("SELECT id, ref, kind, file_name, remarks, uploaded_at FROM evidence WHERE (ref=:n OR ref LIKE :p) AND status='Active' ORDER BY id", n=a.no, p=a.no + '/%')) \
        if 'uploaded_at' in {c_['name'] for c_ in db.q("SELECT COLUMN_NAME AS name FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='evidence'")} \
        else db.clean(db.q("SELECT id, ref, kind, file_name, remarks FROM evidence WHERE (ref=:n OR ref LIKE :p) AND status='Active' ORDER BY id", n=a.no, p=a.no + '/%'))
    return {'audit': {k: d[k] for k in ('id', 'no', 'company', 'branch_id', 'location_name', 'location_type', 'stock_area', 'status', 'type_label', 'audit_date',
                                        'auditor_names', 'supervisor_name', 'reviewed_by_name', 'review_note', 'escalated', 'show_cost', 'adjustment_no')},
            'approval': {'no': apr['no'], 'role': apr['required_role'], 'requested_at': str(apr['requested_at']), 'items': len(cyc)} if apr else None,
            'can_decide': bool(apr) and d['can'].get('decide', False), 'lines': rows, 'totals': totals, 'categories': list(cats.values()), 'conditions': conds,
            'journal': journal, 'ledgers': gl, 'write_off_damaged': c['write_off_damaged'], 'hi_item': hi,
            'comments': [e for e in d['events'] if e['detail'] or any(w in e['action'] for w in ('Submitted', 'Verified', 'Reviewed', 'Returned', 'approval', 'Rejected', 'Sent'))],
            'evidence': ev, 'decisions': decisions}


def next_step(a, u):
    """Who acts next, so the screen can say so (e.g. why the count boxes are not shown)."""
    names = lambda ids: [r['name'] for r in db.q('SELECT name FROM users WHERE id IN :i', i=tuple(ids) or ('',))]  # noqa: E731
    if a.status in OPEN:
        rec = any(ln.recount for ln in a.lines)
        return {'step': 'Count', 'who': names(a.auditors), 'mine': u['id'] in a.auditors and can(u, 'edit', 'saudit'),
                'text': 'Recount the items marked for recount, then submit again.' if rec else
                        'Enter the physical quantity of every item (counts save as you go), then submit for verification.'}
    if a.status == 'Submitted':
        return {'step': 'Verify', 'who': names([a.supervisor_id]), 'mine': u['id'] == a.supervisor_id or (can(u, 'verify', 'saudit') and float(u.get('rank') or 0) >= 2 and u['id'] not in a.auditors),
                'text': 'The location in-charge checks the count and verifies it (or returns it for recount).'}
    if a.status == 'Under Review' and not a.approval_no:
        return {'step': 'Review', 'who': [x['name'] for x in db.q('SELECT name FROM users WHERE id IN :i', i=tuple(users_with('review', a.branch_id, exclude=a.auditors)) or ('',))],
                'mine': can(u, 'review', 'saudit') and u['id'] not in a.auditors, 'text': 'Accounts / Inventory review the variance and send it to the CFO / Director.'}
    if a.status == 'Under Review':
        role = db.val('SELECT required_role FROM approvals WHERE no=:n', n=a.approval_no)
        who = [r['name'] for r in db.q("SELECT u.name FROM users u WHERE u.status='Active' AND (u.main_role IN :r OR u.id IN (SELECT user_id FROM user_roles WHERE role IN :r))",
                                         r=tuple(x for x in APPROVER_ROLES if x == role or (role == 'CFO' and x in ('Director', 'Owner', 'Chairman', 'Super Admin')) or (role == 'Director' and x in ('Owner', 'Chairman', 'Super Admin'))))]
        return {'step': f'Approve ({role})', 'who': who, 'mine': _approver_ok(u) and u['id'] not in a.auditors and u['rank'] >= (3 if role == 'Director' else 2.5),
                'text': 'Open the impact review, check the quantity and value impact, then approve all, approve some items, send for recount or reject.'}
    return None


def add_auditor(aid, user_id):
    from ..core.rbac import load_user
    a = get(aid, lock=True)
    if a.status not in OPEN:
        raise conflict('Auditors can be added only while the audit is in draft or being counted.', 'wrong_status')
    if not (can(g.user, 'add', 'saudit') or g.user['id'] == a.created_by):
        raise denied('Your role cannot change the audit team.', 'forbidden')
    x = load_user(user_id)
    if not x or x['status'] != 'Active':
        raise bad('Choose an active user.', 'bad_auditor')
    if user_id in a.auditors:
        raise conflict(f"{x['name']} is already an auditor.", 'duplicate')
    if user_id == a.supervisor_id:
        raise conflict('The supervisor cannot also be an auditor.', 'segregation')
    if not can(x, 'edit', 'saudit') or not in_scope(x, a.branch_id):
        raise bad(f"{x['name']} has no stock-count rights at this location.", 'bad_auditor')
    a.auditors = list(a.auditors) + [user_id]
    event(a, 'Auditor added', x['name'])
    audit.write('Stock audit auditor added', a.no, new=x['name'], category='stock', company=a.company, branch=a.branch_id)
    tell([user_id], 'assigned to you for counting', f'{a.no} at {a.branch_id}', a, 'Stock audit assigned')
    return a


def post_adjustment(aid):
    a = get(aid, lock=True)
    u = g.user
    if a.status != 'Approved':
        raise conflict(f'{a.no} is {a.status}. Stock adjustments now post automatically when the CFO / Director approves; only audits approved '
                       f'before release 2.3 are posted here.', 'wrong_status')
    if not can(u, 'post', 'saudit'):
        raise denied()
    lines = [ln for ln in a.lines if ln.physical_qty is not None and ln.physical_qty != (ln.system_qty or 0)]
    now = dt.datetime.now()
    if lines:
        adj = StockAdjustment(no=next_no(a.company, 'ADJ'), company=a.company, branch_id=a.branch_id, txn_date=today(), adj_type='Stock audit',
                              reason=f'Stock audit {a.no} ({TYPES[a.audit_type]}, {a.audit_date:%d-%m-%Y})', status='Approved', created_by=u['id'],
                              value=0, audit_no=a.no, approved_by=a.approved_by)
        val = Decimal(0)
        for n, ln in enumerate(lines, 1):
            q = ln.physical_qty - (ln.system_qty or 0)
            adj.lines.append(StockAdjustmentLine(line_no=n, item_id=ln.item_id, qty=q, unit_cost=ln.unit_cost or 0))
            val += abs(q) * Decimal(str(ln.unit_cost or 0))
        adj.value = money(val)
        db.add(adj)
        try:
            _post_adjustment(adj)        # stock moves (ref = adjustment no) + journal 5600/1300, books-lock check
        except Exception as e:
            if getattr(e, 'code', '') == 'negative_stock':
                raise conflict(f'{e.message} Stock has been sold or moved since it was counted; reopen the audit and recount that item.',
                               'negative_stock') from None
            raise
        a.adjustment_no = adj.no
        detail = {'adjustment': adj.no, 'lines': len(lines), 'value': float(adj.value), 'journal': adj.journal_no}
    else:
        detail = {'adjustment': None, 'note': 'no variance — nothing to adjust'}
    a.status, a.posted_by, a.posted_at, a.closed_at = 'Closed', u['id'], now, now
    event(a, 'Adjustment posted and audit closed' if lines else 'Closed (no variance)', detail)
    audit.write('Stock audit adjustment posted', a.no, old='Approved', new=detail, category='stock', company=a.company, branch=a.branch_id)
    tell(a.auditors + [a.supervisor_id, a.created_by, a.reviewed_by], 'approved stock adjustment posted' if lines else 'closed with no variance',
         json.dumps(detail), a, 'Stock adjustment posted')
    return a


def reopen(aid, reason):
    a = get(aid, lock=True)
    if a.status not in ('Closed', 'Rejected'):
        raise conflict(f'{a.no} is {a.status}; only a closed or rejected audit can be reopened.', 'wrong_status')
    if not can(g.user, 'restore', 'saudit'):
        raise denied('Only the Super Admin or a Director can reopen a stock audit.', 'forbidden')
    reason = (reason or '').strip()
    if not reason:
        raise bad('Give the reason for reopening.', 'reason_required')
    before = {'status': a.status, **_totals(a), 'adjustment': a.adjustment_no}
    if a.status == 'Closed':
        # the variance was already posted: counts start again so nothing can be adjusted twice
        for ln in a.lines:
            ln.physical_qty = ln.system_qty = ln.unit_cost = ln.counted_by = ln.counted_at = ln.dmg_qty = None
            ln.decision = ln.decided_by = ln.decided_at = ln.adjustment_no = None
            ln.recount = False
        recompute(a)
    a.status, a.reopen_count = 'In Progress', a.reopen_count + 1
    a.submitted_by = a.submitted_at = a.verified_by = a.verified_at = a.reviewed_by = a.reviewed_at = a.approval_no = None
    a.approved_by = a.approved_at = a.closed_at = a.rejected_reason = None
    a.escalated = False
    event(a, 'Reopened', {'reason': reason, 'before': before})
    audit.write('Stock audit reopened', a.no, old=before, new='In Progress', reason=reason, category='stock', company=a.company, branch=a.branch_id)
    tell(a.auditors + [a.supervisor_id], 'reopened — count again', reason, a, 'Stock audit assigned', 'warn')
    return a


def cancel(aid, reason):
    a = get(aid, lock=True)
    if a.status not in OPEN:
        raise conflict('Submitted, approved and closed audits are permanent records and cannot be cancelled.', 'wrong_status')
    if not (g.user['id'] == a.created_by or can(g.user, 'cancel', 'saudit')):
        raise denied()
    reason = (reason or '').strip()
    if not reason:
        raise bad('Give the reason.', 'reason_required')
    a.status = 'Cancelled'
    event(a, 'Cancelled', reason)
    audit.write('Stock audit cancelled', a.no, reason=reason, category='stock', company=a.company, branch=a.branch_id)
    return a


# ---------------------------------------------------------------- scheduler: pending-count reminders and overdue alerts
def remind():
    n = 0
    t = today()
    for r in db.q("SELECT id FROM stock_audits WHERE status IN ('Draft','In Progress','Submitted','Under Review') AND audit_date <= :t", t=t):
        a = db.session().get(StockAudit, r['id'])
        kind = 'Stock audit overdue' if a.due_date < t else 'Stock audit pending count' if a.status in OPEN else None
        if not kind or db.val('SELECT COUNT(*) FROM alerts WHERE ref=:r AND alert_type=:k AND DATE(created_at)=:t', r=a.no, k=kind, t=t):
            continue
        who = a.auditors if a.status in OPEN else [a.supervisor_id] if a.status == 'Submitted' else users_with('review', a.branch_id, exclude=a.auditors)
        tell(who + ([a.created_by] if kind.endswith('overdue') else []),
             f'{"OVERDUE since " + a.due_date.strftime("%d %b") if a.due_date < t else "physical count pending"} ({a.status})',
             f'{a.no} at {a.branch_id}: {a.counted_count}/{a.lines_count} counted. Due {a.due_date:%d %b %Y}.', a, kind,
             'warn' if kind.endswith('overdue') else 'info')
        n += 1
    return n


# ---------------------------------------------------------------- search
def search(args, user):
    sql = ['''SELECT a.*, l.name AS location_name, u.name AS supervisor_name FROM stock_audits a JOIN branches b ON b.id=a.branch_id
              JOIN locations l ON l.code=b.location LEFT JOIN users u ON u.id=a.supervisor_id WHERE a.company IN :cos''']
    p = {'cos': tuple(user['companies']) or ('',)}
    from ..core.rbac import scope_sql
    sc, sp = scope_sql(user, 'a.branch_id')
    if sc:
        sql.append('AND ' + sc)
        p.update(sp)
    f = args.get
    for k, col in (('company', 'a.company'), ('branch', 'a.branch_id'), ('location', 'a.location'), ('location_type', 'a.location_type'),
                   ('status', 'a.status'), ('audit_type', 'a.audit_type')):
        if f(k):
            sql.append(f'AND {col}=:{k}')
            p[k] = f(k)
    if f('from'):
        sql.append('AND a.audit_date >= :fd')
        p['fd'] = parse_date(f('from'))
    if f('to'):
        sql.append('AND a.audit_date <= :td')
        p['td'] = parse_date(f('to'))
    if f('auditor'):
        sql.append('AND JSON_CONTAINS(a.auditors, JSON_QUOTE(:aud))')
        p['aud'] = f('auditor')
    if f('item'):
        sql.append('AND EXISTS (SELECT 1 FROM stock_audit_lines x WHERE x.audit_id=a.id AND x.item_id=:it)')
        p['it'] = f('item')
    if f('category'):
        sql.append('AND EXISTS (SELECT 1 FROM stock_audit_lines x JOIN items i ON i.id=x.item_id WHERE x.audit_id=a.id AND i.category=:cat)')
        p['cat'] = f('category')
    flags = {'shortage': 'a.shortage_value > 0', 'excess': 'a.excess_value > 0', 'damaged': 'a.damaged_value > 0'}
    for fl in (f('flag') or '').split(','):
        if fl in flags:
            sql.append('AND ' + flags[fl])
    if f('min_value'):
        sql.append('AND (a.shortage_value + a.excess_value) >= :mn')
        p['mn'] = Decimal(str(f('min_value')))
    if f('max_value'):
        sql.append('AND (a.shortage_value + a.excess_value) <= :mx')
        p['mx'] = Decimal(str(f('max_value')))
    if f('q'):
        sql.append('AND a.no LIKE :q')
        p['q'] = f"%{f('q')}%"
    if f('pending'):
        sql.append("AND a.status IN ('Draft','In Progress','Submitted','Under Review')")
    if f('mine'):
        sql.append('AND (JSON_CONTAINS(a.auditors, JSON_QUOTE(:me)) OR a.supervisor_id=:me)')
        p['me'] = user['id']
    rows = db.clean(db.q(' '.join(sql) + ' ORDER BY a.audit_date DESC, a.id DESC LIMIT 500', **p))
    show_cost = can(user, 'cost', 'saudit') or can(user, 'fin', 'saudit')
    names = {r['id']: r['name'] for r in db.q('SELECT id, name FROM users')}
    t = str(today())
    for r in rows:
        r['auditor_names'] = [names.get(x, x) for x in (r['auditors'] or [])]
        r['type_label'] = TYPES.get(r['audit_type'])
        r['accuracy_pct'] = round(100 * r['matched_count'] / r['counted_count'], 1) if r['counted_count'] else None
        r['variance_value'] = (r['excess_value'] or 0) - (r['shortage_value'] or 0)
        r['overdue'] = r['status'] in PENDING and r['due_date'] < t
        if not show_cost:
            for k in ('system_value', 'physical_value', 'shortage_value', 'excess_value', 'damaged_value', 'variance_value'):
                r[k] = None
    return rows
