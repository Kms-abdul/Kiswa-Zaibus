"""Finance & tax API (screen key 'fin', release 2.3): dashboard, vouchers, GST, TDS, reports (with Excel / PDF through kz-docs), banking,
cheques, payables, fixed assets, compliance calendar, periods and the finance masters. Business rules live in services/fin_*.py."""
import datetime as dt
import io
import json

from flask import Blueprint, Response, g, jsonify, request

from .. import db
from ..core import audit
from ..core.rbac import body, can, need_fresh_password, require
from ..errors import bad, denied, not_found
from ..services import docs_render, fin_gst, fin_ops, fin_reports, fin_tds
from ..services import fin_vouchers as fv
from ..services.common import check_company, parse_date, today
from ..services.fin_common import GST_STATES, VTYPES, cfg, gstin_check, pan_check

bp = Blueprint('finance', __name__, url_prefix='/api/fin')


def _int(d, k, label):
    try:
        return int(d.get(k))
    except (TypeError, ValueError):
        raise bad(f'Choose the {label}.', 'invalid') from None


def _co(d=None):
    co = (d or request.args).get('company') or (g.user['companies'][0] if g.user['companies'] else None)
    check_company(g.user, co)
    return co


def _file(data, name, mime, what):
    db.insert('access_log', dict(user_id=g.user['id'], action='export', what=what, detail=str(dict(request.args))[:300]))
    return Response(data, mimetype=mime, headers={'Content-Disposition': f'attachment; filename="{name}"'})


def _table(f):
    """Rows of an uploaded .xlsx / .csv file as lists of cells."""
    name, data = f.filename.lower(), f.read(10 * 1024 * 1024 + 1)
    if len(data) > 10 * 1024 * 1024:
        raise bad('The file is larger than 10 MB.', 'too_large')
    if name.endswith('.xlsx'):
        from openpyxl import load_workbook
        ws = load_workbook(io.BytesIO(data), read_only=True, data_only=True).active
        return [[('' if c is None else (c.isoformat()[:10] if isinstance(c, (dt.date, dt.datetime)) else c)) for c in r] for r in ws.iter_rows(values_only=True)]
    if name.endswith('.csv') or name.endswith('.txt'):
        import csv
        return list(csv.reader(io.StringIO(data.decode('utf-8-sig', errors='replace'))))
    raise bad('Upload an Excel (.xlsx) or CSV file. PDF statements: save as Excel or CSV first.', 'bad_file')


# ---------- meta & dashboard ----------
@bp.get('/meta')
@require('view', 'fin')
def meta():
    co = _co()
    u = g.user
    acts = sorted(u['perms'].get('fin', set()))
    return jsonify({
        'company': co, 'companies': db.clean(db.q('SELECT code, name, gstin, books_locked_to FROM companies WHERE code IN :c', c=tuple(u['companies']) or ('',))),
        'types': [{'code': k, 'label': v[0], 'party': v[2], 'gst': v[4], 'tds': v[5]} for k, v in VTYPES.items()],
        'accounts': db.clean(db.q('SELECT code, name, grp, active, is_control FROM accounts WHERE company=:c ORDER BY code', c=co)),
        'branches': db.clean(db.q("SELECT b.id, l.name FROM branches b JOIN locations l ON l.code=b.location WHERE b.company=:c AND b.active=1 ORDER BY l.name", c=co))
        + [{'id': f'{co}-HO', 'name': 'Head Office'}],
        'cost_centres': db.clean(db.q('SELECT * FROM cost_centres WHERE active=1 ORDER BY kind, id')),
        'customers': db.clean(db.q('SELECT id, name, gstin FROM customers WHERE company=:c AND (type IS NULL OR type<>\'Student\') ORDER BY name LIMIT 2000', c=co)),
        'suppliers': db.clean(db.q('SELECT id, name, gstin FROM suppliers WHERE company=:c AND active=1 ORDER BY name', c=co)),
        'employees': db.clean(db.q('SELECT id, name, branch_id FROM employees WHERE company=:c AND active=1 ORDER BY name', c=co)),
        'banks': fin_ops.bank_accounts(co), 'tds_sections': db.clean(db.q('SELECT * FROM tds_sections ORDER BY code')),
        'hsn': db.clean(db.q('SELECT * FROM hsn_codes ORDER BY code')), 'fin_years': db.clean(db.q('SELECT * FROM fin_years WHERE company=:c ORDER BY from_date DESC', c=co)),
        'asset_categories': db.clean(db.q('SELECT * FROM asset_categories ORDER BY name')), 'states': GST_STATES, 'reports': fin_reports.catalogue(),
        'settings': cfg(), 'tds_forms': fin_tds.FORMS, 'actions': acts,
        'can': {a: can(u, a, 'fin') for a in ('add', 'edit', 'submit', 'approve', 'reverse', 'cancel', 'export', 'master', 'restore', 'audit')}
        | {'close': fv.is_closer(u), 'print': any(can(u, a, 'fin') for a in ('print', 'pdf', 'export')), 'files': can(u, 'export', 'fin') or can(u, 'pdf', 'fin')},
        'docs_service': docs_render.health()})


@bp.get('/dashboard')
@require('view', 'fin')
def dashboard():
    return jsonify(fin_ops.dashboard(_co()))


# ---------- vouchers ----------
@bp.get('/vouchers')
@require('view', 'fin')
def vouchers():
    return jsonify(fv.search(request.args, g.user))


@bp.post('/vouchers')
@require('add', 'fin')
def create_voucher():
    d = body()
    v = fv.create(d)
    if d.get('submit'):
        v = fv.submit(v.id, direct=bool(d.get('direct')))
    return jsonify(fv.view(v)), 201


@bp.post('/vouchers/preview')
@require('view', 'fin')
def preview():
    """Build a voucher without saving it: GST, TDS, the balancing party line, the approval route and any errors, for the editor."""
    d = body()
    sp = db.session().begin_nested()
    try:
        v = fv.create({**d, 'options': {**(d.get('options') or {})}})
        diff = fv.build(v)
        res = {'built': v.built, 'gst': v.gst, 'tds': v.tds, 'total': float(v.total), 'taxable': float(v.taxable), 'tax': float(v.tax), 'diff': round(diff, 2),
               'errors': fv.validate(v, diff), 'route': fv.route(v, g.user), 'duplicate': fv.dup_bill(v)}
    finally:
        sp.rollback()
    return jsonify(res)


@bp.get('/vouchers/<int:vid>')
@require('view', 'fin')
def get_voucher(vid):
    return jsonify(fv.view(fv.get(vid)))


@bp.get('/vouchers/by-no/<path:no>')
@require('view', 'fin')
def get_voucher_by_no(no):
    vid = db.val('SELECT id FROM fin_vouchers WHERE no=:n AND company IN :c', n=no, c=tuple(g.user['companies']) or ('',))
    if not vid:
        j = db.clean(db.one('SELECT * FROM journals WHERE (no=:n OR ref=:n) AND company IN :c ORDER BY id LIMIT 1', n=no, c=tuple(g.user['companies']) or ('',)))
        if not j:
            raise not_found('Voucher not found.')
        j['lines'] = db.clean(db.q('SELECT * FROM journal_lines WHERE journal_id=:i ORDER BY line_no', i=j['id']))
        return jsonify({'journal': j})
    return jsonify(fv.view(fv.get(vid)))


@bp.put('/vouchers/<int:vid>')
@require('edit', 'fin')
def update_voucher(vid):
    return jsonify(fv.view(fv.update(vid, body())))


@bp.post('/vouchers/<int:vid>/submit')
@require('add', 'fin')
def submit_voucher(vid):
    return jsonify(fv.view(fv.submit(vid, direct=bool(body().get('direct')))))


@bp.post('/vouchers/<int:vid>/cancel')
@require('view', 'fin')
def cancel_voucher(vid):
    return jsonify(fv.view(fv.cancel(vid, body().get('reason'))))


@bp.post('/vouchers/<int:vid>/reverse')
@require('reverse', 'fin')
def reverse_voucher(vid):
    d = body()
    return jsonify(fv.view(fv.reverse(vid=vid, why=d.get('reason'), on=d.get('date')))), 201


@bp.post('/journals/<path:no>/reverse')
@require('reverse', 'fin')
def reverse_journal(no):
    d = body()
    return jsonify(fv.view(fv.reverse(journal_no=no, why=d.get('reason'), on=d.get('date')))), 201


@bp.post('/payroll')
@require('add', 'fin')
def payroll():
    d = body()
    from ..services.fin_common import need_ym
    return jsonify(fv.view(fv.payroll(_co(d), need_ym(d.get('month'))))), 201


# ---------- GST ----------
def _ym():
    return request.args.get('ym') or f'{today():%Y-%m}'


@bp.get('/gst/summary')
@require('view', 'fin')
def gst_summary():
    return jsonify(fin_gst.summary(_co(), _ym()))


@bp.get('/gst/docs')
@require('view', 'fin')
def gst_docs():
    a, b = fin_gst.period(_ym())
    return jsonify(fin_gst.docs(_co(), parse_date(request.args['from'], 'from') if request.args.get('from') else a, parse_date(request.args['to'], 'to') if request.args.get('to') else b))


@bp.get('/gst/return/<ret>')
@require('view', 'fin')
def gst_return(ret):
    co, ym = _co(), _ym()
    if ret == '1':
        return jsonify(fin_gst.gstr1(co, ym))
    if ret == '3B':
        return jsonify(fin_gst.gstr3b(co, ym))
    if ret == '9':
        return jsonify(fin_gst.gstr9(co, request.args.get('fy')))
    if ret == '9C':
        return jsonify(fin_gst.gstr9c(co, request.args.get('fy')))
    raise not_found('Unknown return.')


@bp.get('/gst/return/<ret>/json')
@require('export', 'fin')
def gst_json(ret):
    co, ym = _co(), _ym()
    data = fin_gst.gstr1_json(co, ym) if ret == '1' else fin_gst.gstr3b_json(co, ym) if ret == '3B' else None
    if data is None:
        raise not_found('JSON is available for GSTR-1 and GSTR-3B.')
    return _file(json.dumps(data, indent=1).encode(), f'GSTR{ret}_{fin_gst.gstin_of(co)}_{fin_gst.fp(ym)}.json', 'application/json', f'GSTR-{ret} JSON {ym}')


@bp.post('/gst/pay')
@require('add', 'fin')
def gst_pay():
    d = body()
    return jsonify(fv.view(fin_gst.pay(_co(d), d.get('ym'), d))), 201


@bp.post('/gst/file')
@require('submit', 'fin')
def gst_file():
    d = body()
    fin_gst.file_return(_co(d), d.get('ret'), d.get('ym'), d.get('arn'), parse_date(d['date'], 'date') if d.get('date') else None)
    return jsonify(fin_gst.summary(_co(d), d.get('ym')))


@bp.post('/gst/einvoice')
@require('add', 'fin')
def gst_einvoice():
    d = body()
    if d.get('cancel'):
        fin_gst.einvoice_cancel(d.get('no'), d.get('reason'))
        return jsonify({'ok': True})
    return jsonify(fin_gst.einvoice(_co(d), d.get('no')))


@bp.post('/gst/eway')
@require('add', 'fin')
def gst_eway():
    d = body()
    return jsonify(fin_gst.eway(_co(d), d.get('no'), d))


@bp.post('/gst/portal')
@require('import', 'fin')
def gst_portal():
    """GSTR-2B / 2A download: multipart 'file' (.xlsx / .csv) with company, kind (2B | 2A), ym; or sample=1 for demo rows."""
    d = request.form if request.files else body()
    co, kind, ym = _co(d), d.get('kind') or '2B', d.get('ym')
    if kind not in ('2A', '2B') or not ym:
        raise bad('Choose GSTR-2B or 2A and the month.', 'invalid')
    n = fin_gst.portal_sample(co, kind, ym) if d.get('sample') else fin_gst.portal_import(co, kind, ym, _table(request.files['file']))
    fin_gst.reconcile_alert(co, kind, ym)
    return jsonify({'rows': n, **fin_gst.reconcile(co, kind, ym)})


@bp.get('/gst/reconcile')
@require('view', 'fin')
def gst_reconcile():
    return jsonify(fin_gst.reconcile(_co(), request.args.get('kind') or '2B', _ym()))


# ---------- TDS ----------
@bp.get('/tds/register')
@require('view', 'fin')
def tds_register():
    a = request.args
    return jsonify(fin_tds.register(_co(), a.get('from') or None, a.get('to') or None, a.get('party') or None, a.get('section') or None, a.get('status') or None))


@bp.post('/tds/calc')
@require('view', 'fin')
def tds_calc():
    d = body()
    return jsonify(fin_tds.calc(_co(d), d.get('party'), d.get('section'), d.get('base') or 0, parse_date(d['date'], 'date') if d.get('date') else today()))


@bp.post('/tds/pay')
@require('add', 'fin')
def tds_pay():
    d = body()
    return jsonify(fv.view(fin_tds.pay_challan(_co(d), d.get('ids') or [], d))), 201


@bp.get('/tds/quarter')
@require('view', 'fin')
def tds_quarter():
    a = request.args
    return jsonify(fin_tds.quarter(_co(), a.get('fy'), a.get('quarter') or 'Q1', a.get('form') or '26Q'))


@bp.post('/tds/file')
@require('submit', 'fin')
def tds_file():
    d = body()
    return jsonify(fin_tds.file_return(_co(d), d.get('fy'), d.get('quarter'), d.get('form'), d.get('token'), parse_date(d['date'], 'date') if d.get('date') else None))


@bp.post('/tds/certificate')
@require('submit', 'fin')
def tds_cert():
    d = body()
    return jsonify(fin_tds.issue_certificate(_co(d), d.get('fy'), d.get('quarter'), d.get('party'))), 201


@bp.get('/tds/checks')
@require('view', 'fin')
def tds_checks():
    co = _co()
    return jsonify({'thresholds': fin_tds.thresholds(co), 'pan': fin_tds.pan_issues(co)})


@bp.post('/validate')
@require('view', 'fin')
def validate_ids():
    d = body()
    return jsonify({'gstin': gstin_check(d['gstin']) if d.get('gstin') else None, 'pan': pan_check(d['pan']) if d.get('pan') else None})


# ---------- reports ----------
@bp.get('/reports')
@require('view', 'fin')
def reports():
    return jsonify(fin_reports.catalogue())


@bp.get('/reports/<key>')
@require('view', 'fin')
def report(key):
    co = _co()
    rep = fin_reports.run(key, co, request.args.to_dict())
    fmt = request.args.get('format')
    if not fmt:
        return jsonify(rep)
    if fmt == 'print':
        if not any(can(g.user, a, 'fin') for a in ('print', 'pdf', 'export')):
            raise denied('Your role cannot print finance reports.', 'forbidden')
        db.insert('access_log', dict(user_id=g.user['id'], action='print', what=f"Finance report {rep['title']} (PRINT)", detail=str(dict(request.args))[:300]))
        return jsonify(rep)
    if not (can(g.user, 'export', 'fin') or can(g.user, 'pdf', 'fin')):
        raise denied('You cannot export finance reports.', 'forbidden')
    meta_ = {'company_name': db.val('SELECT name FROM companies WHERE code=:c', c=co)}
    name = f"{key}-{co}-{rep['period']['to']}"
    if fmt == 'csv':
        return _file(docs_render.csv_bytes(rep), name + '.csv', 'text/csv', f"Finance report {rep['title']}")
    if fmt == 'xlsx':
        data, how = docs_render.xlsx(rep, meta_)
        r = _file(data, name + '.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', f"Finance report {rep['title']} (Excel)")
    elif fmt == 'pdf':
        data, how = docs_render.pdf(rep, meta_)
        r = _file(data, name + '.pdf', 'application/pdf', f"Finance report {rep['title']} (PDF)")
    else:
        raise bad('Format must be print, csv, xlsx or pdf.', 'invalid')
    r.headers['X-Rendered-By'] = how
    return r


# ---------- banking ----------
@bp.get('/bank/statement')
@require('view', 'fin')
def bank_statement():
    co = _co()
    acc = request.args.get('account') or '1120'
    return jsonify({'lines': fin_ops.statement(co, acc, request.args.get('open') == '1'), 'brs': fin_ops.brs(co, acc, today()),
                    'book': db.clean(fin_ops.book_lines(co, acc, today() - dt.timedelta(days=120), today()))})


@bp.post('/bank/import')
@require('import', 'fin')
def bank_import():
    d = request.form if request.files else body()
    co, acc = _co(d), d.get('account') or '1120'
    n = fin_ops.statement_demo(co, acc) if d.get('demo') else fin_ops.statement_import(co, acc, _table(request.files['file']), request.files['file'].filename[:40])
    return jsonify({'imported': n, 'matched': fin_ops.auto_match(co, acc)})


@bp.post('/bank/match')
@require('edit', 'fin')
def bank_match():
    d = body()
    if d.get('auto'):
        return jsonify({'matched': fin_ops.auto_match(_co(d), d.get('account') or '1120')})
    if d.get('unmatch'):
        fin_ops.unmatch(_int(d, 'line', 'statement line'))
    else:
        if not isinstance(d.get('journal'), str) or not d['journal']:
            raise bad('Choose the book entry to match.', 'invalid')
        fin_ops.manual_match(_int(d, 'line', 'statement line'), d['journal'], _int(d, 'journal_line', 'book entry line'))
    return jsonify({'ok': True})


@bp.post('/bank/voucher')
@require('add', 'fin')
def bank_voucher():
    d = body()
    return jsonify(fv.view(fin_ops.statement_voucher(_int(d, 'line', 'statement line'), d.get('account') if isinstance(d.get('account'), str) else None))), 201


@bp.get('/cheques')
@require('view', 'fin')
def cheques():
    return jsonify(db.clean(db.q('SELECT * FROM cheques WHERE company=:c ORDER BY cdate DESC, id DESC LIMIT 500', c=_co())))


@bp.post('/cheques/<int:cid>/status')
@require('edit', 'fin')
def cheque_status(cid):
    d = body()
    fin_ops.cheque_set(cid, d.get('status'), d.get('reason') or '')
    return jsonify(db.clean(db.one('SELECT * FROM cheques WHERE id=:i', i=cid)))


# ---------- payables & receivables ----------
@bp.get('/payables/proposal')
@require('view', 'fin')
def proposal():
    upto = parse_date(request.args['upto'], 'date') if request.args.get('upto') else today() + dt.timedelta(days=7)
    return jsonify(fin_ops.pay_proposal(_co(), upto))


@bp.post('/payables/proposal')
@require('add', 'fin')
def proposal_create():
    d = body()
    picks = d.get('picks')
    if not isinstance(picks, list) or not picks or not all(isinstance(x, dict) and x.get('party') and x.get('doc') and float(x.get('amount') or 0) > 0 for x in picks):
        raise bad('Choose the bills to pay.', 'nothing_selected')
    return jsonify(fin_ops.pay_proposal_create(_co(d), picks, d.get('bank') or '1120')), 201


@bp.get('/open-items')
@require('view', 'fin')
def open_items():
    return jsonify(db.clean(fin_reports.open_items(_co(), request.args.get('kind') or 'cust', today())))


# ---------- fixed assets ----------
@bp.get('/assets')
@require('view', 'fin')
def assets():
    from ..models import FixedAsset
    co = _co()
    return jsonify([fin_ops.asset_view(a) for a in db.session().query(FixedAsset).filter(FixedAsset.company == co).order_by(FixedAsset.no)])


@bp.post('/assets')
@require('add', 'fin')
def asset_create():
    a, v = fin_ops.capitalise(body())
    return jsonify({'asset': fin_ops.asset_view(a), 'voucher': fv.view(v, False)}), 201


@bp.post('/assets/<int:aid>/<action>')
@require('edit', 'fin')
def asset_action(aid, action):
    d = body()
    if action == 'dispose':
        r = fv.view(fin_ops.dispose(aid, d), False)
    elif action == 'impair':
        r = fv.view(fin_ops.impair(aid, d.get('amount'), d.get('reason')), False)
    elif action == 'transfer':
        r = fv.view(fin_ops.transfer(aid, d.get('to'), d.get('reason') or ''), False)
    elif action == 'verify':
        r = fin_ops.asset_view(fin_ops.verify(aid, bool(d.get('found')), d.get('condition'), d.get('note')))
    else:
        raise not_found('Unknown asset action.')
    return jsonify(r)


@bp.post('/assets/depreciation')
@require('add', 'fin')
def depreciation():
    d = body()
    v = fin_ops.depreciation_run(_co(d), d.get('ym'))      # need_ym() inside refuses a missing month
    return jsonify(fv.view(v, False) if v else {'nil': True})


# ---------- compliance & periods ----------
@bp.get('/compliance')
@require('view', 'fin')
def compliance():
    return jsonify(fin_ops.compliance(_co()))


@bp.post('/compliance/mark')
@require('edit', 'fin')
def compliance_mark():
    d = body()
    fin_ops.compliance_mark(d.get('key'), d.get('status'), d.get('ref'), d.get('note'))
    return jsonify(fin_ops.compliance(d['key'].split('|')[0]))


@bp.post('/periods/<action>')
@require('view', 'fin')
def periods(action):
    d = body()
    co = _co(d)
    if action == 'close':
        fin_ops.close_period(co, parse_date(d.get('date'), 'date'), d.get('reason'))
    elif action == 'reopen':
        fin_ops.reopen_period(co, parse_date(d.get('date'), 'date'), d.get('reason'))
    elif action == 'year':
        need_fresh_password()
        fin_ops.close_year(co, d.get('fy'), d.get('reason'), d.get('split'), d.get('acknowledge') is True)
    else:
        raise not_found('Unknown action.')
    return jsonify({'books_locked_to': str(db.val('SELECT books_locked_to FROM companies WHERE code=:c', c=co) or ''),
                    'fin_years': db.clean(db.q('SELECT * FROM fin_years WHERE company=:c ORDER BY from_date DESC', c=co))})


# ---------- year-end closing (2.6) ----------
@bp.get('/year-end')
@require('view', 'fin')
def year_end():
    from ..services import fin_yearend
    co = _co(request.args)
    return jsonify(_plain(fin_yearend.view(co, request.args.get('fy') or None)))


@bp.post('/year-end/<action>')
@require('view', 'fin')
def year_end_action(action):
    from ..services import fin_yearend
    d = body()
    co = _co(d)
    if action not in ('close', 'reopen'):
        raise not_found('Unknown action.')
    need_fresh_password()
    if action == 'close':
        fin_yearend.close(co, str(d.get('fy') or ''), d.get('reason'), d.get('split'), d.get('acknowledge') is True)
    else:
        fin_yearend.reopen(co, str(d.get('fy') or ''), d.get('reason'))
    return jsonify(_plain(fin_yearend.view(co, str(d.get('fy')))))


def _plain(x):
    import datetime as _dt
    from decimal import Decimal as _D
    if isinstance(x, dict):
        return {k: _plain(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_plain(v) for v in x]
    if isinstance(x, _D):
        return float(x)
    if isinstance(x, (_dt.date, _dt.datetime)):
        return x.isoformat()
    if isinstance(x, str) and x[:1] in '{[':
        try:
            import json as _j
            return _j.loads(x)
        except ValueError:
            return x
    return x


# ---------- masters ----------
MASTERS = {
    'cost_centres': ('cost_centres', ('id',), ('id', 'name', 'kind', 'active'), False),
    'tds_sections': ('tds_sections', ('code',), ('code', 'label', 'rate_ind', 'rate_other', 'single_limit', 'year_limit', 'monthly', 'slab', 'non_resident', 'tcs',
                                                 'no_pan_rate', 'stage', 'form', 'new_ref', 'effective_from', 'active'), False),
    'hsn_codes': ('hsn_codes', ('code',), ('code', 'kind', 'description', 'rate', 'uqc'), False),
    'party_tax': ('party_tax', ('party', 'company'), ('party', 'company', 'pan', 'resident', 'trc', 'section', 'ldc', 'priority', 'related'), True),
    'employees': ('employees', ('id',), ('id', 'company', 'name', 'branch_id', 'pan', 'gross', 'pf', 'esi', 'joined', 'active'), True),
    'bank_accounts': ('bank_accounts', ('id',), ('company', 'account', 'name', 'ifsc', 'branch_id'), True),
    'asset_categories': ('asset_categories', ('id',), ('id', 'name', 'account', 'life_years', 'it_rate'), False),
    'approval_rules': ('fin_approval_rules', ('id',), ('vtype', 'account', 'branch_id', 'above', 'role'), False),
    'budgets': ('budgets', ('company', 'fy', 'account'), ('company', 'fy', 'account', 'amount'), True),
    'recurring': ('recurring_journals', ('id',), ('company', 'name', 'kind', 'vtype', 'day', 'next_date', 'end_date', 'narration', 'lines', 'party_id', 'tds_section', 'active'), True),
    'gst_registrations': ('gst_registrations', ('id',), ('company', 'state', 'gstin', 'from_date', 'kind', 'einvoice'), True),
}


@bp.get('/masters/<name>')
@require('view', 'fin')
def master_list(name):
    if name not in MASTERS:
        raise not_found('Unknown master.')
    t, _, _, by_co = MASTERS[name]
    if by_co:                                      # company data (PANs, salaries, banks, budgets): only the user's companies
        co = request.args.get('company')
        if co:
            check_company(g.user, co)
        rows = db.q(f'SELECT * FROM `{t}` WHERE company IN :c', c=(co,) if co else tuple(g.user['companies']) or ('',))
    else:
        rows = db.q(f'SELECT * FROM `{t}`')
    return jsonify(db.clean(rows))


@bp.put('/masters/<name>')
@require('master', 'fin')
def master_save(name):
    """Insert or update one row: {row: {...}, reason}. Changes are written to the audit trail with the old and new values."""
    if name not in MASTERS:
        raise not_found('Unknown master.')
    t, pk, fields, by_co = MASTERS[name]
    d = body()
    row = {k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in (d.get('row') or {}).items() if k in fields or k in pk}
    if by_co:
        check_company(g.user, row.get('company'))
        if name in ('employees', 'party_tax', 'bank_accounts', 'recurring', 'gst_registrations', 'budgets'):
            k0 = {k: (d.get('row') or {}).get(k) for k in pk}
            cur = db.one(f'SELECT company FROM `{t}` WHERE ' + ' AND '.join(f'`{k}`=:{k}' for k in pk), **k0) if all(v not in (None, '') for v in k0.values()) else None
            if cur:
                check_company(g.user, cur['company'])      # cannot take over another company's row by its key
    if name == 'gst_registrations' and row.get('gstin') and not gstin_check(row['gstin'])['ok']:
        raise bad(f"GSTIN is not valid: {gstin_check(row['gstin'])['msg']}.", 'invalid')
    if name in ('party_tax', 'employees') and row.get('pan') and not pan_check(row['pan'])['ok']:
        raise bad(f"PAN is not valid: {pan_check(row['pan'])['msg']}.", 'invalid')
    key = {k: (d.get('row') or {}).get(k) for k in pk}
    old = db.one(f'SELECT * FROM `{t}` WHERE ' + ' AND '.join(f'`{k}`=:{k}' for k in pk), **key) if all(v not in (None, '') for v in key.values()) else None
    if old:
        sets = {k: v for k, v in row.items() if k not in pk}
        if sets:
            db.run(f'UPDATE `{t}` SET ' + ', '.join(f'`{k}`=:{k}' for k in sets) + ' WHERE ' + ' AND '.join(f'`{k}`=:pk_{k}' for k in pk),
                   **sets, **{f'pk_{k}': v for k, v in key.items()})
    else:
        db.insert(t, {k: v for k, v in row.items() if not (k == 'id' and v in (None, '') and name in ('bank_accounts', 'approval_rules', 'recurring', 'gst_registrations'))})
    audit.write(f'Finance master {"changed" if old else "added"}: {name}', json.dumps(key)[:60], old=db.clean(old) if old else None, new=row, reason=d.get('reason'),
                category='finance')
    return master_list(name)


@bp.delete('/masters/<name>')
@require('master', 'fin')
def master_delete(name):
    if name not in ('approval_rules', 'budgets', 'cost_centres', 'hsn_codes'):
        raise bad('This master cannot be deleted; switch it off instead.', 'not_allowed')
    t, pk, _, _ = MASTERS[name]
    key = {k: request.args.get(k) for k in pk}
    old = db.one(f'SELECT * FROM `{t}` WHERE ' + ' AND '.join(f'`{k}`=:{k}' for k in pk), **key)
    if not old or ('company' in old and old['company'] not in g.user['companies']):
        raise not_found('Row not found.')
    db.run(f'DELETE FROM `{t}` WHERE ' + ' AND '.join(f'`{k}`=:{k}' for k in pk), **key)
    audit.write(f'Finance master deleted: {name}', json.dumps(key)[:60], old=db.clean(old), reason=request.args.get('reason'), category='finance')
    return master_list(name)


@bp.put('/settings')
@require('master', 'fin')
def fin_settings():
    """Finance controls: GST interest / fees, TDS rates, attachment rules, always-approve types, duplicate window, three-way tolerance, number series."""
    d = body()
    v = d.get('value')
    if not isinstance(v, dict) or not v:
        raise bad('Send the settings to change as an object.', 'invalid')
    old = cfg()
    for k, x in v.items():                      # keep the shape: numbers stay numbers, groups stay groups
        if k not in old:
            continue
        if isinstance(old[k], dict):
            if not isinstance(x, dict) or any(not isinstance(y, (int, float)) or isinstance(y, bool) or y < 0 or y > 10 ** 9 for y in x.values()):
                raise bad(f'{k}: every value must be a number between 0 and 1,000,000,000.', 'invalid')
        elif isinstance(old[k], list):
            if not isinstance(x, list) or any(not isinstance(y, str) or len(y) > 40 for y in x):
                raise bad(f'{k} must be a list of short codes.', 'invalid')
        elif isinstance(old[k], (int, float)):
            if not isinstance(x, (int, float)) or isinstance(x, bool) or x < 0 or x > 10 ** 6:
                raise bad(f'{k} must be a number.', 'invalid')
    new = {**old, **{k: v[k] for k in v if k in old}}
    db.run('UPDATE settings SET svalue=:v WHERE skey=\'finance\'', v=json.dumps(new))
    audit.write('Finance settings changed', 'finance', old=old, new=new, reason=d.get('reason'), category='finance')
    return jsonify(cfg())
