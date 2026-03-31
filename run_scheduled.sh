#!/bin/bash
# Wrapper for launchd — sets up PATH since launchd doesn't source shell profiles

export PATH="/opt/homebrew/bin:$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"
export HOME="/Users/merinpeter"

LOG_DIR="$HOME/Documents/Career/Job Tracker/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/$(date +%Y-%m-%d_%H%M).log"

echo "=== Job Tracker run: $(date) ===" > "$LOG_FILE"
"$HOME/Documents/Career/Job Tracker/run.sh" --days 1 >> "$LOG_FILE" 2>&1
echo "=== Finished: $(date) ===" >> "$LOG_FILE"

# Keep only last 30 log files
ls -t "$LOG_DIR"/*.log 2>/dev/null | tail -n +31 | xargs rm -f 2>/dev/null
