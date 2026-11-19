"""Every API blueprint. Adding a module = create kz/api/<module>.py with `bp`, then list it here."""
from . import (accounts, approvals, audit_api, auth, cash, erp, evidence, inventory, masters, msea, online, org, payments, pos, purchasing,
               reports, settings, setup, shop, stock_audit, system, users, finance)

ALL_BLUEPRINTS = [m.bp for m in (system, auth, users, org, approvals, audit_api, masters, inventory, pos, msea, cash, accounts, purchasing, evidence, shop, online, payments, erp, reports, settings, setup, stock_audit, finance)]
