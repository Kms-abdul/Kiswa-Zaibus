"""Organisation, roles and permissions, dashboard, alerts."""
from flask import Blueprint, g, jsonify

from .. import db
from ..errors import not_found
from ..core.rbac import login_required, require, scope_sql

bp = Blueprint('org', __name__, url_prefix='/api')


@bp.get('/org')
@login_required
def org():
    u = g.user
    cos = db.clean(db.q('SELECT code, name, short_name, gstin, books_locked_to FROM companies WHERE code IN :c', c=tuple(u['companies']) or ('',)))
    where, p = scope_sql(u, 'b.id')
    branches = db.clean(db.q(f'''SELECT b.id, b.company, b.location, b.code, b.active, l.name, l.type, l.zone_id, l.closed_on
                                 FROM branches b JOIN locations l ON l.code=b.location WHERE {where} ORDER BY b.company, l.name''', **p))
    branches = [b for b in branches if b['company'] in u['companies']]
    locations = db.clean(db.q('SELECT * FROM locations ORDER BY active DESC, name'))
    return jsonify({'companies': cos, 'branches': branches, 'locations': locations,
                    'zones': db.q('SELECT * FROM zones ORDER BY name'), 'regions': db.q('SELECT * FROM regions')})


@bp.get('/roles')
@require('view', 'ctl.roles')
def roles():
    rows = db.clean(db.q('''SELECT r.*, (SELECT COUNT(*) FROM users u WHERE u.main_role=r.name AND u.status='Active') AS users
                            FROM roles r ORDER BY approval_rank DESC, name'''))
    return jsonify(rows)


@bp.get('/roles/<path:name>/permissions')
@require('view', 'ctl.roles')
def role_perms(name):
    if not db.val('SELECT COUNT(*) FROM roles WHERE name=:n', n=name):
        raise not_found('Role not found.')
    out = {}
    for r in db.q('SELECT screen_key, action FROM permissions WHERE role=:n ORDER BY screen_key', n=name):
        out.setdefault(r['screen_key'], []).append(r['action'])
    limits = {r['limit_key']: float(r['amount']) for r in db.q('SELECT limit_key, amount FROM approval_limits WHERE role=:n', n=name)}
    return jsonify({'role': name, 'permissions': out, 'limits': limits})
