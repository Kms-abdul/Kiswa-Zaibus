"""The permission catalogue: every module, screen and action that the code checks, in one place.

How permissions work
  * A role holds rows in `permissions(role, screen_key, action)`. Nothing about that storage changes.
  * A screen key is 'module' or 'module.screen'. `rbac.actions_for` uses the exact screen key if the role has it, otherwise
    the module key, so a module can be split into finer screens later without breaking older checks.
  * The Role Permissions screen shows modules as sections and screens as rows. Each screen gets a mode:
        None   nothing
        Read   view, print, pdf, export
        Write  Read + add, edit, submit, cancel, upload, import
        Full   Write + approve, reject, return, reverse, delete, restore, verify, review, post
        Custom any mix of the screen's own actions
    plus separate EXTRAS (see cost, finance figures, masters, manage users, configure reports, audit) that are never part of a mode.
  * Only actions a screen really checks are offered for it, so no tick does nothing.

Adding a new module or screen (see docs/DEVELOPER_GUIDE.md)
  1. add it below with the actions its code checks;
  2. protect the endpoints with require('<action>', '<key>') and the frontend with can('<action>', '<key>');
  3. restart the backend: the catalogue is synced into the database at start-up (or run `flask --app wsgi kz sync-permissions`).
     The new screen then appears on the Role Permissions screen for every role, ready to be given a mode.
"""
import json

from sqlalchemy import text

# every action string the code may check (rbac.ALL_ACTIONS, API validation)
ACTIONS = ('view', 'add', 'edit', 'submit', 'approve', 'reject', 'return', 'cancel', 'reverse', 'delete', 'restore', 'print', 'pdf', 'export',
           'import', 'upload', 'audit', 'fin', 'cost', 'master', 'cfgrep', 'users', 'verify', 'review', 'post')

READ = ('view', 'print', 'pdf', 'export')
WRITE = READ + ('add', 'edit', 'submit', 'cancel', 'upload', 'import')
FULL = WRITE + ('approve', 'reject', 'return', 'reverse', 'delete', 'restore', 'verify', 'review', 'post')
MODES = [('none', 'None', ()), ('read', 'Read', READ), ('write', 'Write', WRITE), ('full', 'Full', FULL)]

# sensitive rights that are ticked on their own, never through a mode
EXTRAS = {'cost': 'See cost and margin', 'fin': 'See finance figures', 'master': 'Edit masters', 'users': 'Manage users and roles',
          'cfgrep': 'Configure reports', 'audit': 'Audit access'}

MODULES = [
    ('sell', 'Sell'), ('stock', 'Stock'), ('purchase', 'Purchasing'), ('money', 'Money'), ('finance', 'Finance & tax'),
    ('reports', 'Reports'), ('approvals', 'Approvals'), ('control', 'Control'), ('setup', 'Setup'),
]

# key, module, label, actions the code checks for this key, extras the code checks
SCREENS = [
    ('pos', 'sell', 'Billing (POS)', ('view', 'add', 'edit', 'cancel', 'print', 'upload'), ('cost',)),
    ('msea', 'sell', 'MSEA sponsorship', ('view', 'add', 'cancel', 'upload'), ()),
    ('onl', 'sell', 'Online orders', ('view', 'edit', 'upload'), ('fin',)),
    ('inv', 'stock', 'Stock, items, transfers and adjustments', ('view', 'add', 'edit', 'cancel', 'upload'), ('cost', 'master')),
    ('saudit', 'stock', 'Stock audits', ('view', 'add', 'edit', 'submit', 'cancel', 'upload', 'print', 'pdf', 'export',
                                         'verify', 'review', 'post', 'restore'), ('cost', 'fin')),
    ('pur', 'purchase', 'Purchasing (requisitions, POs, receipts, bills)', ('view', 'add', 'edit', 'submit', 'cancel', 'upload'), ('master',)),
    ('cash', 'money', 'Cash custody', ('view', 'add', 'edit', 'approve', 'upload'), ()),
    ('acc', 'money', 'Accounts', ('view', 'add', 'approve', 'reverse', 'upload'), ('master', 'fin')),
    ('ctl.pay', 'money', 'Payment links and Razorpay', ('view', 'add', 'edit', 'cancel', 'approve'), ()),
    ('fin', 'finance', 'Finance & tax', ('view', 'add', 'edit', 'submit', 'cancel', 'import', 'upload', 'print', 'pdf', 'export',
                                         'approve', 'reverse', 'restore'), ('master', 'audit')),
    ('rep', 'reports', 'Reports', ('view', 'print', 'pdf', 'export'), ('fin', 'cost', 'cfgrep')),
    ('appr', 'approvals', 'Approvals', ('view', 'approve', 'upload'), ()),
    ('audit', 'control', 'Audit trail', ('view', 'export'), ('audit',)),
    ('ctl', 'control', 'Control (users and roles)', ('view',), ('users',)),
    ('ctl.users', 'control', 'Users', ('view',), ('users',)),
    ('ctl.roles', 'control', 'Roles (read-only list)', ('view',), ()),
    ('ctl.sec', 'control', 'Security log', ('view',), ()),
    ('ctl.int', 'control', 'ERP integration', ('view', 'import'), ()),
    ('set', 'setup', 'Settings and setup', ('view', 'edit'), ()),
]

# keys some roles hold from the seed data that no code checks; kept untouched on save, never offered
LEGACY_KEYS = {'dash', 'ctl.dash', 'ctl.scope', 'ctl.flow', 'ctl.alerts', 'ctl.sys'}

LIMITS = [('disc', 'Discount %'), ('po', 'Purchase order ₹'), ('pay', 'Supplier payment ₹'), ('refund', 'Refund ₹'), ('adj', 'Stock adjustment ₹'),
          ('exp', 'Expense ₹'), ('cash', 'Cash difference ₹'), ('credit', 'Credit sale ₹'), ('transfer', 'Stock transfer ₹'), ('jv', 'Manual journal ₹'),
          ('dn', 'Debit note ₹'), ('recon', 'Reconciliation write-off ₹')]


def screen_keys():
    return [s[0] for s in SCREENS]


def screen(key):
    return next((s for s in SCREENS if s[0] == key), None)


def allowed(key):
    """All actions (mode actions + extras) that may be granted on a screen key; empty set for an unknown key."""
    s = screen(key)
    return set(s[3]) | set(s[4]) if s else set()


def _check():
    keys = screen_keys()
    assert len(keys) == len(set(keys)), 'duplicate screen key'
    mods = {m for m, _ in MODULES}
    for key, mod, _, acts, extras in SCREENS:
        assert mod in mods, f'{key}: unknown module {mod}'
        assert set(acts) <= set(FULL), f'{key}: action outside the modes {set(acts) - set(FULL)}'
        assert set(extras) <= set(EXTRAS), f'{key}: unknown extra {set(extras) - set(EXTRAS)}'
    assert set(FULL) | set(EXTRAS) == set(ACTIONS), 'modes + extras must cover ACTIONS'


_check()


def catalog():
    """The catalogue as the Role Permissions screen needs it (labels and order may be overridden in perm_modules / perm_screens)."""
    return {
        'modules': [{'key': m, 'label': label, 'sort': i} for i, (m, label) in enumerate(MODULES)],
        'screens': [{'key': k, 'module': m, 'label': label, 'sort': i, 'actions': list(a), 'extras': list(e)} for i, (k, m, label, a, e) in enumerate(SCREENS)],
        'modes': [{'key': k, 'label': label, 'actions': list(a)} for k, label, a in MODES],
        'extras': [{'key': k, 'label': v} for k, v in EXTRAS.items()],
        'limits': [{'key': k, 'label': v} for k, v in LIMITS],
    }


def sync_catalog(conn):
    """Upsert the catalogue into perm_modules / perm_screens. Keys and actions follow the code; labels and order edited in the
    database are kept. Screens no longer in the code are marked inactive (their permission rows are left alone)."""
    x = lambda sql, **p: conn.execute(text(sql), p)  # noqa: E731
    for i, (key, label) in enumerate(MODULES):
        x('''INSERT INTO perm_modules (`key`, label, sort, active) VALUES (:k, :l, :s, 1)
             ON DUPLICATE KEY UPDATE active=1''', k=key, l=label, s=i)
    for i, (key, mod, label, acts, extras) in enumerate(SCREENS):
        x('''INSERT INTO perm_screens (`key`, module_key, label, sort, actions, extras, active) VALUES (:k, :m, :l, :s, :a, :e, 1)
             ON DUPLICATE KEY UPDATE module_key=:m, actions=:a, extras=:e, active=1''',
          k=key, m=mod, l=label, s=i, a=json.dumps(list(acts)), e=json.dumps(list(extras)))
    keys = screen_keys()
    conn.execute(text('UPDATE perm_screens SET active=0 WHERE `key` NOT IN (' + ', '.join(f':k{i}' for i in range(len(keys))) + ')'),
                 {f'k{i}': k for i, k in enumerate(keys)})
    mods = [m for m, _ in MODULES]
    conn.execute(text('UPDATE perm_modules SET active=0 WHERE `key` NOT IN (' + ', '.join(f':m{i}' for i in range(len(mods))) + ')'),
                 {f'm{i}': m for i, m in enumerate(mods)})
    return {'modules': len(MODULES), 'screens': len(SCREENS)}


def labels(conn):
    """Labels and order as stored (editable), falling back to the code."""
    try:
        mods = {r[0]: (r[1], r[2]) for r in conn.execute(text('SELECT `key`, label, sort FROM perm_modules WHERE active=1'))}
        scrs = {r[0]: (r[1], r[2]) for r in conn.execute(text('SELECT `key`, label, sort FROM perm_screens WHERE active=1'))}
    except Exception:          # table not there yet (before migration 0007)
        return catalog()
    c = catalog()
    for m in c['modules']:
        m['label'], m['sort'] = mods.get(m['key'], (m['label'], m['sort']))
    for s in c['screens']:
        s['label'], s['sort'] = scrs.get(s['key'], (s['label'], s['sort']))
    c['modules'].sort(key=lambda m: m['sort'])
    c['screens'].sort(key=lambda s: s['sort'])
    return c
