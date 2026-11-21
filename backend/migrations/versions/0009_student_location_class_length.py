"""Students: a location (city / state from the ERP, e.g. Hyderabad, Maharashtra) and class names up to 60 characters
(college classes such as "B.Com (Comp) - I Year - Semester 1" did not fit in 20). Items and packages get the same class width so a
book or package can still be matched to such a class.

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-21
"""
from alembic import op
import sqlalchemy as sa

revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('students', sa.Column('location', sa.String(length=60), nullable=True))
    op.alter_column('students', 'class_name', existing_type=sa.String(length=20), type_=sa.String(length=60), existing_nullable=False)
    op.alter_column('items', 'class_name', existing_type=sa.String(length=20), type_=sa.String(length=60), existing_nullable=True)
    op.alter_column('packages', 'class_name', existing_type=sa.String(length=20), type_=sa.String(length=60), existing_nullable=True)


def downgrade() -> None:
    for table, nullable in (('students', False), ('items', True), ('packages', True)):
        op.execute(f'UPDATE {table} SET class_name=LEFT(class_name, 20) WHERE CHAR_LENGTH(class_name) > 20')
        op.alter_column(table, 'class_name', existing_type=sa.String(length=60), type_=sa.String(length=20), existing_nullable=nullable)
    op.drop_column('students', 'location')
