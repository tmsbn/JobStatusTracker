#!/bin/bash
# Wrapper for launchd — sets up PATH since launchd doesn't source shell profiles

export PATH="/opt/homebrew/bin:$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"
export HOME="/Users/merinpeter"

SCRIPT_DIR="$HOME/Documents/Career/Job Tracker"
LOG_DIR="$SCRIPT_DIR/logs"
RUN_LOG="$SCRIPT_DIR/logs/run_history.log"
LOG_FILE="$LOG_DIR/$(date +%Y-%m-%d_%H%M).log"
mkdir -p "$LOG_DIR"

export JOB_TRACKER_TRIGGER="cron"

echo "=== Job Tracker run: $(date) ===" > "$LOG_FILE"
"$SCRIPT_DIR/run.sh" --days 1 >> "$LOG_FILE" 2>&1
EXIT_CODE=$?
echo "=== Finished: $(date) ===" >> "$LOG_FILE"

# Append to persistent run history
if [ $EXIT_CODE -eq 0 ]; then
    STATUS="success"
else
    STATUS="failed (exit $EXIT_CODE)"
fi
echo "$(date '+%Y-%m-%d %H:%M:%S') | cron     | $STATUS" >> "$RUN_LOG"

# Keep only last 30 individual log files
ls -t "$LOG_DIR"/*.log 2>/dev/null | grep -v run_history | tail -n +31 | xargs rm -f 2>/dev/null
