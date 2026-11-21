"""Students: section up to 30 characters (the ERP has sections such as "OLYMPIAS" and "New Admission"; it was 5).

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-21
"""
from alembic import op
import sqlalchemy as sa

revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column('students', 'section', existing_type=sa.String(length=5), type_=sa.String(length=30), existing_nullable=True)


def downgrade() -> None:
    op.execute('UPDATE students SET section=LEFT(section, 5) WHERE CHAR_LENGTH(section) > 5')
    op.alter_column('students', 'section', existing_type=sa.String(length=30), type_=sa.String(length=5), existing_nullable=True)
