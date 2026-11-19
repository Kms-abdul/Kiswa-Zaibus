"""Approval routing: the first matching rule by type, location and value (smallest 'up to' wins); otherwise the default."""
from . import db


def route_role(txn_type, branch_id, amount, default):
    loc = None
    if branch_id:
        loc = db.val('SELECT location FROM branches WHERE id=:b', b=branch_id)
    rows = db.q('''SELECT role FROM routing_rules WHERE txn_type=:t AND (location IS NULL OR location=:l) AND (up_to IS NULL OR up_to >= :a)
                   ORDER BY up_to IS NULL, up_to LIMIT 1''', t=txn_type, l=loc, a=amount or 0)
    return rows[0]['role'] if rows else default
