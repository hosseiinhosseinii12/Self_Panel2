#!/bin/sh
set -e

if [ -d "/data" ]; then
    echo "[entrypoint] Fixing /data ownership..."
    chown -R selfbot:selfbot /data 2>/dev/null || true
    mkdir -p /data/selfbot/logs /data/selfbot/backups /data/selfbot/sessions_backup
    chown -R selfbot:selfbot /data/selfbot 2>/dev/null || true
fi

exec gosu selfbot "$@"