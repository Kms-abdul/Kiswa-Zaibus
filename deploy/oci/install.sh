#!/usr/bin/env bash
# Kiswa-Zaibus — first-time install on an OCI Compute instance running Oracle Linux 9 (x86_64 or Ampere A1 arm64).
#
#   sudo bash deploy/oci/install.sh --domain ops.example.in [--email it@example.in] [--seed] [--demo-students] [--local-mysql]
#
# Run it from the unpacked release folder (the folder that holds backend/, frontend/, deploy/). It is safe to re-run:
# every step checks what is already there. Steps:
#   1 packages  2 kz user + folders  3 copy release to /opt/kz-app  4 /etc/kz/kz.env  5 Python venv  6 build the PWA
#   7 database migrations (Alembic)  8 optional seed  9 systemd units  10 Nginx  11 SELinux  12 firewall  13 TLS (certbot)
# Logs: this script prints every step; the services log to journald (journalctl -u kz-api) and /var/log/kz.
set -Eeuo pipefail

DOMAIN=""; EMAIL=""; SEED=0; DEMO=0; LOCAL_MYSQL=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --domain) DOMAIN="$2"; shift 2 ;;
    --email) EMAIL="$2"; shift 2 ;;
    --seed) SEED=1; shift ;;
    --demo-students) DEMO=1; shift ;;
    --local-mysql) LOCAL_MYSQL=1; shift ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
APP=/opt/kz-app
ETC=/etc/kz
ENVF=$ETC/kz.env
step() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
fail() { printf '\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }
trap 'fail "line $LINENO: \"$BASH_COMMAND\" failed. Fix the cause and re-run; finished steps are skipped. See docs/DEPLOYMENT_OCI.md"' ERR

[[ $EUID -eq 0 ]] || fail "run with sudo"
[[ -f "$SRC/backend/wsgi.py" && -f "$SRC/frontend/package.json" ]] || fail "run from the unpacked release (backend/ and frontend/ not found under $SRC)"
grep -qE '^(ID="?ol"?|ID_LIKE=.*rhel)' /etc/os-release || echo "WARNING: written for Oracle Linux 9; continuing on $(grep -oP '^PRETTY_NAME="?\K[^"]+' /etc/os-release)"

step "1/13 Packages"
dnf -y -q install oracle-epel-release-el9 || true
dnf -y -q module enable nodejs:20 || true
dnf -y -q install python3.12 python3.12-pip python3.12-devel gcc nginx nodejs mysql policycoreutils-python-utils firewalld tar gzip rsync
dnf -y -q install certbot python3-certbot-nginx || echo "certbot not available; TLS step will be skipped"
if [[ $LOCAL_MYSQL -eq 1 ]]; then
  dnf -y -q install mysql-server
  systemctl enable --now mysqld
fi

step "2/13 Service user and folders"
id kz &>/dev/null || useradd --system --home-dir "$APP" --shell /sbin/nologin kz
install -d -o kz -g kz -m 750 /var/lib/kz /var/lib/kz/evidence /var/log/kz
install -d -o root -g kz -m 750 "$ETC"

step "3/13 Copy the release to $APP"
install -d -o root -g root -m 755 "$APP"
rsync -a --delete --exclude '.venv' --exclude 'node_modules' --exclude 'frontend/dist' --exclude 'docs-service/dist' --exclude 'docs-service/dist-test' --exclude '.env' --exclude '__pycache__' "$SRC/" "$APP/"
chown -R root:kz "$APP"; chmod -R g+rX,o-rwx "$APP"

step "4/13 Configuration $ENVF"
if [[ ! -f $ENVF ]]; then
  install -o root -g kz -m 640 "$APP/backend/.env.example" "$ENVF"
  sed -i "s|^SECRET_KEY=.*|SECRET_KEY=$(python3 -c 'import secrets;print(secrets.token_hex(32))')|" "$ENVF"
  [[ -n $DOMAIN ]] && sed -i "s|^PUBLIC_URL=.*|PUBLIC_URL=https://$DOMAIN|" "$ENVF"
  [[ $LOCAL_MYSQL -eq 1 ]] && sed -i "s|^DB_HOST=.*|DB_HOST=127.0.0.1|" "$ENVF"
  echo
  echo "Created $ENVF from the template. Fill in DB_PASSWORD, MIGRATE_DB_PASSWORD (and DB_HOST for HeatWave),"
  echo "create the database and accounts with deploy/oci/mysql-setup.sql, then run this script again."
  exit 0
fi
grep -q '^DB_PASSWORD=.\+' "$ENVF" || fail "DB_PASSWORD is empty in $ENVF"
grep -q '^SECRET_KEY=change-me' "$ENVF" && fail "SECRET_KEY in $ENVF is still the placeholder"
ln -sfn "$ENVF" "$APP/backend/.env"

step "5/13 Python virtual environment"
[[ -x $APP/backend/.venv/bin/python ]] || python3.12 -m venv "$APP/backend/.venv"
"$APP/backend/.venv/bin/pip" install -q --upgrade pip
"$APP/backend/.venv/bin/pip" install -q -r "$APP/backend/requirements.txt"

step "6/13 Build the PWA (frontend/dist)"
( cd "$APP/frontend" && npm ci --no-audit --no-fund --loglevel=error && npm run build )
rm -rf "$APP/frontend/node_modules"
# Node.js document service (kz-docs): production dependencies and the compiled server
( cd "$APP/docs-service" && npm ci --no-audit --no-fund --loglevel=error && npm run build && npm prune --omit=dev --no-audit --no-fund --loglevel=error )
grep -q '^DOCS_SECRET=.\+' "$ENVF" || { sed -i '/^DOCS_SECRET=/d' "$ENVF"; echo "DOCS_SECRET=$(python3 -c 'import secrets;print(secrets.token_hex(24))')" >> "$ENVF"; }
chown -R root:kz "$APP"; chmod -R g+rX,o-rwx "$APP"; chmod -R o+rX "$APP/frontend/dist"; chmod o+x "$APP" "$APP/frontend"

step "7/13 Database migrations"
set -a
# shellcheck source=/dev/null
. "$ENVF"
set +a
( cd "$APP/backend" && .venv/bin/alembic upgrade head && .venv/bin/alembic current )

if [[ $SEED -eq 1 ]]; then
  step "8/13 Seed master data (companies, locations, roles, users, items, opening stock)"
  TEMP_PW="Kz#$(python3 -c 'import secrets;print(secrets.token_urlsafe(9))')"
  args=(--temp-password "$TEMP_PW"); [[ $DEMO -eq 1 ]] && args+=(--demo-students)
  ( cd "$APP/backend" && sudo -u kz -E .venv/bin/flask --app wsgi kz seed "${args[@]}" )
  echo
  echo "Temporary password for every seeded user (shown once, not stored): $TEMP_PW"
  echo "Each user must change it at first sign-in (OTP to their mobile). Reset one user: flask --app wsgi kz set-password EMP_ID"
else
  step "8/13 Seed skipped (use --seed on the first install only)"
fi

step "9/13 systemd services"
install -m 644 "$APP"/deploy/oci/systemd/kz-*.service "$APP"/deploy/oci/systemd/kz-*.timer /etc/systemd/system/
chmod 750 "$APP/deploy/oci/"*.sh
systemctl daemon-reload
systemctl enable --now kz-docs.service kz-api.service kz-tick.timer kz-backup.timer
systemctl restart kz-docs.service kz-api.service

step "10/13 Nginx"
install -m 644 "$APP/deploy/oci/nginx/kz_proxy.conf" "$APP/deploy/oci/nginx/kz_headers.conf" /etc/nginx/
sed "s|__DOMAIN__|${DOMAIN:-_}|" "$APP/deploy/oci/nginx/kz.conf" > /etc/nginx/conf.d/kz.conf
nginx -t
systemctl enable --now nginx
systemctl reload nginx

step "11/13 SELinux"
if command -v getenforce &>/dev/null && [[ $(getenforce) != Disabled ]]; then
  setsebool -P httpd_can_network_connect 1
  semanage fcontext -a -t httpd_sys_content_t "$APP/frontend/dist(/.*)?" 2>/dev/null || semanage fcontext -m -t httpd_sys_content_t "$APP/frontend/dist(/.*)?"
  restorecon -R "$APP/frontend/dist"
fi

step "12/13 Firewall (also open 80/443 in the OCI security list / NSG)"
systemctl enable --now firewalld
firewall-cmd -q --permanent --add-service=http --add-service=https
firewall-cmd -q --reload

step "13/13 TLS certificate"
if [[ -n $DOMAIN ]] && command -v certbot &>/dev/null; then
  if [[ -n $EMAIL ]]; then mail_args=(-m "$EMAIL"); else mail_args=(--register-unsafely-without-email); fi
  certbot --nginx -n --agree-tos --redirect -d "$DOMAIN" "${mail_args[@]}" || echo "certbot failed: point DNS for $DOMAIN to this instance and re-run, or put the OCI Load Balancer with a certificate in front."
else
  echo "Skipped (no --domain). Set COOKIE_SECURE=0 in $ENVF only for a temporary http test."
fi

"$APP/deploy/oci/healthcheck.sh" || fail "health check failed — see 'journalctl -u kz-api -n 100' and docs/TROUBLESHOOTING.md"
printf '\n\033[1;32mInstalled.\033[0m Open https://%s  (parents: https://%s/shop)\n' "${DOMAIN:-<server-ip>}" "${DOMAIN:-<server-ip>}"
