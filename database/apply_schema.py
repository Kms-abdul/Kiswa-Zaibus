"""Create the database (if needed) and apply schema.sql. Safe to re-run: every statement is IF NOT EXISTS / INSERT IGNORE.

    python apply_schema.py                      # uses DB_* from ../backend/.env
    python apply_schema.py --create-db          # also CREATE DATABASE (needs a user allowed to create it)
"""
import argparse
import re
import sys
from pathlib import Path

import pymysql

sys.path.insert(0, str(Path(__file__).resolve().parent))
from seed import load_env  # noqa: E402

HERE = Path(__file__).resolve().parent


def statements(sql):
    """Split a .sql file that may contain DELIMITER blocks (triggers)."""
    out, delim, buf = [], ';', []
    for line in sql.splitlines():
        m = re.match(r'\s*DELIMITER\s+(\S+)\s*$', line, re.I)
        if m:
            delim = m.group(1)
            continue
        if not buf and (not line.strip() or line.strip().startswith('--')):
            continue
        buf.append(line)
        if line.rstrip().endswith(delim):
            stmt = '\n'.join(buf).rstrip()
            out.append(stmt[: -len(delim)].strip())
            buf = []
    if ''.join(buf).strip():
        out.append('\n'.join(buf).strip())
    return [s for s in out if s]


def apply(env, create_db=False, schema=HERE / 'schema.sql'):
    name = env.get('DB_NAME', 'kiswa_zaibus')
    conn = pymysql.connect(host=env.get('DB_HOST', '127.0.0.1'), port=int(env.get('DB_PORT', 3306)), user=env.get('DB_USER', 'root'),
                           password=env.get('DB_PASSWORD', ''), charset='utf8mb4', autocommit=True)
    cur = conn.cursor()
    if create_db:
        cur.execute(f'CREATE DATABASE IF NOT EXISTS `{name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci')
    cur.execute(f'USE `{name}`')
    n = 0
    for s in statements(Path(schema).read_text(encoding='utf-8')):
        cur.execute(s)
        n += 1
    cur.execute('SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=%s', (name,))
    tables = cur.fetchone()[0]
    conn.close()
    return n, tables


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--create-db', action='store_true')
    a = ap.parse_args()
    n, t = apply(load_env(), a.create_db)
    print(f'Schema applied: {n} statements, {t} tables.')
