#!/bin/bash
# Full ICC sync, once an hour: last 7 days of results + next 30 days of fixtures,
# with scorecards for live and completed matches.
#
# crontab entry (runs at minute 0 of every hour):
#   0 * * * * /home/nativesol/live_cricket/run_matches.sh

set -u

# Project folder = the folder this script lives in, so the same script works
# locally and on the server (/home/nativesol/live_cricket).
PROJECT_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"

# Use the project virtualenv (venv/ or .venv/); fall back to system python3.
if [ -x "$PROJECT_DIR/venv/bin/python" ]; then
    PYTHON="$PROJECT_DIR/venv/bin/python"
elif [ -x "$PROJECT_DIR/.venv/bin/python" ]; then
    PYTHON="$PROJECT_DIR/.venv/bin/python"
else
    PYTHON="$(command -v python3)"
fi
LOG_DIR="$PROJECT_DIR/logs"
LOG_FILE="$LOG_DIR/matches.log"
LOCK_FILE="/tmp/icc_sync_matches.lock"
MAX_LOG_BYTES=$((10 * 1024 * 1024))   # rotate at 10 MB

mkdir -p "$LOG_DIR"
cd "$PROJECT_DIR" || exit 1

if [ -f "$LOG_FILE" ] && [ "$(stat -c %s "$LOG_FILE")" -gt "$MAX_LOG_BYTES" ]; then
    mv -f "$LOG_FILE" "$LOG_FILE.1"
fi

# Skip this hour if the previous full sync is somehow still running.
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    echo "$(date '+%F %T') previous full sync still running, skipped" >> "$LOG_FILE"
    exit 0
fi

{
    echo "=== $(date '+%F %T') full sync"
    "$PYTHON" manage.py sync_icc --days-back 7 --days-ahead 30
    echo "=== $(date '+%F %T') finished (exit $?)"
} >> "$LOG_FILE" 2>&1
