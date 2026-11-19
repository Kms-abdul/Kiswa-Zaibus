"""Load the go-live master data into an empty Kiswa-Zaibus database.

Usage:
    python seed.py                     # reads DB_* from ../backend/.env or the environment
    python seed.py --demo-students     # also loads the 35 cached MSEA student records
    python seed.py --check             # report what is loaded, change nothing

What it loads (from seed_data.json, exported from the approved prototype, Version 33):
    companies, locations, branches, regions, zones, number series
    37 roles with approval ranks, the permission matrix and approval limits
    17 users (temporary password, must change at first sign-in)
    chart of accounts (incl. 1115 Razorpay Clearing, 5850 Payment Gateway Charges)
    item categories, suppliers, 427 items, opening stock with an opening-stock journal
    schools, packages, alert rules, routing rules, settings (security, controls, online, ERP, Razorpay)

It refuses to run on a database that already has companies, so it can never overwrite live data.
"""
import argparse, datetime as dt, hashlib, json, os, re, sys
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import bcrypt
import pymysql

HERE = Path(__file__).resolve().parent
TYPE_MAP = {'Store': 'Store', 'School counter': 'Counter', 'Counter': 'Counter', 'Warehouse': 'Warehouse'}
READ_ONLY_ROLES = {'Auditor', 'Read-only Viewer'}
LIMIT_KEYS = ['disc', 'po', 'pay', 'refund', 'adj', 'exp', 'cash', 'credit', 'transfer', 'jv', 'dn', 'recon']
UNLIMITED = Decimal('999999999999.99')


def load_env():
    env = {}
    for p in (HERE.parent / 'backend' / '.env', HERE.parent / '.env'):
        if p.exists():
            for line in p.read_text(encoding='utf-8').splitlines():
                m = re.match(r'\s*([A-Z0-9_]+)\s*=\s*(.*)\s*$', line)
                if m and not line.lstrip().startswith('#'):
                    env.setdefault(m.group(1), m.group(2).strip().strip('"').strip("'"))
    for k, v in os.environ.items():
        if k.startswith('DB_') or k.startswith('SEED_'):
            env[k] = v
    return env


def money(x):
    return Decimal(str(x or 0)).quantize(Decimal('0.01'), ROUND_HALF_UP)


def digits(s):
    return re.sub(r'\D', '', s or '')


def zero_hash():
    return '0' * 64


class Seeder:
    def __init__(self, conn, data, temp_pw):
        self.c = conn
        self.d = data
        self.temp_pw = temp_pw
        self.cur = conn.cursor()
        self.cur.execute('SELECT hash FROM audit_head WHERE id=1 FOR UPDATE')
        row = self.cur.fetchone()
        self.prev_hash = row[0] if row else zero_hash()
        self.today = dt.date.today()

    # ---- helpers -------------------------------------------------------
    def ins(self, table, row, ignore=False):
        cols = ', '.join(f'`{k}`' for k in row)
        ph = ', '.join(['%s'] * len(row))
        vals = [json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v for v in row.values()]
        self.cur.execute(f"INSERT {'IGNORE ' if ignore else ''}INTO `{table}` ({cols}) VALUES ({ph})", vals)
        return self.cur.lastrowid

    def audit(self, action, ref, new_value=None, reason=None):
        # Same canonical form as backend/kz/audit.py so the chain verifies end to end.
        at = dt.datetime.now()
        user = 'System (installer)'
        body = json.dumps([self.prev_hash, at.isoformat(timespec='microseconds'), None, user, action, ref, None, new_value, reason],
                          ensure_ascii=False, separators=(',', ':'), default=str)
        h = hashlib.sha256(body.encode('utf-8')).hexdigest()
        rid = self.ins('audit_log', dict(at=at, user_name=user, role='System', action=action, ref=ref,
                                         new_value=new_value, reason=reason, category='setup', status='Done',
                                         prev_hash=self.prev_hash, hash=h))
        self.cur.execute('UPDATE audit_head SET last_id=%s, hash=%s WHERE id=1', (rid, h))
        self.prev_hash = h

    # ---- sections -------------------------------------------------------
    def organisation(self):
        d = self.d
        for co in d['companies'].values():
            self.ins('companies', dict(code=co['id'], name=co['name'], short_name=co['short'], gstin=co.get('gstin'),
                                       address=co.get('addr'), phone=co.get('phone'), email=co.get('email'),
                                       online_prefix=co.get('onlPrefix'), books_locked_to=co.get('lock')))
        for r in d['regions']:
            self.ins('regions', dict(id=r['id'], name=r['name']))
        zone_of = {}
        for z in d['zones']:
            self.ins('zones', dict(id=z['id'], name=z['name'], region_id=z['region']))
            for loc in z['locs']:
                zone_of[loc] = z['id']
        seen = set()
        central = set((d.get('online') or {}).get('wh', {}).values())
        for b in d['branches']:
            if b['loc'] in seen:
                continue
            seen.add(b['loc'])
            closed = b.get('closed') or {}
            self.ins('locations', dict(code=b['loc'], name=b['name'], public_name=b.get('pubName'), type=TYPE_MAP.get(b['type'], 'Store'),
                                       zone_id=zone_of.get(b['loc']), address=b.get('addr'), contact=b.get('contact'),
                                       is_central_wh=1 if b['id'] in central else 0, active=1 if b.get('active', True) else 0,
                                       closed_on=closed.get('date'), closed_reason=closed.get('reason'),
                                       stock_moved_to=(closed.get('to') or '').split('-')[-1] or None))
        for b in d['branches']:
            self.ins('branches', dict(id=b['id'], company=b['co'], location=b['loc'], code=b['code'], active=1 if b.get('active', True) else 0))
        fy = self.fy()
        for co in d['companies']:
            series = ['RV', 'PV', 'CV', 'JV', 'EXP', 'PO', 'GRN', 'PI', 'ST', 'ADJ', 'CN', 'DN', 'DLV', 'ONL', 'HOLD', 'OPN']
            series += [f"INV:{b['code']}" for b in d['branches'] if b['co'] == co]
            for s in series:
                self.ins('number_series', dict(company=co, series=s, fy=fy, last_no=0))
        self.ins('number_series', dict(company='HO', series='APR', fy=fy, last_no=0))
        self.audit('Organisation loaded', 'Setup', f"{len(d['companies'])} companies, {len(seen)} locations, {len(d['branches'])} branches")

    def fy(self):
        y = self.today.year if self.today.month >= 4 else self.today.year - 1
        return f"{y % 100:02d}{(y + 1) % 100:02d}"

    def access(self):
        d = self.d
        for r in d['roles']:
            self.ins('roles', dict(name=r['name'], description=r.get('desc'), approval_rank=r.get('rank', 0),
                                   read_only=1 if r['name'] in READ_ONLY_ROLES else 0, is_custom=0, active=1 if r.get('active', True) else 0))
        n = 0
        for role, screens in d['perms'].items():
            for screen, actions in screens.items():
                for a in sorted(set(actions)):
                    self.ins('permissions', dict(role=role, screen_key=screen, action=a), ignore=True)
                    n += 1
        for role, lim in d['limits'].items():
            for k in LIMIT_KEYS:
                v = lim.get(k, 0) or 0
                self.ins('approval_limits', dict(role=role, limit_key=k, amount=min(money(v), UNLIMITED)), ignore=True)
        for rr in d.get('routes') or []:
            self.ins('routing_rules', dict(txn_type=rr['type'], location=None if rr.get('loc') in (None, '*') else rr['loc'],
                                           up_to=rr.get('upto'), role=rr['role']))
        self.audit('Roles and permissions loaded', 'Setup', f"{len(d['roles'])} roles, {n} permission grants")

    def users(self):
        pw_hash = bcrypt.hashpw(self.temp_pw.encode(), bcrypt.gensalt(rounds=12)).decode()
        all_br = [b['id'] for b in self.d['branches'] if b.get('active', True)]
        for u in self.d['users']:
            status = u.get('status') or ('Active' if u.get('active', True) else 'Inactive')
            self.ins('users', dict(id=u['id'], emp_id=u['empId'], name=u['name'], mobile=digits(u['mobile'])[-10:], email=u.get('email'),
                                   department=u.get('dept'), designation=u.get('desig') or u.get('title'), main_role=u['role'],
                                   manager_id=None, status=status, join_date=u.get('join') or None,
                                   valid_from=u.get('validFrom') or None, valid_to=u.get('validTo') or None,
                                   all_branches=1 if u.get('brsAsg') == '*' else 0, pw_hash=pw_hash, pw_set_at=dt.datetime.now(), pw_must_change=1))
        for u in self.d['users']:   # managers and scope after everyone exists
            if u.get('mgr'):
                self.cur.execute('UPDATE users SET manager_id=%s WHERE id=%s', (u['mgr'], u['id']))
            for r in u.get('roles') or []:
                self.ins('user_roles', dict(user_id=u['id'], role=r), ignore=True)
            for co in u.get('cos') or []:
                self.ins('user_companies', dict(user_id=u['id'], company=co), ignore=True)
            if isinstance(u.get('brsAsg'), list):
                for b in u['brsAsg']:
                    if b in all_br:
                        self.ins('user_branches', dict(user_id=u['id'], branch_id=b), ignore=True)
            for z in u.get('zones') or []:
                self.ins('user_zones', dict(user_id=u['id'], zone_id=z), ignore=True)
            for r in u.get('regions') or []:
                self.ins('user_regions', dict(user_id=u['id'], region_id=r), ignore=True)
            self.ins('password_history', dict(user_id=u['id'], pw_hash=pw_hash))
        self.audit('Users loaded', 'Setup', f"{len(self.d['users'])} users with a temporary password; change required at first sign-in")

    def accounts(self):
        coa = {code: (code, name, grp) for code, name, grp in self.d['coa']}
        for co in self.d['companies']:
            for code, name, grp in sorted(coa.values()):
                self.ins('accounts', dict(company=co, code=code, name=name, grp=grp))
        self.audit('Chart of accounts loaded', 'Setup', f"{len(coa)} accounts per company")

    def masters(self):
        d = self.d
        for s in d['schools'].values():
            self.ins('schools', dict(id=s['id'], name=s['name'], board=s.get('board'), area=s.get('area'), active=1 if s.get('active', True) else 0))
        for c in d['itemCats']:
            self.ins('item_categories', dict(company=c['co'], name=c['name'], isbn_required=1 if c['name'] in ('Book', 'Textbook', 'Notebook') else 0), ignore=True)
        for s in d['suppliers']:
            self.ins('suppliers', dict(id=s['id'], company=s['co'], name=s['name'], gstin=s.get('gstin') or None, city=s.get('city') or None,
                                       terms_days=s.get('terms') or 30, contact=s.get('contact'), active=1 if s.get('active', True) else 0))
        cats = {(c['co'], c['name']) for c in d['itemCats']}
        for i in d['items']:
            if (i['co'], i['cat']) not in cats:
                self.ins('item_categories', dict(company=i['co'], name=i['cat']), ignore=True)
                cats.add((i['co'], i['cat']))
            self.ins('items', dict(id=i['id'], company=i['co'], name=i['name'], category=i['cat'], size=i.get('size'), colour=i.get('colour'),
                                   gender=i.get('gender'), class_name=i.get('cls'), term=i.get('term'), academic_year=i.get('ay'),
                                   school_id=i.get('school'), hsn=i.get('hsn'), gst_rate=i.get('gst') or 0, price=money(i.get('price')),
                                   cost=Decimal(str(i.get('cost') or 0)), barcode=i.get('barcode') or None, supplier_id=i.get('sup'),
                                   reorder_level=i.get('reorder') or 0, unpriced=1 if i.get('unpriced') else 0))
        for p in d['packages']:
            self.ins('packages', dict(id=p['id'], company=p['co'], name=p['name'], school_id=p.get('school'), gender=p.get('gender'),
                                      disc_pct=p.get('disc') or 0, active=1 if p.get('active', True) else 0))
            for n, ln in enumerate(p['lines'], 1):
                self.ins('package_lines', dict(package_id=p['id'], line_no=n, item_id=ln.get('i'), category=ln.get('cat'), qty=ln['q']))
        self.audit('Masters loaded', 'Setup', f"{len(d['items'])} items, {len(d['suppliers'])} suppliers, {len(d['packages'])} packages")

    def opening_stock(self):
        cost = {i['id']: Decimal(str(i.get('cost') or 0)) for i in self.d['items']}
        co_of = {i['id']: i['co'] for i in self.d['items']}
        active = {b['id'] for b in self.d['branches'] if b.get('active', True)}
        fy = self.fy()
        for co in self.d['companies']:
            lines, total, units = [], Decimal('0'), 0
            for br, bal in self.d['stock'].items():
                if br not in active or not br.startswith(co + '-'):
                    continue
                value = Decimal('0')
                for item, q in bal.items():
                    if not q or co_of.get(item) != co:
                        continue
                    self.ins('stock_balance', dict(branch_id=br, item_id=item, qty=int(q)))
                    self.ins('stock_ledger', dict(company=co, branch_id=br, item_id=item, txn_date=self.today, qty=int(q),
                                                  unit_cost=cost[item], txn_type='Opening stock', ref=f'{co}/OPN/{fy}/0001'))
                    value += cost[item] * int(q)
                    units += int(q)
                value = money(value)
                if value:
                    lines.append((br, value))
                    total += value
            if not lines:
                continue
            jid = self.ins('journals', dict(no=f'{co}/OPN/{fy}/0001', company=co, txn_date=self.today, posted_on=self.today, type='Journal',
                                            ref='Opening stock', narration='Opening stock at cost on go-live (migrated from the approved prototype)'))
            n = 0
            for br, v in lines:   # Dr Inventory / Cr Capital, branch by branch so each branch's books balance
                self.ins('journal_lines', dict(journal_id=jid, line_no=n + 1, account='1300', branch_id=br, debit=v, credit=0))
                self.ins('journal_lines', dict(journal_id=jid, line_no=n + 2, account='3000', branch_id=br, debit=0, credit=v))
                n += 2
            self.cur.execute("UPDATE number_series SET last_no=1 WHERE company=%s AND series='OPN' AND fy=%s", (co, fy))
            self.audit('Opening stock posted', f'{co}/OPN/{fy}/0001', f'{units} units, ₹{total:,.2f} at cost')

    def settings(self):
        d = self.d
        sec = dict(d['security'])
        sec.pop('tempPw', None)
        intg = json.loads(json.dumps(d['razorpay_erp']))
        erp = intg.get('erp', {})
        for k in ('keyMask', 'keyFp'):
            erp.pop(k, None)
        erp['url'] = ''                        # set in Access & Control Center after go-live
        rzp = intg.get('rzp', {})
        rzp['keys'] = {co: {'test': '', 'live': ''} for co in d['companies']}   # key ids live in .env / vault, never in seed data
        rzp['env'] = 'Test'
        rzp['webhookUrl'] = ''
        rows = {
            'security': sec, 'controls': d.get('controls') or {}, 'maker_checker': d.get('maker_checker') or {},
            'escalation': d.get('escalation') or {}, 'online': d.get('online') or {}, 'erp': erp, 'razorpay': rzp,
            'student_categories': d.get('studentCats') or [], 'cashiers': d.get('cashiers') or [], 'counters': d.get('counters') or [],
            'closed_locations': d.get('closedLocs') or {}, 'pay_modes': d.get('payModes') or [], 'tax_rates': d.get('taxRates') or [],
            'integrations_active': {'erp': False, 'razorpay': False},
        }
        for k, v in rows.items():
            self.ins('settings', dict(skey=k, svalue=v, updated_by=None))
        for name, r in (d.get('alertRules') or {}).items():
            self.ins('alert_rules', dict(alert_type=name, in_app=1 if r.get('inapp') else 0, email=1 if r.get('email') else 0,
                                         whatsapp=1 if r.get('wa') else 0, roles=r.get('roles') or []))
        self.audit('Settings loaded', 'Setup', ', '.join(rows))

    def students(self):
        for s in self.d['students']:
            self.ins('students', dict(adm_no=s['adm'], erp_ref=s['erp'], name=s['name'], gender=s.get('g'), class_name=s['cls'], section=s.get('sec'),
                                      parent_mobile=digits(s.get('mobile'))[-10:] or None, category=s.get('cat') or 'Regular',
                                      status=s.get('status') or 'Active', school_id=s.get('school'), campus=s.get('campus'),
                                      academic_year=s.get('ay'), synced_at=None))
        self.audit('Demo student records loaded', 'Setup', f"{len(self.d['students'])} students (replace with the live ERP sync)")


def connect(env, database=True):
    return pymysql.connect(host=env.get('DB_HOST', '127.0.0.1'), port=int(env.get('DB_PORT', 3306)), user=env.get('DB_USER', 'root'),
                           password=env.get('DB_PASSWORD', ''), database=env.get('DB_NAME', 'kiswa_zaibus') if database else None,
                           charset='utf8mb4', autocommit=False)


def check(conn):
    cur = conn.cursor()
    for t in ['companies', 'locations', 'branches', 'roles', 'permissions', 'users', 'accounts', 'items', 'stock_balance', 'journals', 'settings', 'audit_log', 'students']:
        cur.execute(f'SELECT COUNT(*) FROM `{t}`')
        print(f'  {t:<16}{cur.fetchone()[0]:>8}')
    cur.execute('SELECT j.company, SUM(l.debit), SUM(l.credit) FROM journal_lines l JOIN journals j ON j.id=l.journal_id GROUP BY j.company')
    for co, dr, cr in cur.fetchall():
        print(f'  books {co}: debit {dr:,.2f} credit {cr:,.2f} {"balanced" if dr == cr else "NOT BALANCED"}')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--demo-students', action='store_true', help='load the cached demo student records')
    ap.add_argument('--check', action='store_true', help='print row counts only')
    ap.add_argument('--data', default=str(HERE / 'seed_data.json'))
    a = ap.parse_args()
    env = load_env()
    conn = connect(env)
    if a.check:
        check(conn)
        return 0
    cur = conn.cursor()
    cur.execute('SELECT COUNT(*) FROM companies')
    if cur.fetchone()[0]:
        print('Seed skipped: the database already has companies (it never overwrites existing data).')
        check(conn)
        return 0
    data = json.loads(Path(a.data).read_text(encoding='utf-8'))
    s = Seeder(conn, data, env.get('SEED_TEMP_PASSWORD', 'Kiswa@2026'))
    try:
        s.organisation(); s.access(); s.users(); s.accounts(); s.masters(); s.opening_stock(); s.settings()
        if a.demo_students:
            s.students()
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    print('Seed complete.')
    check(conn)
    return 0


if __name__ == '__main__':
    sys.exit(main())
