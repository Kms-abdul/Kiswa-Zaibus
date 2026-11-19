"""Excel / PDF / CSV of a report table. Release 2.3 renders through the Node.js document service (kz-docs: Express + exceljs + pdfkit,
POST /render/xlsx | /render/pdf with a shared secret). When the service is not configured or does not answer, the same document is rendered
here with openpyxl / reportlab, so exports never fail because of it. The report shape is the one fin_reports returns:
{title, columns: [{h, t}], rows: [[…]], totals?, note?, company, period}."""
import csv
import io
import logging

import requests
from ..config import Config

log = logging.getLogger('kz.docs')


def payload(rep, meta=None):
    if not rep.get('columns'):                       # an empty report with no known columns still prints a readable page
        rep = {**rep, 'columns': [{'h': 'Result', 't': 'text'}], 'rows': [['No rows for this selection.']], 'totals': None}
    return {'title': rep['title'], 'subtitle': ' · '.join(x for x in ((meta or {}).get('company_name'), rep.get('period') and f"{rep['period']['from']} to {rep['period']['to']}") if x),
            'columns': rep['columns'], 'rows': rep['rows'], 'totals': rep.get('totals'), 'note': rep.get('note'),
            'footer': (meta or {}).get('footer') or 'Kiswa-Zaibus Operations · generated from the posted books'}


def _service(kind, body):
    url = (Config.DOCS_URL or '').rstrip('/')
    if not url:
        return None
    try:
        r = requests.post(f'{url}/render/{kind}', json=body, timeout=Config.DOCS_TIMEOUT,
                          headers={'X-KZ-Docs-Secret': Config.DOCS_SECRET or ''})
        if r.status_code == 200 and r.content:
            return r.content
        log.warning('kz-docs %s answered %s: %s', kind, r.status_code, r.text[:200])
    except requests.RequestException as e:
        log.info('kz-docs not reachable (%s); rendering in Python', e.__class__.__name__)
    return None


def _cell(v):
    return '' if v is None else v


def xlsx(rep, meta=None):
    body = payload(rep, meta)
    data = _service('xlsx', body)
    if data:
        return data, 'node'
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter
    wb = Workbook()
    ws = wb.active
    ws.title = body['title'][:31].replace('/', '-').replace(':', ' ')
    ws.append([body['title']])
    ws['A1'].font = Font(bold=True, size=13)
    ws.append([body['subtitle']])
    ws.append([])
    ws.append([c['h'] for c in body['columns']])
    for c in ws[4]:
        c.font = Font(bold=True, color='FFFFFF')
        c.fill = PatternFill('solid', fgColor='1F3A5F')
    for r in body['rows']:
        ws.append([_cell(v) for v in r])
    if body['totals']:
        ws.append([_cell(v) for v in body['totals']])
        for c in ws[ws.max_row]:
            c.font = Font(bold=True)
    for i, col in enumerate(body['columns'], 1):
        if col['t'] in ('num',):
            for row in ws.iter_rows(min_row=5, min_col=i, max_col=i):
                row[0].number_format = '#,##0.00'
        ws.column_dimensions[get_column_letter(i)].width = max(10, min(48, len(col['h']) + 4, *[len(str(r[i - 1] or '')) + 2 for r in body['rows'][:200]] or [10]))
    ws.freeze_panes = 'A5'
    if body['note']:
        ws.append([])
        ws.append([body['note']])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue(), 'python'


def pdf(rep, meta=None):
    body = payload(rep, meta)
    data = _service('pdf', body)
    if data:
        return data, 'node'
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    buf = io.BytesIO()
    wide = len(body['columns']) > 6
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4) if wide else A4, leftMargin=12 * mm, rightMargin=12 * mm, topMargin=12 * mm, bottomMargin=12 * mm,
                            title=body['title'], author='Kiswa-Zaibus Operations')
    ss = getSampleStyleSheet()
    small = ss['BodyText'].clone('small', fontSize=7.5, leading=9)

    def fmt(v, t):
        if v is None or v == '':
            return ''
        if t == 'num' and isinstance(v, (int, float)):
            return f'{v:,.2f}'
        if t == 'pct' and isinstance(v, (int, float)):
            return f'{v}%'
        return Paragraph(str(v).replace('&', '&amp;').replace('<', '&lt;'), small)
    types = [c['t'] for c in body['columns']]
    data_ = [[Paragraph(f"<b>{c['h']}</b>", small) for c in body['columns']]] + [[fmt(v, types[i]) for i, v in enumerate(r)] for r in body['rows']]
    if body['totals']:
        data_.append([fmt(v, types[i]) for i, v in enumerate(body['totals'])])
    t = Table(data_, repeatRows=1)
    st = [('GRID', (0, 0), (-1, -1), 0.25, colors.HexColor('#C9D3DF')), ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#E8EEF5')),
          ('VALIGN', (0, 0), (-1, -1), 'TOP'), ('FONTSIZE', (0, 0), (-1, -1), 7.5)]
    for i, ty in enumerate(types):
        if ty in ('num', 'qty', 'pct'):
            st.append(('ALIGN', (i, 1), (i, -1), 'RIGHT'))
    if body['totals']:
        st.append(('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#F3F5F8')))
    t.setStyle(TableStyle(st))
    story = [Paragraph(body['title'], ss['Title']), Paragraph(body['subtitle'] or '', ss['Normal']), Spacer(1, 6), t]
    if body['note']:
        story += [Spacer(1, 6), Paragraph(body['note'], small)]
    story += [Spacer(1, 8), Paragraph(body['footer'], small)]
    doc.build(story)
    return buf.getvalue(), 'python'


def csv_bytes(rep):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([c['h'] for c in rep['columns']])
    for r in rep['rows']:
        w.writerow([_cell(v) for v in r])
    if rep.get('totals'):
        w.writerow([_cell(v) for v in rep['totals']])
    return buf.getvalue().encode('utf-8-sig')


def health():
    url = (Config.DOCS_URL or '').rstrip('/')
    if not url:
        return {'configured': False, 'ok': False}
    try:
        r = requests.get(f'{url}/health', timeout=3)
        return {'configured': True, 'ok': r.status_code == 200, **(r.json() if r.headers.get('content-type', '').startswith('application/json') else {})}
    except requests.RequestException as e:
        return {'configured': True, 'ok': False, 'error': e.__class__.__name__}
