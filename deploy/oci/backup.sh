#!/usr/bin/env bash
# Nightly backup: consistent MySQL dump + evidence files, uploaded to OCI Object Storage, local copies kept 7 days.
# Run by kz-backup.timer (01:17). Settings in /etc/kz/backup.env:
#   OCI_BUCKET=kz-backups          bucket in Object Storage (create it with a retention rule, e.g. 90 days)
#   OCI_NAMESPACE=<namespace>      oci os ns get
#   OCI_AUTH=instance_principal    let the instance's dynamic group write to the bucket (no keys on disk)
# Without OCI_BUCKET the backup stays local in /var/lib/kz/backups only.
#
# Restore (see docs/DATABASE.md → Restore):
#   gunzip -c kz-db-YYYYmmdd-HHMM.sql.gz | mysql -h $DB_HOST -u $MIGRATE_DB_USER -p $DB_NAME
#   tar -xzf kz-evidence-YYYYmmdd-HHMM.tar.gz -C /var/lib/kz
set -Eeuo pipefail
: "${DB_HOST:?}" "${DB_NAME:?}" "${MIGRATE_DB_USER:?}"
DIR=/var/lib/kz/backups; STAMP=$(date +%Y%m%d-%H%M); KEEP_DAYS=${KEEP_DAYS:-7}
install -d -m 700 "$DIR"

DB="$DIR/kz-db-$STAMP.sql.gz"
MYSQL_PWD="$MIGRATE_DB_PASSWORD" mysqldump -h "$DB_HOST" -P "${DB_PORT:-3306}" -u "$MIGRATE_DB_USER" --single-transaction --quick \
  --routines --triggers --set-gtid-purged=OFF "$DB_NAME" | gzip -9 > "$DB"
EV="$DIR/kz-evidence-$STAMP.tar.gz"
tar -czf "$EV" -C /var/lib/kz evidence
sha256sum "$DB" "$EV" > "$DIR/kz-$STAMP.sha256"
echo "backup: $(du -h "$DB" | cut -f1) database, $(du -h "$EV" | cut -f1) evidence"

if [[ -n ${OCI_BUCKET:-} ]]; then
  command -v oci >/dev/null || { echo "oci CLI missing: dnf install -y python39-oci-cli" >&2; exit 1; }
  for f in "$DB" "$EV" "$DIR/kz-$STAMP.sha256"; do
    oci os object put --auth "${OCI_AUTH:-instance_principal}" ${OCI_NAMESPACE:+--namespace "$OCI_NAMESPACE"} \
      --bucket-name "$OCI_BUCKET" --name "$(hostname -s)/$(basename "$f")" --file "$f" --force >/dev/null
  done
  echo "uploaded to bucket $OCI_BUCKET"
fi
find "$DIR" -type f -mtime +"$KEEP_DAYS" -delete
