#!/usr/bin/env bash
# Encrypted backup of the SmartPoli database (used by .github/workflows/db-backup.yml; runs the same anywhere).
#
#   DB_URL=postgresql://...  BACKUP_PASSPHRASE=...  backend/tools/db_backup.sh  out-dir
#
# Writes out-dir/smartpoli-<UTC time>.dump.gpg: a pg_dump (custom format, every table and row) encrypted with AES-256.
# The plain dump never leaves this machine and is deleted. The dump is checked readable before it is encrypted.
#
# Restore (into an EMPTY database):
#   gpg --batch --passphrase "$BACKUP_PASSPHRASE" -d smartpoli-XXXX.dump.gpg > smartpoli.dump
#   pg_restore --no-owner --no-privileges -d "$TARGET_DB_URL" smartpoli.dump
set -euo pipefail
: "${DB_URL:?DB_URL is required}"
: "${BACKUP_PASSPHRASE:?BACKUP_PASSPHRASE is required}"
out="${1:-.}"
mkdir -p "$out"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
plain="$(mktemp -d)/smartpoli-$stamp.dump"
trap 'rm -f "$plain"' EXIT

pg_dump "$DB_URL" --format=custom --no-owner --no-privileges --schema=public --file="$plain"
pg_restore --list "$plain" > /dev/null                       # a broken dump fails here, before anything is kept
tables=$(pg_restore --list "$plain" | grep -c " TABLE DATA public ")
gpg --batch --yes --pinentry-mode loopback --passphrase "$BACKUP_PASSPHRASE" --symmetric --cipher-algo AES256 \
    --output "$out/smartpoli-$stamp.dump.gpg" "$plain"
echo "backup ok: $tables tables, $(du -h "$out/smartpoli-$stamp.dump.gpg" | cut -f1) encrypted -> $out/smartpoli-$stamp.dump.gpg"
