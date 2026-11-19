"""Stock audit dashboard, the ten stock-audit reports, and Excel / PDF / CSV exports.

Every report takes from / to (audit date), company, branch, location_type and returns a list of flat rows, so the same
rows feed the screen, CSV and Excel. Values are hidden unless the user may see cost ('cost' or 'fin' on saudit)."""
import csv
import datetime as dt
import io
from decimal import Decimal

from .. import db
from ..core.rbac import can, scope_sql
from ..errors import bad
from .common import parse_date, today
from .stock_audit import PENDING, cfg

REPORTS = {
    'summary': 'Stock audit summary by date and location',
    'stock-vs-physical': 'Current stock vs physical stock',
    'item-variance': 'Item-wise variance',
    'location-variance': 'Location-wise shortage and excess',
    'damaged': 'Damaged / expired stock',
    'pending': 'Pending stock audits',
    'adjustments': 'Approved stock adjustments',
    'high-value': 'High-value variance alerts',
    'auditors': 'Auditor-wise audit completion',
    'accuracy': 'Stock accuracy percentage',
}
VALUE_COLS = {'system_value', 'physical_value', 'shortage_value', 'excess_value', 'damaged_value', 'variance_value', 'net_variance_value',
              'unit_cost', 'value', 'gross_variance_value', 'value_accuracy_pct'}


def _where(user, args, alias='a', closed_only=False):
    sc, p = scope_sql(user, f'{alias}.branch_id')
    w = [f"{alias}.company IN :cos", sc, f"{alias}.status <> 'Cancelled'"]
    p['cos'] = tuple(user['companies']) or ('',)
    f = args.get
    w.append(f'{alias}.audit_date BETWEEN :fd AND :td')
    p['fd'] = parse_date(f('from'), today().replace(day=1))
    p['td'] = parse_date(f('to'), today())
    for k in ('company', 'branch_id', 'location_type'):
        if f(k):
            w.append(f'{alias}.{k}=:{k}')
            p[k] = f(k)
    if f('branch'):
        w.append(f'{alias}.branch_id=:branch')
        p['branch'] = f('branch')
    if closed_only:
        w.append(f"{alias}.status IN ('Approved','Closed')")
    return ' AND '.join(w), p


def _loc():
    return 'JOIN branches b ON b.id=a.branch_id JOIN locations l ON l.code=b.location'


def report(key, user, args):
    if key not in REPORTS:
        raise bad('Unknown stock-audit report.', 'invalid')
    w, p = _where(user, args)
    counted = 'x.physical_qty IS NOT NULL'
    var = '(x.physical_qty - COALESCE(x.system_qty,0))'
    if key == 'summary':
        rows = db.q(f'''SELECT a.audit_date, l.name AS location, a.location_type, a.company, COUNT(*) AS audits,
                        SUM(a.status IN ('Approved','Closed')) AS approved, SUM(a.counted_count) AS items_counted, SUM(a.matched_count) AS items_matched,
                        SUM(a.system_value) AS system_value, SUM(a.physical_value) AS physical_value, SUM(a.shortage_value) AS shortage_value,
                        SUM(a.excess_value) AS excess_value, SUM(a.damaged_value) AS damaged_value
                        FROM stock_audits a {_loc()} WHERE {w} GROUP BY a.audit_date, l.name, a.location_type, a.company ORDER BY a.audit_date DESC, l.name''', **p)
    elif key == 'stock-vs-physical':
        rows = db.q(f'''SELECT a.no AS audit_no, a.audit_date, l.name AS location, x.item_id, i.name AS item, i.category, i.uom, x.batch_no,
                        x.system_qty, x.physical_qty, {var} AS variance_qty, x.unit_cost, x.system_qty*x.unit_cost AS system_value,
                        x.physical_qty*x.unit_cost AS physical_value, {var}*x.unit_cost AS variance_value, x.`condition`, x.remarks
                        FROM stock_audits a {_loc()} JOIN stock_audit_lines x ON x.audit_id=a.id JOIN items i ON i.id=x.item_id
                        WHERE {w} AND {counted} ORDER BY a.audit_date DESC, a.no, x.line_no LIMIT 20000''', **p)
    elif key == 'item-variance':
        rows = db.q(f'''SELECT x.item_id, i.name AS item, i.category, COUNT(DISTINCT a.id) AS audits, SUM(x.system_qty) AS system_qty,
                        SUM(x.physical_qty) AS physical_qty, SUM({var}) AS variance_qty, SUM({var}*x.unit_cost) AS variance_value,
                        SUM(CASE WHEN {var} < 0 THEN -{var}*x.unit_cost ELSE 0 END) AS shortage_value,
                        SUM(CASE WHEN {var} > 0 THEN {var}*x.unit_cost ELSE 0 END) AS excess_value
                        FROM stock_audits a JOIN stock_audit_lines x ON x.audit_id=a.id JOIN items i ON i.id=x.item_id
                        WHERE {w} AND {counted} GROUP BY x.item_id, i.name, i.category HAVING variance_qty <> 0
                        ORDER BY ABS(SUM({var}*x.unit_cost)) DESC LIMIT 5000''', **p)
    elif key == 'location-variance':
        rows = db.q(f'''SELECT l.name AS location, a.location_type, a.company, COUNT(*) AS audits, SUM(a.shortage_value) AS shortage_value,
                        SUM(a.excess_value) AS excess_value, SUM(a.excess_value - a.shortage_value) AS net_variance_value, SUM(a.damaged_value) AS damaged_value
                        FROM stock_audits a {_loc()} WHERE {w} GROUP BY l.name, a.location_type, a.company ORDER BY SUM(a.shortage_value) DESC''', **p)
    elif key == 'damaged':
        rows = db.q(f'''SELECT a.no AS audit_no, a.audit_date, l.name AS location, x.item_id, i.name AS item, x.`condition`, x.physical_qty AS qty,
                        x.unit_cost, x.physical_qty*x.unit_cost AS value, x.remarks
                        FROM stock_audits a {_loc()} JOIN stock_audit_lines x ON x.audit_id=a.id JOIN items i ON i.id=x.item_id
                        WHERE {w} AND x.`condition` IN ('Damaged','Expired') ORDER BY a.audit_date DESC''', **p)
    elif key == 'pending':
        p['today'] = today()
        rows = db.q(f'''SELECT a.no AS audit_no, a.audit_date, a.due_date, l.name AS location, a.audit_type, a.status, a.counted_count, a.lines_count,
                        DATEDIFF(:today, a.audit_date) AS age_days, (a.due_date < :today) AS overdue, a.approval_no
                        FROM stock_audits a {_loc()} WHERE {w} AND a.status IN :pend ORDER BY a.due_date''', pend=PENDING, **p)
    elif key == 'adjustments':
        rows = db.q(f'''SELECT s.no AS adjustment_no, a.no AS audit_no, s.created_at AS posted_at, l.name AS location, s.value, s.journal_no,
                        ua.name AS approved_by, up.name AS posted_by, s.reason,
                        (SELECT COUNT(*) FROM stock_adjustment_lines z WHERE z.adjustment_id=s.id) AS line_count
                        FROM stock_adjustments s JOIN stock_audits a ON a.no=s.audit_no {_loc()}
                        LEFT JOIN users ua ON ua.id=s.approved_by LEFT JOIN users up ON up.id=s.created_by
                        WHERE {w} ORDER BY s.created_at DESC''', **p)
    elif key == 'high-value':
        p['hv'] = Decimal(str(cfg()['high_value']))
        rows = db.q(f'''SELECT a.no AS audit_no, a.audit_date, l.name AS location, a.status, a.shortage_value, a.excess_value, a.damaged_value,
                        a.escalated, a.approval_no FROM stock_audits a {_loc()}
                        WHERE {w} AND (a.escalated=1 OR a.shortage_value >= :hv OR a.excess_value >= :hv OR a.damaged_value >= :hv)
                        ORDER BY GREATEST(a.shortage_value, a.excess_value, a.damaged_value) DESC''', **p)
    elif key == 'auditors':
        audits = db.q(f'SELECT a.auditors, a.status, a.submitted_at, a.due_date, a.counted_count, a.matched_count FROM stock_audits a WHERE {w}', **p)
        names = {r['id']: r['name'] for r in db.q('SELECT id, name FROM users')}
        agg = {}
        import json
        for r in audits:
            ids = r['auditors'] if isinstance(r['auditors'], list) else json.loads(r['auditors'] or '[]')
            done = r['status'] not in ('Draft', 'In Progress')
            for uid in ids:
                x = agg.setdefault(uid, {'auditor': names.get(uid, uid), 'assigned': 0, 'completed': 0, 'on_time': 0, 'overdue_open': 0, 'counted': 0, 'matched': 0})
                x['assigned'] += 1
                x['counted'] += r['counted_count']
                x['matched'] += r['matched_count']
                if done:
                    x['completed'] += 1
                    if r['submitted_at'] and r['submitted_at'].date() <= r['due_date']:
                        x['on_time'] += 1
                elif r['due_date'] < today():
                    x['overdue_open'] += 1
        rows = []
        for x in agg.values():
            x['completion_pct'] = round(100 * x['completed'] / x['assigned'], 1) if x['assigned'] else None
            x['accuracy_pct'] = round(100 * x['matched'] / x['counted'], 1) if x['counted'] else None
            rows.append(x)
        rows.sort(key=lambda r: -r['assigned'])
    else:  # accuracy
        rows = db.q(f'''SELECT l.name AS location, a.location_type, a.company, COUNT(*) AS audits, SUM(a.counted_count) AS items_counted,
                        SUM(a.matched_count) AS items_matched, SUM(a.system_value) AS system_value,
                        SUM(a.shortage_value + a.excess_value) AS gross_variance_value
                        FROM stock_audits a {_loc()} WHERE {w} AND a.counted_count > 0 GROUP BY l.name, a.location_type, a.company ORDER BY l.name''', **p)
        for r in rows:
            r['accuracy_pct'] = round(100 * float(r['items_matched']) / float(r['items_counted']), 1) if r['items_counted'] else None
            sv = float(r['system_value'] or 0)
            r['value_accuracy_pct'] = round(100 * (1 - float(r['gross_variance_value'] or 0) / sv), 1) if sv else None
    rows = db.clean([dict(r) for r in rows])
    if not (can(user, 'cost', 'saudit') or can(user, 'fin', 'saudit')):
        for r in rows:
            for k in list(r):
                if k in VALUE_COLS and k != 'value_accuracy_pct':
                    r[k] = None
    return rows


def dashboard(user, args):
    w, p = _where(user, args)
    t = db.one(f'''SELECT COUNT(*) audits, COALESCE(SUM(a.system_value),0) system_value, COALESCE(SUM(a.physical_value),0) physical_value,
                   COALESCE(SUM(a.shortage_value),0) shortage_value, COALESCE(SUM(a.excess_value),0) excess_value,
                   COALESCE(SUM(a.damaged_value),0) damaged_value, COALESCE(SUM(a.counted_count),0) counted, COALESCE(SUM(a.matched_count),0) matched
                   FROM stock_audits a WHERE {w}''', **p)
    sc, sp = scope_sql(user, 'branch_id')
    pend = db.one(f'''SELECT SUM(status IN ('Submitted','Under Review')) pending_approval, SUM(status IN ('Draft','In Progress')) pending_count,
                      SUM(status IN ('Draft','In Progress','Submitted','Under Review') AND due_date < CURDATE()) overdue, SUM(status='Approved') to_post
                      FROM stock_audits WHERE company IN :cos AND {sc}''', cos=tuple(user['companies']) or ('',), **sp)
    p6 = dict(p, fd=(today().replace(day=1) - dt.timedelta(days=150)).replace(day=1))
    trend = db.q(f'''SELECT DATE_FORMAT(a.audit_date, '%Y-%m') AS month, l.name AS location, SUM(a.excess_value - a.shortage_value) AS net_variance_value,
                     SUM(a.shortage_value) AS shortage_value, SUM(a.excess_value) AS excess_value
                     FROM stock_audits a {_loc()} WHERE {w.replace('a.audit_date BETWEEN :fd AND :td', 'a.audit_date >= :fd')}
                     GROUP BY month, l.name ORDER BY month, l.name''', **p6)
    out = {k: (float(v) if isinstance(v, Decimal) else v) for k, v in dict(t).items()}
    out.update({k: int(v or 0) for k, v in dict(pend).items()})
    out['accuracy_pct'] = round(100 * out['matched'] / out['counted'], 1) if out['counted'] else None
    out['trend'] = db.clean([dict(r) for r in trend])
    out['by_location'] = report('location-variance', user, args)
    if not (can(user, 'cost', 'saudit') or can(user, 'fin', 'saudit')):
        for k in ('system_value', 'physical_value', 'shortage_value', 'excess_value', 'damaged_value'):
            out[k] = None
        for r in out['trend']:
            r['net_variance_value'] = r['shortage_value'] = r['excess_value'] = None
    return out


# ---------------------------------------------------------------- file exports
def as_table(title, rows):
    """A list of report rows (dicts) as the common report table shape, for the PDF renderer (2.4)."""
    keys = list(rows[0].keys()) if rows else []

    def kind(k):
        vals = [r.get(k) for r in rows if r.get(k) not in (None, '')]
        if k in VALUE_COLS and not k.endswith('_pct'):
            return 'num'
        if k.endswith('_pct') or k.endswith('accuracy'):
            return 'pct'
        if vals and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in vals):
            return 'qty' if all(float(v).is_integer() for v in vals) else 'num'
        return 'text'
    cols = [{'h': k.replace('_', ' ').capitalize(), 't': kind(k)} for k in keys]
    return {'title': title, 'columns': cols, 'rows': [[r.get(k) for k in keys] for r in rows]}


def to_csv(rows):
    buf = io.StringIO()
    if rows:
        w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()), extrasaction='ignore')
        w.writeheader()
        w.writerows(rows)
    return buf.getvalue().encode('utf-8-sig')


def to_xlsx(sheets):
    """sheets: [(title, rows)] -> .xlsx bytes. Figures stay numbers; headers bold and frozen; columns sized."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter
    wb = Workbook()
    wb.remove(wb.active)
    for title, rows in sheets:
        ws = wb.create_sheet(title[:31])
        if not rows:
            ws.append(['No rows'])
            continue
        head = list(rows[0].keys())
        ws.append([h.replace('_', ' ').capitalize() for h in head])
        for c in ws[1]:
            c.font = Font(bold=True)
            c.fill = PatternFill('solid', fgColor='E6EEF8')
        for r in rows:
            ws.append([v if isinstance(v, (int, float)) or v is None else str(v) for v in (r.get(h) for h in head)])
        for i, h in enumerate(head, 1):
            ws.column_dimensions[get_column_letter(i)].width = min(48, max(10, len(h) + 2, *(len(str(r.get(h) or '')) + 1 for r in rows[:300])))
            if h in VALUE_COLS:
                for row in ws.iter_rows(min_row=2, min_col=i, max_col=i):
                    row[0].number_format = '#,##0.00'
        ws.freeze_panes = 'A2'
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def audit_sheets(d):
    head = [{'field': k, 'value': v} for k, v in [
        ('Audit reference', d['no']), ('Audit date', d['audit_date']), ('Created', d['created_at']), ('Company', d['company_name']),
        ('Location', f"{d['location_name']} ({d['location_type']})"), ('Stock area', d['stock_area']), ('Audit type', d['type_label']),
        ('Auditors', ', '.join(d['auditor_names'])), ('Supervisor', d['supervisor_name']), ('Status', d['status']),
        ('Verified by', d['verified_by_name']), ('Reviewed by', d['reviewed_by_name']), ('Approved by', d['approved_by_name']),
        ('Approval', d['approval_no']), ('Adjustment', d['adjustment_no']), ('Items counted', f"{d['counted_count']} of {d['lines_count']}"),
        ('Accuracy %', d['accuracy_pct']), ('System value', d['system_value']), ('Physical value', d['physical_value']),
        ('Shortage value', d['shortage_value']), ('Excess value', d['excess_value']), ('Damaged / expired value', d['damaged_value']),
        ('Instructions', d['instructions'])]]
    lines = [{k: ln[k] for k in ('line_no', 'item_id', 'name', 'category', 'sub', 'uom', 'batch_no', 'system_qty', 'physical_qty', 'variance_qty',
                                  'unit_cost', 'system_value', 'physical_value', 'variance_value', 'condition', 'status', 'remarks', 'counted_at')} for ln in d['lines']]
    events = [{'at': _ts(e['at']), 'user': e['user_name'], 'action': e['action'], 'detail': _detail(e['detail'])} for e in d['events']]
    return [('Audit', head), ('Lines', lines), ('Activity', events)]


def _ts(v):
    return str(v or '')[:16].replace('T', ' ')


def _detail(v):
    """Event detail JSON -> 'key: value · key: value' (same as the screen)."""
    import json
    if v in (None, ''):
        return ''
    try:
        v = json.loads(v) if isinstance(v, str) else v
    except ValueError:
        return str(v)
    if not isinstance(v, dict):
        return str(v)
    return ' · '.join(f"{k.replace('_', ' ')}: {', '.join(map(str, x)) if isinstance(x, list) else x}" for k, x in v.items() if x not in (None, '', []))


def audit_pdf(d):
    """Formal audit record (A4 landscape): header, totals, item lines with variance colouring, approval trail, signatures."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=12 * mm, rightMargin=12 * mm, topMargin=12 * mm, bottomMargin=12 * mm,
                            title=f"Stock audit {d['no']}", author='Kiswa-Zaibus Operations')
    ss = getSampleStyleSheet()
    small = ss['BodyText'].clone('small', fontSize=8, leading=10)
    fmt = lambda v: '' if v is None else f'{v:,.2f}' if isinstance(v, float) else str(v)  # noqa: E731
    story = [Paragraph(f"{d['company_name']} — Stock audit {d['no']}", ss['Title']),
             Paragraph(f"{d['type_label']} · {d['location_name']} ({d['location_type']}){' · ' + d['stock_area'] if d['stock_area'] else ''} · "
                       f"audit date {d['audit_date']} · status <b>{d['status']}</b>", ss['Normal']), Spacer(0, 4 * mm)]
    info = [['Auditors', ', '.join(d['auditor_names']), 'Supervisor', d['supervisor_name'] or ''],
            ['Verified by', f"{d['verified_by_name'] or ''} {_ts(d['verified_at'])}", 'Reviewed by', f"{d['reviewed_by_name'] or ''} {_ts(d['reviewed_at'])}"],
            ['Approved by', f"{d['approved_by_name'] or ''} {_ts(d['approved_at'])} {d['approval_no'] or ''}", 'Adjustment', d['adjustment_no'] or '—'],
            ['Counted', f"{d['counted_count']} of {d['lines_count']} items · {d['matched_count']} matched · accuracy {d['accuracy_pct'] or 0}%", 'Created',
             f"{_ts(d['created_at'])} by {d['created_by_name']}"]]
    if d['show_cost']:
        info.append(['Values (Rs)', f"system {fmt(d['system_value'])} · physical {fmt(d['physical_value'])}", 'Variance',
                     f"shortage {fmt(d['shortage_value'])} · excess {fmt(d['excess_value'])} · damaged/expired {fmt(d['damaged_value'])}"])
    t = Table(info, colWidths=[28 * mm, 105 * mm, 26 * mm, 110 * mm])
    t.setStyle(TableStyle([('FONTSIZE', (0, 0), (-1, -1), 8), ('TEXTCOLOR', (0, 0), (0, -1), colors.grey), ('TEXTCOLOR', (2, 0), (2, -1), colors.grey),
                           ('VALIGN', (0, 0), (-1, -1), 'TOP')]))
    story += [t, Spacer(0, 4 * mm)]
    head = ['#', 'Item', 'Category', 'UoM', 'Batch', 'System', 'Physical', 'Variance'] + (['Cost', 'Variance Rs'] if d['show_cost'] else []) + ['Condition', 'Remarks']
    data = [head]
    style = [('FONTSIZE', (0, 0), (-1, -1), 7.5), ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#E6EEF8')), ('GRID', (0, 0), (-1, -1), 0.25, colors.HexColor('#CDD5E0')),
             ('ALIGN', (5, 1), (9 if d['show_cost'] else 7, -1), 'RIGHT'), ('REPEATROWS', (0, 0), (-1, 0))]
    for i, ln in enumerate(d['lines'], 1):
        row = [ln['line_no'], Paragraph(f"{ln['name']} <font color='grey'>{ln['item_id']} {ln['sub']}</font>", small), ln['category'], ln['uom'], ln['batch_no'] or '',
               fmt(ln['system_qty']), fmt(ln['physical_qty']), fmt(ln['variance_qty'])]
        if d['show_cost']:
            row += [fmt(ln['unit_cost']), fmt(ln['variance_value'])]
        row += [ln['condition'], Paragraph(ln['remarks'] or '', small)]
        data.append(row)
        if ln['variance_qty']:
            style.append(('TEXTCOLOR', (7, i), (9 if d['show_cost'] else 7, i), colors.HexColor('#B4271F' if ln['variance_qty'] < 0 else '#137148')))
    widths = [8 * mm, 70 * mm, 26 * mm, 12 * mm, 18 * mm, 16 * mm, 16 * mm, 16 * mm] + ([18 * mm, 22 * mm] if d['show_cost'] else []) + [18 * mm, None]
    lt = Table(data, colWidths=widths, repeatRows=1)
    lt.setStyle(TableStyle(style))
    story += [lt, Spacer(0, 5 * mm), Paragraph('Activity and approval trail', ss['Heading3'])]
    ev = [['When', 'User', 'Action', 'Detail']] + [[_ts(e['at']), e['user_name'] or '', e['action'], Paragraph(_detail(e['detail'])[:400], small)]
                                                    for e in d['events']]
    et = Table(ev, colWidths=[34 * mm, 40 * mm, 55 * mm, None], repeatRows=1)
    et.setStyle(TableStyle([('FONTSIZE', (0, 0), (-1, -1), 7.5), ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#E6EEF8')),
                            ('GRID', (0, 0), (-1, -1), 0.25, colors.HexColor('#CDD5E0')), ('VALIGN', (0, 0), (-1, -1), 'TOP')]))
    story += [et, Spacer(0, 12 * mm)]
    sig = Table([['Auditor', 'Location in-charge', 'Accounts / Inventory', 'Approver'], ['', '', '', '']], colWidths=[66 * mm] * 4, rowHeights=[6 * mm, 14 * mm])
    sig.setStyle(TableStyle([('FONTSIZE', (0, 0), (-1, -1), 8), ('LINEABOVE', (0, 1), (-1, 1), 0, colors.white), ('LINEBELOW', (0, 1), (-1, 1), 0.5, colors.black)]))
    story.append(sig)

    def footer(c, _doc):
        c.setFont('Helvetica', 7)
        c.setFillColor(colors.grey)
        c.drawString(12 * mm, 7 * mm, f"Stock audit {d['no']} · printed {dt.datetime.now():%d-%m-%Y %H:%M} · page {c.getPageNumber()}")
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()


def count_sheet(d):
    """Blank count sheet (for printing or filling offline) — system quantities left out so counters count blind."""
    return [{'line_no': ln['line_no'], 'item_code': ln['item_id'], 'barcode': ln['barcode'], 'item': ln['name'], 'category': ln['category'],
             'size_class': ln['sub'], 'uom': ln['uom'], 'bin': ln['bin'], 'batch_no': ln['batch_no'], 'physical_qty': ln['physical_qty'],
             'condition': ln['condition'], 'remarks': ln['remarks']} for ln in d['lines']]

