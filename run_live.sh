#!/bin/bash
# Live ICC scorecard sync, every 30 seconds.
#
# Cron's smallest interval is 1 minute, so cron starts this script every minute
# and the script runs the sync twice: at :00 and at :30.
#
# crontab entry:
#   * * * * * /home/tayyab-saeed/PycharmProjects/icc_django_postgres_project/run_live.sh

set -u

PROJECT_DIR="/home/tayyab-saeed/PycharmProjects/icc_django_postgres_project"
PYTHON="$PROJECT_DIR/venv/bin/python"
LOG_DIR="$PROJECT_DIR/logs"
LOG_FILE="$LOG_DIR/live.log"
LOCK_FILE="/tmp/icc_sync_live.lock"
MAX_LOG_BYTES=$((10 * 1024 * 1024))   # rotate at 10 MB

mkdir -p "$LOG_DIR"
cd "$PROJECT_DIR" || exit 1

# Keep the log from growing forever: one rotated copy (live.log.1).
if [ -f "$LOG_FILE" ] && [ "$(stat -c %s "$LOG_FILE")" -gt "$MAX_LOG_BYTES" ]; then
    mv -f "$LOG_FILE" "$LOG_FILE.1"
fi

# Never run two live syncs at once. If the previous minute's run is still
# going (slow ICC API), skip this minute instead of piling up processes.
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    echo "$(date '+%F %T') previous live sync still running, skipped" >> "$LOG_FILE"
    exit 0
fi

run_sync() {
    echo "=== $(date '+%F %T') live sync"
    "$PYTHON" manage.py sync_icc --mode live --snapshots
    echo "=== $(date '+%F %T') finished (exit $?)"
}

start=$(date +%s)
run_sync >> "$LOG_FILE" 2>&1

# Second run at the 30-second mark (or right away if the first run took longer).
elapsed=$(( $(date +%s) - start ))
if [ "$elapsed" -lt 30 ]; then
    sleep $(( 30 - elapsed ))
fi
# Only run again if there is still time before the next cron start.
if [ $(( $(date +%s) - start )) -lt 55 ]; then
    run_sync >> "$LOG_FILE" 2>&1
fi
