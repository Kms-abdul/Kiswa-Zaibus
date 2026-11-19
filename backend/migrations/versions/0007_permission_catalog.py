"""Dynamic roles: permission catalogue tables (modules -> screens -> allowed actions) and a display name for roles.

perm_modules / perm_screens are filled from kz/core/permission_catalog.py (and re-synced at every start-up), so the Role
Permissions screen is built from data. The permissions table itself is unchanged.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-19
"""
from alembic import op
import sqlalchemy as sa

revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('roles', sa.Column('display_name', sa.String(length=80), nullable=True))
    op.create_table('perm_modules',
                    sa.Column('key', sa.String(length=40), nullable=False),
                    sa.Column('label', sa.String(length=80), nullable=False),
                    sa.Column('sort', sa.Integer(), server_default='0', nullable=False),
                    sa.Column('active', sa.Boolean(), server_default=sa.text('1'), nullable=False),
                    sa.PrimaryKeyConstraint('key', name=op.f('pk_perm_modules')))
    op.create_table('perm_screens',
                    sa.Column('key', sa.String(length=40), nullable=False),
                    sa.Column('module_key', sa.String(length=40), nullable=False),
                    sa.Column('label', sa.String(length=120), nullable=False),
                    sa.Column('sort', sa.Integer(), server_default='0', nullable=False),
                    sa.Column('actions', sa.JSON(), nullable=False),
                    sa.Column('extras', sa.JSON(), nullable=False),
                    sa.Column('active', sa.Boolean(), server_default=sa.text('1'), nullable=False),
                    sa.PrimaryKeyConstraint('key', name=op.f('pk_perm_screens')))
    from kz.core.permission_catalog import sync_catalog
    sync_catalog(op.get_bind())


def downgrade() -> None:
    op.drop_table('perm_screens')
    op.drop_table('perm_modules')
    op.drop_column('roles', 'display_name')
