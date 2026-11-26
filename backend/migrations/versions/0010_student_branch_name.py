"""Students: the branch (campus) name from the ERP next to the campus code, e.g. CMB = "MS Charminar Boys", and an index on campus
for the "Students purchased by branch" report.

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-26
"""
from alembic import op
import sqlalchemy as sa

revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('students', sa.Column('branch_name', sa.String(length=80), nullable=True))
    op.create_index('ix_students_campus', 'students', ['campus'])


def downgrade() -> None:
    op.drop_index('ix_students_campus', table_name='students')
    op.drop_column('students', 'branch_name')
