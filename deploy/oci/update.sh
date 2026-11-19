#!/usr/bin/env bash
# Deploy a new release over an existing install with a database backup first and automatic rollback of the code
# if migrations or the health check fail.
#
#   sudo bash deploy/oci/update.sh            (run from the NEW unpacked release folder)
#
# Order: backup DB → keep previous code in /opt/kz-app.prev → copy new code → pip → build PWA → alembic upgrade → restart → health.
set -Eeuo pipefail
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
APP=/opt/kz-app; PREV=/opt/kz-app.prev; ENVF=/etc/kz/kz.env
step() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
[[ $EUID -eq 0 ]] || { echo "run with sudo" >&2; exit 1; }
[[ -d $APP && -f $ENVF ]] || { echo "No existing install; use install.sh" >&2; exit 1; }
set -a
# shellcheck source=/dev/null
. "$ENVF"
set +a

rollback() {
  echo "Update failed at line $1 — restoring the previous code. The database was backed up to $DUMP." >&2
  if [[ -d $PREV ]]; then rm -rf "$APP"; mv "$PREV" "$APP"; systemctl restart kz-api; systemctl restart kz-docs 2>/dev/null || true; fi
  echo "If a migration had already run, restore the dump or run 'alembic downgrade <previous revision>' (docs/MIGRATIONS.md)." >&2
  exit 1
}

step "Backup database before migrating"
DUMP=/var/lib/kz/pre-update-$(date +%Y%m%d-%H%M%S).sql.gz
MYSQL_PWD="$MIGRATE_DB_PASSWORD" mysqldump -h "$DB_HOST" -P "${DB_PORT:-3306}" -u "$MIGRATE_DB_USER" --single-transaction --routines --triggers --set-gtid-purged=OFF "$DB_NAME" | gzip > "$DUMP"
echo "saved $DUMP"
trap 'rollback $LINENO' ERR

step "Swap code (previous kept in $PREV)"
rm -rf "$PREV"; cp -a "$APP" "$PREV"
rsync -a --delete --exclude '.venv' --exclude 'node_modules' --exclude 'frontend/dist' --exclude 'docs-service/dist' --exclude 'docs-service/dist-test' --exclude '.env' --exclude '__pycache__' "$SRC/" "$APP/"
ln -sfn "$ENVF" "$APP/backend/.env"

step "Dependencies and PWA build"
"$APP/backend/.venv/bin/pip" install -q -r "$APP/backend/requirements.txt"
( cd "$APP/frontend" && npm ci --no-audit --no-fund --loglevel=error && npm run build && rm -rf node_modules )
( cd "$APP/docs-service" && npm ci --no-audit --no-fund --loglevel=error && npm run build && npm prune --omit=dev --no-audit --no-fund --loglevel=error )
grep -q '^DOCS_SECRET=.\+' "$ENVF" || { sed -i '/^DOCS_SECRET=/d' "$ENVF"; echo "DOCS_SECRET=$(python3 -c 'import secrets;print(secrets.token_hex(24))')" >> "$ENVF"; }
chown -R root:kz "$APP"; chmod -R g+rX,o-rwx "$APP"; chmod -R o+rX "$APP/frontend/dist"; chmod o+x "$APP" "$APP/frontend"
command -v restorecon &>/dev/null && restorecon -R "$APP/frontend/dist" || true

step "Migrations"
( cd "$APP/backend" && .venv/bin/alembic upgrade head && .venv/bin/alembic current )

step "Restart"
install -m 644 "$APP"/deploy/oci/systemd/kz-*.service "$APP"/deploy/oci/systemd/kz-*.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable kz-docs.service
systemctl restart kz-docs kz-api
systemctl reload nginx
"$APP/deploy/oci/healthcheck.sh"
trap - ERR
echo "Updated. Phones pick up the new version on next open (they show 'A new version is ready')."
