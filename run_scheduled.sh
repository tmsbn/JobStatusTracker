#!/bin/bash
# Wrapper for launchd — sets up PATH since launchd doesn't source shell profiles

export PATH="/opt/homebrew/bin:$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"
export HOME="/Users/merinpeter"

SCRIPT_DIR="$HOME/Documents/Career/Job Tracker"
LOG_DIR="$SCRIPT_DIR/logs"
RUNS_DIR="$LOG_DIR/runs"
RUN_LOG="$LOG_DIR/run_history.log"
LOG_FILE="$RUNS_DIR/$(date +%Y-%m-%d_%H%M).log"
mkdir -p "$RUNS_DIR"

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

# Pull the match counts that run.sh persisted for this run
COUNTS_FILE="$SCRIPT_DIR/.last_run_counts"
ADDED=0
UPDATED=0
if [ -f "$COUNTS_FILE" ]; then
    ADDED=$(grep '^added=' "$COUNTS_FILE" | cut -d= -f2)
    UPDATED=$(grep '^updated=' "$COUNTS_FILE" | cut -d= -f2)
fi
ADDED="${ADDED:-0}"
UPDATED="${UPDATED:-0}"

printf '%s | cron     | %s | added=%3d | updated=%3d\n' \
    "$(date '+%Y-%m-%d %H:%M:%S')" "$STATUS" "$ADDED" "$UPDATED" >> "$RUN_LOG"

# Keep only the 30 most recent per-run log files
ls -t "$RUNS_DIR"/*.log 2>/dev/null | tail -n +31 | xargs rm -f 2>/dev/null
