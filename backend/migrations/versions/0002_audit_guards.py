"""Append-only audit trail: triggers that refuse UPDATE and DELETE on audit_log, and the chain head row.

Revision ID: 0002
Revises: 0001
"""
from alembic import op

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("INSERT INTO audit_head (id, last_id, hash) VALUES (1, 0, REPEAT('0', 64))")
    op.execute("""CREATE TRIGGER audit_log_no_update BEFORE UPDATE ON audit_log FOR EACH ROW
                  SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'audit_log is append-only: entries cannot be changed'""")
    op.execute("""CREATE TRIGGER audit_log_no_delete BEFORE DELETE ON audit_log FOR EACH ROW
                  SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'audit_log is append-only: entries cannot be deleted'""")


def downgrade() -> None:
    op.execute('DROP TRIGGER IF EXISTS audit_log_no_update')
    op.execute('DROP TRIGGER IF EXISTS audit_log_no_delete')
    op.execute('DELETE FROM audit_head WHERE id=1')
