"""Gap-free document numbers per company, series and financial year (KG/PO/2627/0004).

The number_series row is locked FOR UPDATE inside the request transaction, so a number is only
consumed if the document is committed.
"""
import datetime as dt

from . import db


def fy(d=None):
    d = d or dt.date.today()
    y = d.year if d.month >= 4 else d.year - 1
    return f'{y % 100:02d}{(y + 1) % 100:02d}'


def next_no(company, series, d=None):
    f = fy(d)
    row = db.one('SELECT last_no FROM number_series WHERE company=:c AND series=:s AND fy=:f FOR UPDATE', c=company, s=series, f=f)
    if row is None:
        db.run('INSERT INTO number_series (company, series, fy, last_no) VALUES (:c, :s, :f, 0)', c=company, s=series, f=f)
        n = 1
    else:
        n = row['last_no'] + 1
    db.run('UPDATE number_series SET last_no=:n WHERE company=:c AND series=:s AND fy=:f', n=n, c=company, s=series, f=f)
    label = series.replace(':', '/')
    return f'{company}/{label}/{f}/{n:04d}' if company != 'HO' else f'{series}-{f}-{n:04d}'
