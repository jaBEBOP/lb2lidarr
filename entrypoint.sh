#!/bin/sh
set -e

CRON_SCHEDULE="${CRON_SCHEDULE:-0 */6 * * *}"
CRONTAB_FILE="/app/crontab"
PUID="${PUID:-1000}"
PGID="${PGID:-1000}"

echo "lb2lidarr starting"
echo "  Schedule : ${CRON_SCHEDULE}"
echo "  PUID     : ${PUID}"
echo "  PGID     : ${PGID}"

# Create a group and user matching the requested PUID/PGID so that any
# files written by the container are owned by the host user.
addgroup -g "${PGID}" appgroup 2>/dev/null || true
adduser  -D -u "${PUID}" -G appgroup appuser 2>/dev/null || true

echo "${CRON_SCHEDULE} python /app/lb2lidarr.py 2>&1" > "${CRONTAB_FILE}"
chown appuser:appgroup "${CRONTAB_FILE}"

echo "Running initial execution..."
su-exec appuser python /app/lb2lidarr.py

echo "Starting cron scheduler..."
exec su-exec appuser supercronic "${CRONTAB_FILE}"
