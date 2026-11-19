"""Go-live master data (seed/seed_data.json, exported from the approved prototype, Version 33).

`flask --app wsgi kz seed` loads it into an empty database (after `alembic upgrade head`). It refuses to run when
companies already exist, so it can never overwrite live data.
"""
import datetime as dt
import json
import re
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import bcrypt
from sqlalchemy import text

from .. import db
from ..core import audit
from ..core.numbering import fy as fy_of

SEED_FILE = Path(__file__).resolve().parents[2] / 'seed' / 'seed_data.json'
TYPE_MAP = {'Store': 'Store', 'School counter': 'Counter', 'Counter': 'Counter', 'Warehouse': 'Warehouse'}
READ_ONLY_ROLES = {'Auditor', 'Read-only Viewer'}
LIMIT_KEYS = ['disc', 'po', 'pay', 'refund', 'adj', 'exp', 'cash', 'credit', 'transfer', 'jv', 'dn', 'recon']
UNLIMITED = Decimal('999999999999.99')
CONTROL = {'1105', '1200', '1210', '1300', '1400', '2000', '2050', '2200', '2400'}
EXTRA_ACCOUNTS = [('1115', 'Razorpay Clearing', 'A'), ('5850', 'Payment Gateway Charges', 'X'), ('5650', 'Bank Charges', 'X')]
SYSTEM = {'id': None, 'name': 'System (installer)', 'main_role': 'System'}
ISBN_CATEGORIES = {'Book', 'Textbook', 'Workbook'}


def money(x):
    return Decimal(str(x or 0)).quantize(Decimal('0.01'), ROUND_HALF_UP)


def digits(s):
    return re.sub(r'\D', '', s or '')


def ins(table, row, ignore=False):
    cols = ', '.join(f'`{k}`' for k in row)
    ph = ', '.join(f':{k}' for k in row)
    vals = {k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v) for k, v in row.items()}
    return db.session().execute(text(f"INSERT {'IGNORE ' if ignore else ''}INTO `{table}` ({cols}) VALUES ({ph})"), vals).lastrowid


class Seeder:
    def __init__(self, data, temp_pw, demo_students=False):
        self.d = data
        self.temp_pw = temp_pw
        self.demo = demo_students
        self.today = dt.date.today()
        self.fy = fy_of(self.today)

    def run(self):
        if db.val('SELECT COUNT(*) FROM companies'):
            return False
        self.organisation(); self.access(); self.users(); self.accounts(); self.masters(); self.customers()
        self.opening_stock(); self.settings(); self.finance()
        if self.demo:
            self.students()
        return True

    def finance(self):
        from ..services.fin_bootstrap import bootstrap
        r = bootstrap(db.session().connection(), self.today)
        self.note('Finance & tax reference data loaded', f"{r['accounts']} finance accounts per company, TDS sections, HSN / SAC, cost centres, years")
        # demo tax profiles: PAN from each supplier's GSTIN (194Q for goods suppliers) and a small payroll for Form 130 / salary vouchers
        from ..services.fin_common import gstin_check
        for s in db.q('SELECT id, company, gstin FROM suppliers'):
            chk = gstin_check(s['gstin'] or '')
            db.run('INSERT IGNORE INTO party_tax (party, company, pan, resident, trc, section, priority, related) VALUES (:p, :c, :n, 1, 0, :s, \'Normal\', 0)',
                   p=s['id'], c=s['company'], n=chk['pan'] if chk['ok'] else None, s='194Q')
        people = [('E01', 'KG', 'Shabana Parveen', 'KG-ABD', 22000, 'ABQPS2310K'), ('E02', 'KG', 'Kiran Kumar', 'KG-ABD', 19500, 'BKJPK2317K'),
                  ('E03', 'KG', 'Ravi Kumar', 'KG-MSA', 21000, 'EKRPK2338K'), ('E04', 'KG', 'Raju Naik', 'KG-WH', 24000, None),
                  ('E05', 'KG', 'Srinivas Rao', 'KG-MYJ', 140000, 'HSRPR2359K'), ('E06', 'ZE', 'Imran Shaikh', 'ZE-ABD', 60000, 'LISPS2380K')]
        for eid, co, name, br, gross, pan in people:
            db.run('''INSERT IGNORE INTO employees (id, company, name, branch_id, pan, gross, pf, esi, joined, active)
                      VALUES (:i, :c, :n, :b, :p, :g, :pf, :esi, '2024-04-01', 1)''', i=eid, c=co, n=name, b=br, p=pan, g=gross, pf=gross <= 50000, esi=gross <= 21000)

    def note(self, action, value):
        audit.write(action, 'Setup', new=value, category='setup', status='Done', user=SYSTEM)

    def organisation(self):
        d = self.d
        for co in d['companies'].values():
            ins('companies', dict(code=co['id'], name=co['name'], short_name=co['short'], gstin=co.get('gstin'), address=co.get('addr'),
                                  phone=co.get('phone'), email=co.get('email'), online_prefix=co.get('onlPrefix'), books_locked_to=co.get('lock')))
        for r in d['regions']:
            ins('regions', dict(id=r['id'], name=r['name']))
        zone_of = {}
        for z in d['zones']:
            ins('zones', dict(id=z['id'], name=z['name'], region_id=z['region']))
            for loc in z['locs']:
                zone_of[loc] = z['id']
        seen = set()
        central = set((d.get('online') or {}).get('wh', {}).values())
        for b in d['branches']:
            if b['loc'] in seen:
                continue
            seen.add(b['loc'])
            closed = b.get('closed') or {}
            ins('locations', dict(code=b['loc'], name=b['name'], public_name=b.get('pubName'), type=TYPE_MAP.get(b['type'], 'Store'),
                                  zone_id=zone_of.get(b['loc']), address=b.get('addr'), contact=b.get('contact'),
                                  is_central_wh=1 if b['id'] in central else 0, active=1 if b.get('active', True) else 0,
                                  closed_on=closed.get('date'), closed_reason=closed.get('reason'),
                                  stock_moved_to=(closed.get('to') or '').split('-')[-1] or None))
        for b in d['branches']:
            ins('branches', dict(id=b['id'], company=b['co'], location=b['loc'], code=b['code'], active=1 if b.get('active', True) else 0))
        self.note('Organisation loaded', f"{len(d['companies'])} companies, {len(seen)} locations, {len(d['branches'])} branches")

    def access(self):
        d = self.d
        for r in d['roles']:
            ins('roles', dict(name=r['name'], description=r.get('desc'), approval_rank=r.get('rank', 0),
                              read_only=1 if r['name'] in READ_ONLY_ROLES else 0, is_custom=0, active=1 if r.get('active', True) else 0))
        n = 0
        from ..services.stock_audit import PERMS as SAUDIT
        perms = {r: dict(v) for r, v in d['perms'].items()}
        for role, acts in SAUDIT.items():      # stock-audit grants (screen 'saudit', release 2.2)
            if role in perms:
                perms[role]['saudit'] = acts
        for role, screens in perms.items():
            for screen, actions in screens.items():
                for a in sorted(set(actions)):
                    ins('permissions', dict(role=role, screen_key=screen, action=a), ignore=True)
                    n += 1
        for role, lim in d['limits'].items():
            for k in LIMIT_KEYS:
                ins('approval_limits', dict(role=role, limit_key=k, amount=min(money(lim.get(k, 0) or 0), UNLIMITED)), ignore=True)
        for rr in d.get('routes') or []:
            ins('routing_rules', dict(txn_type=rr['type'], location=None if rr.get('loc') in (None, '*') else rr['loc'], up_to=rr.get('upto'), role=rr['role']))
        self.note('Roles and permissions loaded', f"{len(d['roles'])} roles, {n} permission grants")

    def users(self):
        pw_hash = bcrypt.hashpw(self.temp_pw.encode(), bcrypt.gensalt(rounds=12)).decode()
        active_br = {b['id'] for b in self.d['branches'] if b.get('active', True)}
        for u in self.d['users']:
            ins('users', dict(id=u['id'], emp_id=u['empId'], name=u['name'], mobile=digits(u['mobile'])[-10:], email=u.get('email'),
                              department=u.get('dept'), designation=u.get('desig') or u.get('title'), main_role=u['role'],
                              status=u.get('status') or 'Active', join_date=u.get('join') or None, all_branches=1 if u.get('brsAsg') == '*' else 0,
                              pw_hash=pw_hash, pw_set_at=dt.datetime.now(), pw_must_change=1))
        for u in self.d['users']:
            if u.get('mgr'):
                db.run('UPDATE users SET manager_id=:m WHERE id=:i', m=u['mgr'], i=u['id'])
            for r in u.get('roles') or []:
                ins('user_roles', dict(user_id=u['id'], role=r), ignore=True)
            for co in u.get('cos') or []:
                ins('user_companies', dict(user_id=u['id'], company=co), ignore=True)
            if isinstance(u.get('brsAsg'), list):
                for b in u['brsAsg']:
                    if b in active_br:
                        ins('user_branches', dict(user_id=u['id'], branch_id=b), ignore=True)
            for z in u.get('zones') or []:
                ins('user_zones', dict(user_id=u['id'], zone_id=z), ignore=True)
            for r in u.get('regions') or []:
                ins('user_regions', dict(user_id=u['id'], region_id=r), ignore=True)
            ins('password_history', dict(user_id=u['id'], pw_hash=pw_hash))
        self.note('Users loaded', f"{len(self.d['users'])} users with a temporary password; change required at first sign-in")

    def accounts(self):
        coa = {c: (c, n, g) for c, n, g in self.d['coa']}
        for c, n, g in EXTRA_ACCOUNTS:
            coa.setdefault(c, (c, n, g))
        for co in self.d['companies']:
            for c, n, g in sorted(coa.values()):
                ins('accounts', dict(company=co, code=c, name=n, grp=g, is_control=1 if c in CONTROL else 0))
        self.note('Chart of accounts loaded', f'{len(coa)} accounts per company')

    def masters(self):
        d = self.d
        for s in d['schools'].values():
            ins('schools', dict(id=s['id'], name=s['name'], board=s.get('board'), area=s.get('area')))
        for c in d['itemCats']:
            ins('item_categories', dict(company=c['co'], name=c['name'], hsn=c.get('hsn'), gst_rate=c.get('gst') or 0,
                                        isbn_required=1 if c['name'] in ISBN_CATEGORIES else 0), ignore=True)
        for s in d['suppliers']:
            ins('suppliers', dict(id=s['id'], company=s['co'], name=s['name'], gstin=s.get('gstin') or None, city=s.get('city') or None,
                                  terms_days=s.get('terms') or 30, contact=s.get('contact'), active=1 if s.get('active', True) else 0))
        cats = {(c['co'], c['name']) for c in d['itemCats']}
        for i in d['items']:
            if (i['co'], i['cat']) not in cats:
                ins('item_categories', dict(company=i['co'], name=i['cat'], hsn=i.get('hsn'), gst_rate=i.get('gst') or 0,
                                            isbn_required=1 if i['cat'] in ISBN_CATEGORIES else 0), ignore=True)
                cats.add((i['co'], i['cat']))
            ins('items', dict(id=i['id'], company=i['co'], name=i['name'], category=i['cat'], size=i.get('size'), colour=i.get('colour'),
                              gender=i.get('gender'), class_name=i.get('cls'), term=i.get('term'), academic_year=i.get('ay'), school_id=i.get('school'),
                              hsn=i.get('hsn'), gst_rate=i.get('gst') or 0, price=money(i.get('price')), cost=Decimal(str(i.get('cost') or 0)),
                              barcode=i.get('barcode') or None, supplier_id=i.get('sup'), reorder_level=i.get('reorder') or 0,
                              bin_location=i.get('bin'), unpriced=1 if i.get('unpriced') else 0,
                              uom=i.get('uom') or ('Pair' if i['cat'] in ('Shoes', 'Socks') else 'Book' if i['co'] == 'ZE' else 'Nos')))
        for p in d['packages']:
            ins('packages', dict(id=p['id'], company=p['co'], name=p['name'], school_id=p.get('school'), gender=p.get('gender'), disc_pct=p.get('disc') or 0))
            for n, ln in enumerate(p['lines'], 1):
                ins('package_lines', dict(package_id=p['id'], line_no=n, item_id=ln.get('i'), category=ln.get('cat'), qty=ln['q']))
        self.note('Masters loaded', f"{len(d['items'])} items, {len(d['suppliers'])} suppliers, {len(d['packages'])} packages")

    def customers(self):
        for co in self.d['companies']:
            ins('customers', dict(id=f'{co}-WALKIN', company=co, type='Walk-in', name='Walk-in customer', receivable_account='1200'))
            ins('customers', dict(id=f'{co}-MSEA', company=co, type='Institution', name='MS Education Academy (sponsorships)', receivable_account='1210'))

    def opening_stock(self):
        from ..services.ledger import post
        cost = {i['id']: Decimal(str(i.get('cost') or 0)) for i in self.d['items']}
        co_of = {i['id']: i['co'] for i in self.d['items']}
        active = {b['id'] for b in self.d['branches'] if b.get('active', True)}
        for co in self.d['companies']:
            lines, units = [], 0
            ref = f'{co}/OPN/{self.fy}/0001'
            for br, bal in self.d['stock'].items():
                if br not in active or not br.startswith(co + '-'):
                    continue
                value = Decimal('0')
                for item, q in bal.items():
                    if not q or co_of.get(item) != co:
                        continue
                    ins('stock_balance', dict(branch_id=br, item_id=item, qty=int(q), reserved=0))
                    ins('stock_ledger', dict(company=co, branch_id=br, item_id=item, txn_date=self.today, qty=int(q), unit_cost=cost[item],
                                             txn_type='Opening stock', ref=ref))
                    value += cost[item] * int(q)
                    units += int(q)
                value = money(value)
                if value:
                    lines += [{'account': '1300', 'branch': br, 'debit': value}, {'account': '3000', 'branch': br, 'credit': value}]
            if lines:
                post(co, self.today, 'Opening', ref, 'Opening stock at cost on go-live (migrated from the approved prototype)', lines, force=True)
                total = sum(x.get('debit', 0) for x in lines)
                self.note('Opening stock posted', f'{co}: {units} units, Rs {total:,.2f} at cost')

    def settings(self):
        d = self.d
        sec = dict(d['security'])
        sec.pop('tempPw', None)
        intg = json.loads(json.dumps(d['razorpay_erp']))
        erp = intg.get('erp', {})
        for k in ('keyMask', 'keyFp'):
            erp.pop(k, None)
        erp['url'] = ''
        rzp = intg.get('rzp', {})
        rzp.pop('keys', None)
        rzp['env'] = 'Test'
        rzp['webhookUrl'] = ''
        controls = dict(d.get('controls') or {})
        controls.setdefault('returnDays', 7)
        online = dict(d.get('online') or {})
        rows = {
            'security': sec, 'controls': controls, 'maker_checker': d.get('maker_checker') or {}, 'escalation': d.get('escalation') or {},
            'online': online, 'erp': erp, 'razorpay': rzp, 'student_categories': d.get('studentCats') or [],
            'counters': d.get('counters') or [], 'closed_locations': d.get('closedLocs') or {}, 'integrations_active': {'erp': False, 'razorpay': False},
        }
        for k, v in rows.items():
            ins('settings', dict(skey=k, svalue=v))
        for name, r in (d.get('alertRules') or {}).items():
            ins('alert_rules', dict(alert_type=name, in_app=1 if r.get('inapp') else 0, email=1 if r.get('email') else 0,
                                    whatsapp=1 if r.get('wa') else 0, roles=r.get('roles') or []))
        self.note('Settings loaded', ', '.join(rows))

    def students(self):
        for s in self.d['students']:
            ins('students', dict(adm_no=s['adm'], erp_ref=s['erp'], name=s['name'], gender=s.get('g'), class_name=s['cls'], section=s.get('sec'),
                                 parent_mobile=digits(s.get('mobile'))[-10:] or None, category=s.get('cat') or 'Regular',
                                 status=s.get('status') or 'Active', school_id=s.get('school'), campus=s.get('campus'), academic_year=s.get('ay')))
        self.note('Demo student records loaded', f"{len(self.d['students'])} students (replace with the live ERP sync)")


def seed(temp_pw='Kiswa@2026', demo_students=False, path=SEED_FILE):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    return Seeder(data, temp_pw, demo_students).run()
