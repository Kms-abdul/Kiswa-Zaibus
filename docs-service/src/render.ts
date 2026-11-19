/* Rendering of one report table to .xlsx (exceljs) and .pdf (pdfkit). The input is what the Flask API sends:
   { title, subtitle?, columns: [{h, t}], rows: [[…]], totals?, note?, footer? }  — t is text | date | num | qty | pct. */
import ExcelJS from 'exceljs';
import PDFDocument from 'pdfkit';

export type ColType = 'text' | 'date' | 'num' | 'qty' | 'pct';
export interface Column { h: string; t: ColType }
export type Cell = string | number | boolean | null | undefined;
export interface Report {
  title: string;
  subtitle?: string;
  columns: Column[];
  rows: Cell[][];
  totals?: Cell[] | null;
  note?: string | null;
  footer?: string;
}

const MAX_ROWS = 100_000;

export function validate(b: unknown): Report {
  const r = b as Report;
  if (!r || typeof r !== 'object') throw new Error('Body must be a JSON object.');
  if (typeof r.title !== 'string' || !r.title.trim()) throw new Error('title is required.');
  if (!Array.isArray(r.columns) || !r.columns.length) throw new Error('columns must be a non-empty array.');
  for (const c of r.columns) if (typeof c?.h !== 'string' || !['text', 'date', 'num', 'qty', 'pct'].includes(c.t)) throw new Error('Each column needs h (text) and t (text|date|num|qty|pct).');
  if (!Array.isArray(r.rows)) throw new Error('rows must be an array.');
  if (r.rows.length > MAX_ROWS) throw new Error(`At most ${MAX_ROWS} rows.`);
  for (const row of r.rows) if (!Array.isArray(row)) throw new Error('Each row must be an array of cells.');
  return r;
}

const blank = (v: Cell) => v === null || v === undefined || v === '';
const inr = (v: number) => v.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export function fmt(v: Cell, t: ColType): string {
  if (blank(v)) return '';
  if (typeof v === 'number') {
    if (t === 'num') return inr(v);
    if (t === 'pct') return `${v}%`;
    if (t === 'qty') return v.toLocaleString('en-IN');
  }
  if (t === 'date' && typeof v === 'string' && /^\d{4}-\d{2}-\d{2}/.test(v)) return `${v.slice(8, 10)}-${v.slice(5, 7)}-${v.slice(0, 4)}`;
  return String(v);
}

export async function toXlsx(r: Report): Promise<Buffer> {
  const wb = new ExcelJS.Workbook();
  wb.creator = 'Kiswa-Zaibus Operations';
  wb.created = new Date();
  const ws = wb.addWorksheet(r.title.slice(0, 31).replace(/[\\/?*[\]:]/g, ' '), { views: [{ state: 'frozen', ySplit: 4 }] });
  ws.addRow([r.title]).font = { bold: true, size: 13 };
  ws.addRow([r.subtitle ?? '']).font = { color: { argb: 'FF5B6B7F' } };
  ws.addRow([]);
  const head = ws.addRow(r.columns.map(c => c.h));
  head.eachCell(c => { c.font = { bold: true, color: { argb: 'FFFFFFFF' } }; c.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FF1F3A5F' } }; c.alignment = { vertical: 'middle', wrapText: true }; });
  const asCell = (v: Cell, t: ColType): ExcelJS.CellValue => {
    if (blank(v)) return null;
    if (t === 'date' && typeof v === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(v)) return new Date(`${v}T00:00:00Z`);
    return typeof v === 'boolean' ? (v ? 'Yes' : 'No') : (v as string | number);
  };
  for (const row of r.rows) ws.addRow(r.columns.map((c, i) => asCell(row[i], c.t)));
  if (r.totals) {
    const t = ws.addRow(r.columns.map((c, i) => asCell(r.totals![i], c.t)));
    t.font = { bold: true };
    t.eachCell(c => { c.border = { top: { style: 'thin' } }; });
  }
  r.columns.forEach((c, i) => {
    const col = ws.getColumn(i + 1);
    if (c.t === 'num') col.numFmt = '#,##0.00';
    if (c.t === 'qty') col.numFmt = '#,##0';
    if (c.t === 'date') col.numFmt = 'dd-mm-yyyy';
    const widest = Math.max(c.h.length, ...r.rows.slice(0, 300).map(x => fmt(x[i], c.t).length));
    col.width = Math.min(48, Math.max(10, widest + 2));
  });
  if (r.note) { ws.addRow([]); ws.addRow([r.note]).font = { italic: true }; }
  ws.addRow([]);
  ws.addRow([r.footer ?? 'Kiswa-Zaibus Operations']).font = { size: 9, color: { argb: 'FF5B6B7F' } };
  return Buffer.from(await wb.xlsx.writeBuffer());
}

export function toPdf(r: Report): Promise<Buffer> {
  const wide = r.columns.length > 6;
  const doc = new PDFDocument({ size: 'A4', layout: wide ? 'landscape' : 'portrait', margin: 34, bufferPages: true, info: { Title: r.title, Author: 'Kiswa-Zaibus Operations' } });
  const chunks: Buffer[] = [];
  doc.on('data', (b: Buffer) => chunks.push(b));
  const done = new Promise<Buffer>(res => doc.on('end', () => res(Buffer.concat(chunks))));
  const left = doc.page.margins.left;
  const width = doc.page.width - left - doc.page.margins.right;
  // column widths: numbers get a fixed share, text columns share the rest by their typical length
  const weights = r.columns.map((c, i) => c.t === 'text' ? Math.min(30, Math.max(8, c.h.length, ...r.rows.slice(0, 100).map(x => fmt(x[i], c.t).length))) : 11);
  const sum = weights.reduce((a, b) => a + b, 0);
  const W = weights.map(w => (w / sum) * width);
  const FS = r.columns.length > 9 ? 6.5 : 7.5;
  const right = (t: ColType) => t === 'num' || t === 'qty' || t === 'pct';

  doc.font('Helvetica-Bold').fontSize(14).fillColor('#14202E').text(r.title, { width });
  if (r.subtitle) doc.font('Helvetica').fontSize(9).fillColor('#5B6B7F').text(r.subtitle, { width });
  doc.moveDown(0.6);

  const rowHeight = (cells: string[], bold: boolean) => {
    doc.font(bold ? 'Helvetica-Bold' : 'Helvetica').fontSize(FS);
    return Math.max(...cells.map((s, i) => doc.heightOfString(s || ' ', { width: W[i] - 6 }))) + 6;
  };
  const drawRow = (cells: string[], opts: { bold?: boolean; fill?: string } = {}) => {
    const h = rowHeight(cells, !!opts.bold);
    if (doc.y + h > doc.page.height - doc.page.margins.bottom - 18) { doc.addPage(); drawHead(); }
    const y = doc.y;
    if (opts.fill) doc.rect(left, y, width, h).fill(opts.fill);
    let x = left;
    doc.fillColor('#14202E').font(opts.bold ? 'Helvetica-Bold' : 'Helvetica').fontSize(FS);
    cells.forEach((s, i) => { doc.text(s, x + 3, y + 3, { width: W[i] - 6, align: right(r.columns[i].t) ? 'right' : 'left' }); x += W[i]; });
    doc.moveTo(left, y + h).lineTo(left + width, y + h).lineWidth(0.3).strokeColor('#C9D3DF').stroke();
    doc.x = left;
    doc.y = y + h;
  };
  const drawHead = () => drawRow(r.columns.map(c => c.h), { bold: true, fill: '#E8EEF5' });

  drawHead();
  r.rows.forEach((row, k) => drawRow(r.columns.map((c, i) => fmt(row[i], c.t)), { fill: k % 2 ? '#FAFBFC' : undefined }));
  if (r.totals) drawRow(r.columns.map((c, i) => fmt(r.totals![i], c.t)), { bold: true, fill: '#F3F5F8' });
  if (!r.rows.length) doc.font('Helvetica-Oblique').fontSize(9).fillColor('#5B6B7F').text('No rows for this period.', left, doc.y + 6);
  if (r.note) { doc.moveDown(0.5); doc.font('Helvetica-Oblique').fontSize(8).fillColor('#3A4A5E').text(r.note, left, doc.y, { width }); }

  const pages = doc.bufferedPageRange();
  for (let i = 0; i < pages.count; i++) {
    doc.switchToPage(pages.start + i);
    const y = doc.page.height - doc.page.margins.bottom + 6;
    doc.font('Helvetica').fontSize(7).fillColor('#5B6B7F');
    doc.text(r.footer ?? 'Kiswa-Zaibus Operations', left, y, { width: width / 2, lineBreak: false });
    doc.text(`Page ${i + 1} of ${pages.count}`, left + width / 2, y, { width: width / 2, align: 'right', lineBreak: false });
  }
  doc.end();
  return done;
}
