"""Evidence: supporting files (bills, slips, photos) attached to any document or approval by its number.

Files are stored under EVIDENCE_DIR/<yyyy>/<mm>/<sha256>.<ext> and never overwritten; replacing a file marks the old
one Superseded. Some approvals require evidence first (Settings -> controls.evBeforeApprove: exp, pay, adj by default).
"""
import datetime as dt
import hashlib
import mimetypes
from pathlib import Path

from flask import Blueprint, g, jsonify, request, send_file

from .. import db
from ..config import Config
from ..core import audit
from ..core.rbac import body, can, in_scope, login_required, require
from ..errors import bad, conflict, denied, not_found

bp = Blueprint('evidence', __name__, url_prefix='/api/evidence')
ALLOWED = {'.pdf', '.jpg', '.jpeg', '.png', '.webp', '.heic', '.xlsx', '.xls', '.csv', '.docx', '.doc', '.txt'}


def _can_attach(ref=None):
    u = g.user
    if not u['read_only'] and any(can(u, a, m) for m in ('pos', 'inv', 'pur', 'acc', 'cash', 'msea', 'onl', 'appr', 'saudit', 'fin') for a in ('upload', 'add')):
        return True
    # a read-only internal auditor named on an open stock audit may attach count photos to that audit's lines
    if ref and can(u, 'upload', 'saudit'):
        return bool(db.val('''SELECT COUNT(*) FROM stock_audits WHERE :r LIKE CONCAT(no, '/%') AND status IN ('Draft','In Progress')
                              AND JSON_CONTAINS(auditors, JSON_QUOTE(:u))''', r=ref, u=u['id']))
    return False


@bp.post('')
@login_required
def upload():
    f = request.files.get('file')
    ref = (request.form.get('ref') or '').strip()
    if not _can_attach(ref):
        raise denied('Your role cannot attach evidence.', 'forbidden')
    kind = (request.form.get('kind') or 'Other').strip()[:60]
    if not f or not f.filename:
        raise bad('Choose a file.', 'file_required')
    if not ref:
        raise bad('Say which document this evidence belongs to (its number).', 'ref_required')
    ext = Path(f.filename).suffix.lower()
    if ext not in ALLOWED:
        raise bad(f'File type {ext or "(none)"} is not accepted. Use PDF, image, Excel, Word, CSV or text.', 'bad_file_type')
    data = f.read()
    if not data:
        raise bad('The file is empty.', 'file_required')
    sha = hashlib.sha256(data).hexdigest()
    if db.val("SELECT COUNT(*) FROM evidence WHERE ref=:r AND sha256=:h AND status='Active'", r=ref, h=sha):
        raise conflict('This exact file is already attached to this document.', 'duplicate_evidence')
    now = dt.datetime.now()
    rel = Path(f'{now:%Y}') / f'{now:%m}' / f'{sha}{ext}'
    dest = Config.EVIDENCE_DIR / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        dest.write_bytes(data)
    company = request.form.get('company') if request.form.get('company') in g.user['companies'] else None
    eid = db.insert('evidence', dict(ref=ref, company=company, kind=kind, file_name=Path(f.filename).name[:255], mime_type=mimetypes.guess_type(f'x{ext}')[0] or 'application/octet-stream',
                                     size_bytes=len(data), storage_path=str(rel), sha256=sha, remarks=(request.form.get('remarks') or '')[:500] or None,
                                     uploaded_by=g.user['id']))
    audit.write('Evidence attached', ref, new=f'{kind}: {f.filename} ({len(data)} bytes, sha256 {sha[:12]})', category='evidence', company=company)
    return jsonify({'id': eid, 'ref': ref, 'sha256': sha}), 201


# Which document a reference belongs to: (screen key, company, branch). Evidence can be read only by people who may read that document.
OWNERS = (('fin', "SELECT company, branch_id AS branch FROM fin_vouchers WHERE no=:r"),
          ('pos', "SELECT company, branch_id AS branch FROM invoices WHERE no=:r"),
          ('pos', "SELECT n.company, i.branch_id AS branch FROM credit_notes n JOIN invoices i ON i.id=n.invoice_id WHERE n.no=:r"),
          ('saudit', "SELECT company, branch_id AS branch FROM stock_audits WHERE no=:r OR :r LIKE CONCAT(no, '/%') LIMIT 1"),
          ('inv', "SELECT company, branch_id AS branch FROM stock_adjustments WHERE no=:r"),
          ('inv', "SELECT company, from_branch AS branch FROM transfers WHERE no=:r"),
          ('pur', "SELECT company, branch_id AS branch FROM purchase_orders WHERE no=:r"),
          ('pur', "SELECT company, branch_id AS branch FROM supplier_bills WHERE bill_no=:r LIMIT 1"),
          ('pur', "SELECT company, NULL AS branch FROM supplier_payments WHERE no=:r"),
          ('pur', "SELECT company, NULL AS branch FROM debit_notes WHERE no=:r"),
          ('cash', "SELECT company, branch_id AS branch FROM cash_handovers WHERE no=:r"),
          ('cash', "SELECT company, NULL AS branch FROM owner_instructions WHERE no=:r"),
          ('msea', "SELECT company, NULL AS branch FROM msea_vouchers WHERE no=:r"),
          ('onl', "SELECT company, NULL AS branch FROM online_orders WHERE no=:r"),
          ('acc', "SELECT company, branch_id AS branch FROM receipts WHERE no=:r"),
          ('acc', "SELECT company, NULL AS branch FROM journals WHERE no=:r LIMIT 1"),
          ('appr', "SELECT company, branch_id AS branch FROM approvals WHERE no=:r"),
          ('fin', "SELECT company, branch_id AS branch FROM fixed_assets WHERE tag=:r OR no=:r LIMIT 1"))


def owner(ref):
    ref = ref or ''
    if ref.startswith('CMP:'):
        return 'fin', ref[4:].split('|')[0], None
    for key, q in OWNERS:
        r = db.one(q, r=ref)
        if r:
            return key, r['company'], r['branch']
    return None, None, None


def can_view(e):
    """May the signed-in user read this evidence row? The uploader always may; otherwise the owning document's rules apply:
    its company, its location (Head Office records need all-location access) and view rights on its screen."""
    u = g.user
    if e['uploaded_by'] == u['id']:
        return True
    key, company, branch = owner(e['ref'])
    company = company or e['company']
    if company and company not in u['companies']:
        return False
    if key is None:                                 # unknown document: company match plus a finance / accounts / audit role
        return company is not None and any(can(u, 'view', k) for k in ('fin', 'acc', 'appr'))
    if not can(u, 'view', key) and not can(u, 'approve', 'appr'):     # approvers must see the bills they approve
        return False
    if branch and key not in ('fin', 'acc') and not in_scope(u, branch):
        return False
    return True


@bp.get('')
@login_required
def for_ref():
    ref = request.args.get('ref')
    if not ref:
        return jsonify([])
    rows = db.q('''SELECT e.*, u.name AS uploaded_by_name FROM evidence e LEFT JOIN users u ON u.id=e.uploaded_by
                   WHERE e.ref=:r ORDER BY e.id DESC''', r=ref)
    return jsonify(db.clean([r for r in rows if can_view(r)]))


@bp.get('/<int:eid>/download')
@login_required
def download(eid):
    e = db.one('SELECT * FROM evidence WHERE id=:i', i=eid)
    if not e or not can_view(e):
        raise not_found('Evidence not found.', 'unknown_evidence')       # same answer as a missing file: ids reveal nothing
    path = Config.EVIDENCE_DIR / e['storage_path']
    if not path.exists():
        raise not_found('The file is missing from storage (check EVIDENCE_DIR and backups).', 'evidence_file_missing')
    db.insert('access_log', dict(user_id=g.user['id'], action='download', what=f"Evidence {eid}", detail=e['ref']))
    mime = mimetypes.guess_type(e['storage_path'])[0] or 'application/octet-stream'
    return send_file(path, mimetype=mime, as_attachment=True, download_name=e['file_name'])


@bp.post('/<int:eid>/verify')
@require('audit', 'audit')
def verify(eid):
    d = body()
    e = db.one('SELECT * FROM evidence WHERE id=:i FOR UPDATE', i=eid)
    if not e or not can_view(e):
        raise not_found('Evidence not found.', 'unknown_evidence')
    if e['uploaded_by'] == g.user['id']:
        raise denied('You cannot verify evidence you uploaded.', 'cannot_decide')
    st = 'Verified' if d.get('ok', True) else 'Rejected'
    if st == 'Rejected' and not str(d.get('remarks') or '').strip():
        raise bad('Give the reason.', 'reason_required')
    db.run('UPDATE evidence SET verify_status=:s, verified_by=:u, verified_at=NOW(6), verify_remarks=:r WHERE id=:i', s=st, u=g.user['id'], r=d.get('remarks'), i=eid)
    audit.write(f'Evidence {st.lower()}', e['ref'], new=e['file_name'], reason=d.get('remarks'), category='evidence')
    return jsonify({'id': eid, 'verify_status': st})


@bp.post('/<int:eid>/supersede')
@login_required
def supersede(eid):
    d = body()
    e = db.one('SELECT * FROM evidence WHERE id=:i FOR UPDATE', i=eid)
    if not e or not can_view(e):
        raise not_found('Evidence not found.', 'unknown_evidence')
    if e['status'] != 'Active':
        raise conflict('This file is already superseded.', 'wrong_status')
    if e['uploaded_by'] != g.user['id'] and not can(g.user, 'audit', 'audit'):
        raise denied('Only the uploader or an auditor can withdraw a file.', 'forbidden')
    if not str(d.get('reason') or '').strip():
        raise bad('Give the reason.', 'reason_required')
    db.run("UPDATE evidence SET status='Superseded' WHERE id=:i", i=eid)
    audit.write('Evidence superseded', e['ref'], old=e['file_name'], reason=d['reason'], category='evidence')
    return jsonify({'id': eid, 'status': 'Superseded'})
