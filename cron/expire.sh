#!/bin/sh
# Trueno — sweep TTL'd bucket directories.
#
# Buckets 1h / 1d / 1w are swept by mtime; the `keep` bucket is exempt.
# Run every 15min so worst-case overrun stays bounded.
#
# Install:
#   crontab -e
#   */15 * * * * /home/USER/cron/expire.sh >> /home/USER/cron/expire.log 2>&1
#
# Knobs (set via env in the crontab line, no need to edit the script):
#   DOCROOT         — Apache document root  (default: ~/public_html/your-domain)
#   EXPIRE_ACTION   — delete | move:<dir>   (default: delete)
#
# `move:<dir>` archives expired files into <dir>/<bucket>/<id>.<ext>
# instead of deleting. To restore one, move it back and touch its mtime:
#
#   mv <dir>/1h/AbCd.jpg ~/public_html/your-domain/files/1h/ && touch $_

set -eu

DOCROOT="${DOCROOT:-$HOME/public_html/your-domain}"
ACTION="${EXPIRE_ACTION:-delete}"
FILES_DIR="$DOCROOT/files"

# Per-bucket TTL in minutes. Must match _trueno.py BUCKETS values / 60.
TTL_1H=60
TTL_1D=1440
TTL_1W=10080

if [ ! -d "$FILES_DIR" ]; then
    echo "$(date -u +%FT%TZ) skip: $FILES_DIR not found"
    exit 0
fi

case "$ACTION" in
    delete)
        ARCHIVE_ROOT=""
        ;;
    move:*)
        ARCHIVE_ROOT="${ACTION#move:}"
        if [ -z "$ARCHIVE_ROOT" ]; then
            echo "$(date -u +%FT%TZ) error: EXPIRE_ACTION=move requires a path (e.g. move:/home/me/trueno-archive)"
            exit 2
        fi
        ;;
    *)
        echo "$(date -u +%FT%TZ) error: invalid EXPIRE_ACTION=$ACTION (use 'delete' or 'move:<dir>')"
        exit 2
        ;;
esac

# -mmin counts mtime in minutes; +N = strictly older than N. -delete and
# `-exec mv -t ... +` are atomic per-file; missing/locked files are skipped.
sweep() {
    bucket="$1"
    ttl_min="$2"
    src="$FILES_DIR/$bucket"
    [ -d "$src" ] || return 0
    if [ -z "$ARCHIVE_ROOT" ]; then
        n=$(find "$src" -type f -mmin "+$ttl_min" -print -delete | wc -l)
    else
        dst="$ARCHIVE_ROOT/$bucket"
        mkdir -p "$dst"
        n=$(find "$src" -type f -mmin "+$ttl_min" -print -exec mv -t "$dst" {} + | wc -l)
    fi
    echo "$(date -u +%FT%TZ) bucket=$bucket expired=$n action=$ACTION"
}

sweep 1h "$TTL_1H"
sweep 1d "$TTL_1D"
sweep 1w "$TTL_1W"
