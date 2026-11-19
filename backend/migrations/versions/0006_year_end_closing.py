"""Release 2.6: year-end closing. A financial year now records how it was closed: the closing journal that moved the year's profit or
loss to capital / reserves, the profit, who closed it and why, a snapshot of the checklist and figures, and — when it is reopened —
who reopened it and why. Nothing else changes; existing years keep working (the new columns are empty).

Revision ID: 0006
Revises: 0005
Create Date: (release 2.6) 2026-11-11 00:40:00+05:30
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

DT6 = sa.DateTime().with_variant(mysql.DATETIME(fsp=6), 'mysql', 'mariadb')

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('fin_years', sa.Column('closing_journal', sa.String(length=30), nullable=True))
    op.add_column('fin_years', sa.Column('net_profit', sa.Numeric(precision=16, scale=2), nullable=True))
    op.add_column('fin_years', sa.Column('close_reason', sa.String(length=300), nullable=True))
    op.add_column('fin_years', sa.Column('close_summary', sa.JSON(), nullable=True))
    op.add_column('fin_years', sa.Column('reopened_by', sa.String(length=16), nullable=True))
    op.add_column('fin_years', sa.Column('reopened_at', DT6, nullable=True))
    op.add_column('fin_years', sa.Column('reopen_reason', sa.String(length=300), nullable=True))


def downgrade() -> None:
    for c in ('reopen_reason', 'reopened_at', 'reopened_by', 'close_summary', 'close_reason', 'net_profit', 'closing_journal'):
        op.drop_column('fin_years', c)
