#!/bin/sh
set -e

CRON_SCHEDULE="${CRON_SCHEDULE:-0 */6 * * *}"
CRONTAB_FILE="/app/crontab"

echo "lb2lidarr starting"
echo "  Schedule : ${CRON_SCHEDULE}"

# Pipe cron output straight to stdout/stderr so 'docker logs -f' works.
echo "${CRON_SCHEDULE} python /app/lb2lidarr.py 2>&1" > "${CRONTAB_FILE}"

echo "Running initial execution..."
python /app/lb2lidarr.py

echo "Starting cron scheduler..."
exec supercronic "${CRONTAB_FILE}"
