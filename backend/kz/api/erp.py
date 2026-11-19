"""ERP sync: CSV import, API pull, run history, failed records; MCB Read-Only API connection and column mapping."""
from flask import Blueprint, g, jsonify, request

from .. import db
from ..core.rbac import body, require
from ..errors import bad
from ..services import erp as svc
from ..services import erp_api

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
    b = body()
    return jsonify(svc.pull_api(g.user['name'], b.get('since'), dry_run=bool(b.get('dry_run'))))


# ---------------------------------------------------------------- MCB Read-Only API: connection, views, column mapping
@bp.get('/api/status')
@require('view', 'ctl.int')
def api_status():
    return jsonify(erp_api.status())


@bp.get('/api/views')
@require('view', 'ctl.int')
def api_views():
    return jsonify(erp_api.list_views())


@bp.get('/api/views/<name>/schema')
@require('view', 'ctl.int')
def api_schema(name):
    return jsonify(erp_api.view_schema(name))


@bp.get('/api/mapping')
@require('view', 'ctl.int')
def api_mapping():
    return jsonify({'mapping': erp_api.load_mapping(), 'fields': [{'key': k, 'label': erp_api.LABELS[k], 'required': k in erp_api.REQUIRED}
                                                                    for k in svc.STUDENT_COLUMNS]})


@bp.put('/api/mapping')
@require('import', 'ctl.int')
def api_mapping_save():
    return jsonify({'mapping': erp_api.save_mapping(body().get('mapping'), g.user)})


@bp.post('/api/preview')
@require('import', 'ctl.int')
def api_preview():
    spec = body().get('mapping')
    if spec:
        spec = erp_api.validate_mapping(spec, erp_api.view_schema(spec.get('view') if isinstance(spec, dict) else None))
    else:
        spec = erp_api.load_mapping()
        if not spec:
            raise bad('Save or send a mapping first.', 'no_mapping')
    return jsonify(erp_api.preview(spec))


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
