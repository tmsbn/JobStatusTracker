#!/bin/bash
# Job Tracker - Extracts job application emails and updates Google Spreadsheet
#
# Flow: Mail.app (AppleScript) → Claude Code (AI parsing + Job ID tracking) → Google Sheets
#
# Persistent state is stored in job_data.json — each application gets a unique
# Job ID that is preserved across runs. AI detects status changes from new emails.
#
# Usage: ./run.sh [--days N]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
JOB_DATA_FILE="$SCRIPT_DIR/job_data.json"
RUN_LOG="$SCRIPT_DIR/logs/run_history.log"
TEMP_DIR=$(mktemp -d)

# ── Determine trigger source ────────────────────────────────
TRIGGER="${JOB_TRACKER_TRIGGER:-manual}"

# ── Parse arguments ──────────────────────────────────────────
DAYS_BACK=7
while [[ $# -gt 0 ]]; do
    case "$1" in
        --days)
            DAYS_BACK="$2"
            shift 2
            ;;
        *)
            echo "Usage: ./run.sh [--days N]"
            echo "  --days N   Look back N days for emails (default: 7)"
            exit 1
            ;;
    esac
done

# Cleanup on exit
cleanup() {
    rm -rf "$TEMP_DIR"
}
trap cleanup EXIT

echo "============================================"
echo "  Job Application Tracker"
echo "============================================"
echo "  Triggered by: $TRIGGER"
echo "  Looking back: $DAYS_BACK day(s)"
echo ""

# ── Initialize SQLite database ────────────────────────────────
"$VENV_DIR/bin/python3" "$SCRIPT_DIR/job_db.py" init > /dev/null 2>&1

# Migrate from JSON if DB is empty and JSON exists
DB_COUNT=$("$VENV_DIR/bin/python3" "$SCRIPT_DIR/job_db.py" count 2>/dev/null || echo "0")
if [ "$DB_COUNT" = "0" ] && [ -f "$JOB_DATA_FILE" ]; then
    echo "  Migrating existing job_data.json to SQLite..."
    "$VENV_DIR/bin/python3" "$SCRIPT_DIR/job_db.py" migrate --from "$JOB_DATA_FILE" 2>&1
    DB_COUNT=$("$VENV_DIR/bin/python3" "$SCRIPT_DIR/job_db.py" count 2>/dev/null || echo "0")
fi

EXISTING_COUNT="$DB_COUNT"

if [ "$EXISTING_COUNT" -gt 0 ] 2>/dev/null; then
    echo "  Loaded $EXISTING_COUNT existing job applications from database."
else
    echo "  No existing job database found. Starting fresh."
fi
echo ""

# ── Step 1: Extract emails from Mail.app ──────────────────────
echo "Step 1/4: Extracting job-related emails from Mail.app (last $DAYS_BACK days)..."
echo "  (This may take a moment depending on mailbox size)"

RAW_OUTPUT=$(osascript "$SCRIPT_DIR/extract_emails.applescript" "$DAYS_BACK" 2>/dev/null) || {
    echo "Error: Failed to run AppleScript. Make sure Mail.app has permission."
    echo "Go to: System Settings > Privacy & Security > Automation > Terminal > Mail"
    exit 1
}

if [ "$RAW_OUTPUT" = "NO_EMAILS_FOUND" ] || [ -z "$RAW_OUTPUT" ]; then
    echo "  No new job-related emails found in the last $DAYS_BACK days."
    if [ "$EXISTING_COUNT" -gt 0 ] 2>/dev/null; then
        echo "  Re-exporting existing data to Google Sheets..."
        "$VENV_DIR/bin/python3" "$SCRIPT_DIR/job_db.py" export > "$JOB_DATA_FILE"
        GSHEET_CREDS="$SCRIPT_DIR/credentials.json"
        if [ -f "$GSHEET_CREDS" ]; then
            "$VENV_DIR/bin/python3" "$SCRIPT_DIR/write_gsheet.py" "$JOB_DATA_FILE" 2>&1
        fi
    fi
    exit 0
fi

# Save raw emails to temp file
echo "$RAW_OUTPUT" > "$TEMP_DIR/raw_emails.txt"
EMAIL_COUNT=$(grep -c "===EMAIL_START===" "$TEMP_DIR/raw_emails.txt" || echo "0")
echo "  Found $EMAIL_COUNT job-related emails."

# ── Filter out already-processed emails ──────────────────────
DEDUP_OUTPUT=$("$VENV_DIR/bin/python3" "$SCRIPT_DIR/job_db.py" dedup "$TEMP_DIR/raw_emails.txt" 2>/dev/null)

if [ "$DEDUP_OUTPUT" = "NO_NEW_EMAILS" ] || [ -z "$DEDUP_OUTPUT" ]; then
    echo "  All emails already processed in previous runs. Skipping AI extraction."
    if [ "$EXISTING_COUNT" -gt 0 ] 2>/dev/null; then
        # Still run stale detection and re-export
        "$VENV_DIR/bin/python3" "$SCRIPT_DIR/job_db.py" stale 2>&1
        "$VENV_DIR/bin/python3" "$SCRIPT_DIR/job_db.py" export > "$JOB_DATA_FILE"
        GSHEET_CREDS="$SCRIPT_DIR/credentials.json"
        if [ -f "$GSHEET_CREDS" ]; then
            "$VENV_DIR/bin/python3" "$SCRIPT_DIR/write_gsheet.py" "$JOB_DATA_FILE" 2>&1
        fi
    fi
    exit 0
fi

# Replace raw emails with only new (unprocessed) ones
echo "$DEDUP_OUTPUT" > "$TEMP_DIR/raw_emails.txt"
NEW_EMAIL_COUNT=$(grep -c "===EMAIL_START===" "$TEMP_DIR/raw_emails.txt" || echo "0")
echo "  $NEW_EMAIL_COUNT new emails to process ($(( EMAIL_COUNT - NEW_EMAIL_COUNT )) already seen)."
echo ""

# ── Step 2: Extract structured data with Claude AI ──────────
echo "Step 2/4: Extracting job info with Claude AI..."

# Build prompt — only emails, no existing job data
cat > "$TEMP_DIR/prompt.txt" << 'PROMPT_DELIM'
You are a job application email parser. Extract structured data from each email below.

## EMAILS TO PARSE:
PROMPT_DELIM

cat "$TEMP_DIR/raw_emails.txt" >> "$TEMP_DIR/prompt.txt"

cat >> "$TEMP_DIR/prompt.txt" << 'PROMPT_DELIM'

## YOUR TASK:

Each email is either RECEIVED (from a recruiter/company to the user) or SENT
(from the user outward — marked with a `Direction: SENT` line). For each email
that is genuinely about a job application, extract:
- `company`: Clean company name.
  * For RECEIVED emails: use the sender's organization/domain.
  * For SENT emails: use the recipient organization from the `To:` line or the
    company mentioned in the subject/body — NOT the user's own address.
- `role`: Job title/position ("Unknown Role" if unclear)
- `job_url`: URL to the job posting if found in the email body (look for URLs containing "job", "career", "position", "apply", "lever", "greenhouse", "workday", "ashby", "icims", "smartrecruiters", "myworkdayjobs", or any link that clearly points to a job listing). Use "" if none found. NEVER fabricate a URL.
- `source`: The platform or channel this application came through. Infer from sender domain, email body, or URLs. Use one of: "LinkedIn", "Indeed", "Glassdoor", "ZipRecruiter", "Wellfound", "Dice", "Lever", "Greenhouse", "Workday", "Company Website", "Referral", "Recruiter", or "Other". Use "" if truly unclear.
- `status`: EXACTLY one of: Applied, Interview, Offer, Rejected, Follow-up, Other
- `date`: Email date (YYYY-MM-DD)
- `subject`: Email subject line
- `notes`: Brief one-line summary of what this email is about. For SENT emails,
  prefix with "[Sent]" so the user can tell at a glance.

## STATUS RULES (RECEIVED):
- Confirmation email / "application received" = Applied
- Scheduling / "interview" / "meet the team" = Interview
- "We regret" / "not moving forward" / "unfortunately" = Rejected
- Compensation / start date / "congratulations" = Offer
- Generic follow-up / survey / feedback request = Follow-up

## STATUS RULES (SENT):
- Submitting an application / cover letter / resume = Applied
- Replying to schedule or confirm an interview = Interview
- Accepting or negotiating an offer = Offer
- Checking in / "following up on my application" = Follow-up
- Withdrawing = Rejected

## RULES:
- Skip emails that are NOT about job applications (newsletters, spam, promotions, marketing)
- Skip SENT emails that are clearly not a job application (personal messages,
  calendar invites, unrelated replies).
- Deduplicate: if multiple emails are about the same company+role, output ONE entry with the latest status and date. A SENT application followed by a RECEIVED confirmation should collapse into a single entry.
- Output ONLY a valid JSON array. No markdown fences, no explanation, no extra text.
- If no emails are relevant, output an empty array: []
PROMPT_DELIM

# Pipe prompt to Claude
CLAUDE_OUTPUT=$(claude -p --model claude-haiku-4-5-20251001 "$(cat "$TEMP_DIR/prompt.txt")" 2>/dev/null) || {
    echo "Error: Failed to run Claude. Make sure 'claude' CLI is available."
    echo "Check: which claude"
    exit 1
}

# Save and validate Claude's output
echo "$CLAUDE_OUTPUT" > "$TEMP_DIR/extracted.json"

python3 << PYEOF
import re, json, sys

with open("$TEMP_DIR/extracted.json", "r") as f:
    text = f.read()

text = re.sub(r'\`\`\`json?\n?', '', text)
text = re.sub(r'\`\`\`', '', text)
text = text.strip()

try:
    data = json.loads(text)
except json.JSONDecodeError:
    match = re.search(r'\[.*\]', text, re.DOTALL)
    if match:
        data = json.loads(match.group())
    else:
        print("Error: Could not extract valid JSON from Claude output", file=sys.stderr)
        sys.exit(1)

if not isinstance(data, list):
    print("Error: Expected JSON array", file=sys.stderr)
    sys.exit(1)

for entry in data:
    entry.setdefault("company", "Unknown")
    entry.setdefault("role", "Unknown Role")
    entry.setdefault("job_url", "")
    entry.setdefault("source", "")
    entry.setdefault("status", "Other")
    entry.setdefault("date", "")
    entry.setdefault("subject", "")
    entry.setdefault("notes", "")

with open("$TEMP_DIR/extracted.json", "w") as f:
    json.dump(data, f, indent=2)

print(f"  Extracted {len(data)} job-related entries from emails.")
PYEOF

if [ $? -ne 0 ]; then
    echo "Error: Could not parse Claude's output as JSON."
    exit 1
fi

# ── Match extracted entries to existing jobs and update DB ────
"$VENV_DIR/bin/python3" "$SCRIPT_DIR/job_db.py" match "$TEMP_DIR/extracted.json" 2>&1

# Mark emails as processed so they're skipped next run
"$VENV_DIR/bin/python3" "$SCRIPT_DIR/job_db.py" mark-processed "$TEMP_DIR/raw_emails.txt" 2>&1

echo "  Database saved."
echo ""

# ── Step 3: Detect stale applications ─────────────────────────
echo "Step 3/4: Checking for stale applications..."
"$VENV_DIR/bin/python3" "$SCRIPT_DIR/job_db.py" stale 2>&1

"$VENV_DIR/bin/python3" "$SCRIPT_DIR/job_db.py" export > "$JOB_DATA_FILE"
echo ""

# ── Step 4: Write to Google Sheets ────────────────────────────
echo "Step 4/4: Updating Google Spreadsheet..."

"$VENV_DIR/bin/python3" "$SCRIPT_DIR/write_gsheet.py" "$JOB_DATA_FILE" 2>&1 || {
    echo "  Error: Google Sheets update failed."
    exit 1
}

echo ""
echo "============================================"
echo "  Done! Your job tracker is ready."
echo "============================================"

# ── Log this run (only for manual triggers — launchd logs from its wrapper) ──
if [ "$TRIGGER" = "manual" ]; then
    mkdir -p "$(dirname "$RUN_LOG")"
    echo "$(date '+%Y-%m-%d %H:%M:%S') | manual   | success" >> "$RUN_LOG"
fi
