"""Stock audit API (screen key 'saudit'). Business rules live in services/stock_audit.py; reports and exports in
services/stock_audit_reports.py."""
import datetime as dt
import random

from flask import Blueprint, Response, g, jsonify, request

from .. import db
from ..core.rbac import body, can, high_risk, login_required, require
from ..errors import bad, denied
from ..services import stock_audit as sa
from ..services import stock_audit_reports as rep

bp = Blueprint('stock_audit', __name__, url_prefix='/api/stock-audits')


def _file(data, name, mime, what):
    db.insert('access_log', dict(user_id=g.user['id'], action='export', what=what, detail=str(dict(request.args))[:300]))
    return Response(data, mimetype=mime, headers={'Content-Disposition': f'attachment; filename="{name}"'})


def _rows(rows, name, title=None):
    fmt = request.args.get('format')
    if fmt in ('csv', 'xlsx') and not can(g.user, 'export', 'saudit'):
        raise denied('You cannot export stock-audit data.', 'forbidden')
    if fmt in ('pdf', 'print') and not can(g.user, fmt, 'saudit'):
        raise denied('Your role cannot print stock-audit reports.' if fmt == 'print' else 'Your role cannot download stock-audit reports as PDF.', 'forbidden')
    if fmt == 'print':
        db.insert('access_log', dict(user_id=g.user['id'], action='print', what=f'Stock audit {name} (PRINT)', detail=str(dict(request.args))[:300]))
        return jsonify(rows)
    if fmt == 'pdf':
        from ..services import docs_render
        t = rep.as_table(title or name, db.clean(rows) if rows and not isinstance(rows[0], list) else rows)
        t['period'] = {'from': request.args.get('from') or '', 'to': request.args.get('to') or str(dt.date.today())}
        data, how = docs_render.pdf(t, {'company_name': request.args.get('company') or 'All companies',
                                        'footer': 'Kiswa-Zaibus Operations · stock audit report'})
        r = _file(data, f'{name}-{dt.date.today()}.pdf', 'application/pdf', f'Stock audit {name} (PDF)')
        r.headers['X-Rendered-By'] = how
        return r
    if fmt == 'csv':
        return _file(rep.to_csv(rows), f'{name}-{dt.date.today()}.csv', 'text/csv', f'Stock audit {name}')
    if fmt == 'xlsx':
        return _file(rep.to_xlsx([(name, rows)]), f'{name}-{dt.date.today()}.xlsx',
                     'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', f'Stock audit {name}')
    return jsonify(rows)


@bp.get('')
@require('view', 'saudit')
def search():
    """Search audits: from, to, company, branch, location_type, status, audit_type, auditor, item, category, flag=shortage,excess,damaged,
    min_value, max_value, q, pending, mine; format=csv|xlsx."""
    return _rows(sa.search(request.args, g.user), 'stock-audits')


@bp.get('/meta')
@require('view', 'saudit')
def meta():
    """Choices for the create form: audit types, conditions, locations by type, eligible auditors and supervisors per branch."""
    from ..core.rbac import in_scope, load_user
    u = g.user
    brs = db.q('''SELECT b.id, b.company, b.location, l.name, l.type FROM branches b JOIN locations l ON l.code=b.location
                  WHERE b.active=1 AND l.active=1 AND b.company IN :c ORDER BY l.name''', c=tuple(u['companies']) or ('',))
    brs = [dict(b, location_type=sa.LOCATION_TYPE.get(b['type'], 'Branch')) for b in brs if in_scope(u, b['id'])]
    people = []
    for r in db.q("SELECT id, name, emp_id, main_role FROM users WHERE status='Active' ORDER BY name"):
        x = load_user(r['id'])
        if x and (can(x, 'edit', 'saudit') or can(x, 'verify', 'saudit')):
            people.append({'id': r['id'], 'name': r['name'], 'role': r['main_role'], 'count': can(x, 'edit', 'saudit'), 'verify': can(x, 'verify', 'saudit'),
                           'branches': '*' if x['branches'] == '*' else list(x['branches'])})
    return jsonify({'types': sa.TYPES, 'conditions': sa.CONDITIONS, 'branches': brs, 'people': people, 'settings': sa.cfg(),
                    'location_types': ['Warehouse', 'Branch', 'Store', 'Godown'], 'reports': rep.REPORTS})


@bp.post('')
@require('add', 'saudit')
def create():
    a = sa.create(body())
    return jsonify(sa.detail(a)), 201


@bp.get('/<int:aid>')
@require('view', 'saudit')
def get(aid):
    return jsonify(sa.detail(sa.get(aid)))


@bp.get('/by-no/<path:no>')
@require('view', 'saudit')
def get_by_no(no):
    return jsonify(sa.detail(sa.by_no(no)))


@bp.post('/<int:aid>/start')
@require('edit', 'saudit')
def start(aid):
    return jsonify(sa.detail(sa.start(aid)))


@bp.post('/<int:aid>/counts')
@require('edit', 'saudit')
def counts(aid):
    """Save counts: {rows: [{line_no | item_id | code, physical_qty, condition, remarks, batch_no}], mode: set | add}. 'add' = +1 per scan."""
    d = body()
    return jsonify(sa.detail(sa.save_counts(aid, d.get('rows') or [], d.get('mode') or 'set')))


@bp.post('/<int:aid>/scan')
@require('edit', 'saudit')
def scan(aid):
    """One barcode scan: {code, qty?} adds qty (default 1) to that item's count; unknown items on the shelf are added to the audit."""
    d = body()
    if not str(d.get('code') or '').strip():
        raise bad('Scan or type a barcode.', 'invalid')
    a = sa.save_counts(aid, [{'code': d['code'].strip(), 'physical_qty': d.get('qty') or 1}], 'add')
    code = d['code'].strip()
    ln = next((x for x in sa.detail(a)['lines'] if code in (x['item_id'], x['barcode'])), None)
    return jsonify({'line': ln, 'counted_count': a.counted_count, 'lines_count': a.lines_count})


@bp.post('/<int:aid>/import')
@require('edit', 'saudit')
def import_sheet(aid):
    """Upload a filled count sheet (multipart 'file': .xlsx or .csv with item_code / barcode, physical_qty, condition, remarks, batch_no)."""
    f = request.files.get('file')
    if not f:
        raise bad('Choose the count sheet file.', 'no_file')
    a, n = sa.import_sheet(aid, f.filename, f.read(5 * 1024 * 1024 + 1))
    out = sa.detail(a)
    out['imported_rows'] = n
    return jsonify(out)


@bp.get('/<int:aid>/count-sheet')
@require('view', 'saudit')
def count_sheet(aid):
    """Blank count sheet (.xlsx, or format=csv) to print or fill offline; system quantities are not shown (blind count)."""
    d = sa.detail(sa.get(aid))
    rows = rep.count_sheet(d)
    name = d['no'].replace('/', '-')
    if request.args.get('format') == 'csv':
        return _file(rep.to_csv(rows), f'{name}-count-sheet.csv', 'text/csv', f'Count sheet {d["no"]}')
    return _file(rep.to_xlsx([('Count sheet', rows)]), f'{name}-count-sheet.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                 f'Count sheet {d["no"]}')


@bp.get('/<int:aid>/export')
@require('view', 'saudit')
def export(aid):
    """The full audit record: format=xlsx (audit, lines, activity sheets) or format=pdf (signed-off A4 record)."""
    if not (can(g.user, 'export', 'saudit') or can(g.user, 'pdf', 'saudit') or can(g.user, 'print', 'saudit')):
        raise denied()
    d = sa.detail(sa.get(aid))
    name = d['no'].replace('/', '-')
    if request.args.get('format') == 'pdf':
        return _file(rep.audit_pdf(d), f'{name}.pdf', 'application/pdf', f'Stock audit {d["no"]} PDF')
    return _file(rep.to_xlsx(rep.audit_sheets(d)), f'{name}.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', f'Stock audit {d["no"]} Excel')


@bp.post('/<int:aid>/submit')
@require('submit', 'saudit')
def submit(aid):
    return jsonify(sa.detail(sa.submit(aid)))


@bp.post('/<int:aid>/verify')
@require('verify', 'saudit')
def verify(aid):
    """Location in-charge: {ok: true} verifies the count; {ok: false, note} returns it for recount."""
    d = body()
    return jsonify(sa.detail(sa.verify(aid, bool(d.get('ok')), d.get('note'))))


@bp.post('/<int:aid>/review')
@require('review', 'saudit')
def review(aid):
    """Accounts / inventory review: {action: approve | return | reject, note}. approve sends the variance for final approval by value
    (202 with the approval) or approves it within the reviewer's limit."""
    d = body()
    a, ap = sa.review(aid, d.get('action'), d.get('note'))
    out = sa.detail(a)
    if ap:
        out['approval'] = ap
        return jsonify(out), 202
    return jsonify(out)


@bp.post('/<int:aid>/post')
@require('post', 'saudit')
def post(aid):
    """Post the approved variance as one stock adjustment (stock moves + journal) and close the audit."""
    return jsonify(sa.detail(sa.post_adjustment(aid)))


@bp.post('/<int:aid>/reopen')
@require('restore', 'saudit')
@high_risk('delete')
def reopen(aid):
    return jsonify(sa.detail(sa.reopen(aid, body().get('reason'))))


@bp.post('/<int:aid>/cancel')
@login_required
def cancel(aid):
    return jsonify(sa.detail(sa.cancel(aid, body().get('reason'))))


@bp.get('/dashboard')
@require('view', 'saudit')
def dashboard():
    return jsonify(rep.dashboard(g.user, request.args))


@bp.get('/reports/<key>')
@require('view', 'saudit')
def report(key):
    """One of the ten stock-audit reports (see services/stock_audit_reports.REPORTS); format=csv|xlsx to download."""
    return _rows(rep.report(key, g.user, request.args), key, rep.REPORTS.get(key))


@bp.get('/random-items')
@require('add', 'saudit')
def random_items():
    """Preview a spot-check sample: ?branch=&n= (items with stock at that branch)."""
    rows = db.q('''SELECT i.id, i.name, i.size FROM stock_balance s JOIN items i ON i.id=s.item_id WHERE s.branch_id=:b AND s.qty > 0 AND i.active=1''',
                b=request.args.get('branch'))
    n = min(int(request.args.get('n') or 20), len(rows))
    return jsonify(random.SystemRandom().sample(rows, n) if n else [])


@bp.get('/<int:aid>/impact')
@require('view', 'saudit')
def impact(aid):
    """Pre-approval impact review for the CFO / Director: item-wise quantity and value impact, totals, breakdowns and the proposed journal."""
    return jsonify(sa.impact(sa.get(aid)))


@bp.post('/<int:aid>/decide')
@require('view', 'saudit')
def decide(aid):
    """CFO / Director decision: {mode: all | partial | return | reject, decisions: {line_no: Approved | Pending | Recount | Rejected}, remarks}.
    Approved items post the stock adjustment and journal at once."""
    d = body()
    a, r = sa.decide(aid, d.get('mode'), d.get('decisions') or {}, d.get('remarks'))
    out = sa.detail(a)
    out['result'] = r
    return jsonify(out)


@bp.post('/<int:aid>/auditors')
@require('view', 'saudit')
def add_auditor(aid):
    """Add a counter to an open audit: {user_id}."""
    return jsonify(sa.detail(sa.add_auditor(aid, str(body().get('user_id') or '').strip())))
