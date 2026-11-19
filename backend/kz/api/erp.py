"""ERP sync: CSV import, API pull, run history, failed records."""
from flask import Blueprint, g, jsonify, request

from .. import db
from ..core.rbac import body, require
from ..errors import bad
from ..services import erp as svc

bp = Blueprint('erp', __name__, url_prefix='/api/erp')


@bp.post('/import')
@require('import', 'ctl.int')
def import_file():
    f = request.files.get('file')
    if not f:
        raise bad('Choose the CSV file exported from the ERP.', 'file_required')
    return jsonify(svc.import_csv(f.read(), g.user['name'], dry_run=request.form.get('dry_run') == '1'))


@bp.post('/sync')
@require('import', 'ctl.int')
def sync():
    return jsonify(svc.pull_api(g.user['name'], body().get('since')))


@bp.get('/runs')
@require('view', 'ctl.int')
def runs():
    return jsonify(db.clean(db.q('SELECT * FROM erp_sync_runs ORDER BY id DESC LIMIT 100')))


@bp.get('/runs/<int:rid>/records')
@require('view', 'ctl.int')
def records(rid):
    st = request.args.get('status')
    sql = 'SELECT * FROM erp_sync_records WHERE run_id=:r'
    if st:
        sql += ' AND status=:s'
    return jsonify(db.clean(db.q(sql + ' ORDER BY id LIMIT 2000', r=rid, s=st)))


@bp.get('/template')
@require('view', 'ctl.int')
def template():
    return jsonify({'columns': svc.STUDENT_COLUMNS, 'example': {'adm_no': 'MSA260201', 'erp_ref': 'STU-00201', 'name': 'Asma Begum', 'gender': 'F',
                                                               'class_name': '6', 'section': 'A', 'parent_mobile': '9848012345', 'category': 'Regular',
                                                               'status': 'Active', 'school_id': 'MSA', 'campus': 'MSEA-MAIN', 'academic_year': '2026-27'}})
