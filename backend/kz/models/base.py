"""Shared declarative base and column types.

Conventions (see docs/DATABASE.md):
  * money is Numeric(16, 2) -> DECIMAL(16,2); unit costs Numeric(12, 4)
  * timestamps are DATETIME(6) in server local time (Asia/Kolkata)
  * JSON columns hold small structured values (settings, snapshots, line lists)
  * every table name is plural snake_case; every class name is singular CamelCase
"""
import datetime as dt
from decimal import Decimal

from sqlalchemy import JSON, Boolean, Date, DateTime, MetaData, Numeric, String, text
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import DeclarativeBase, mapped_column

NAMING = {
    'ix': 'ix_%(table_name)s_%(column_0_N_name)s',
    'uq': 'uq_%(table_name)s_%(column_0_N_name)s',
    'ck': 'ck_%(table_name)s_%(constraint_name)s',
    'fk': 'fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s',
    'pk': 'pk_%(table_name)s',
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)
    type_annotation_map = {
        Decimal: Numeric(16, 2),
        dt.datetime: DateTime().with_variant(mysql.DATETIME(fsp=6), 'mysql', 'mariadb'),
        dt.date: Date(),
        bool: Boolean(),
        dict: JSON(),
        list: JSON(),
    }

    def as_dict(self, *exclude):
        out = {}
        for c in self.__table__.columns:
            if c.key in exclude:
                continue
            v = getattr(self, c.key)
            if isinstance(v, Decimal):
                v = float(v)
            elif isinstance(v, (dt.datetime, dt.date, dt.time)):
                v = v.isoformat(sep=' ') if isinstance(v, dt.datetime) else v.isoformat()
            out[c.key] = v
        return out


DT6 = DateTime().with_variant(mysql.DATETIME(fsp=6), 'mysql', 'mariadb')
COST = Numeric(12, 4)


def created_at():
    return mapped_column(DT6, server_default=text('CURRENT_TIMESTAMP(6)'), nullable=False)


def code(n=16, **kw):
    return mapped_column(String(n), **kw)
