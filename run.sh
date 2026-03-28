#!/bin/bash
# Job Tracker - Extracts job application emails and creates an Excel summary
#
# Flow: Mail.app (AppleScript) → Claude Code (AI parsing + Job ID tracking) → Excel
#
# Persistent state is stored in job_data.json — each application gets a unique
# Job ID that is preserved across runs. AI detects status changes from new emails.
#
# Usage: ./run.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
OUTPUT_FILE="$HOME/Documents/Job Tracker.xlsx"
JOB_DATA_FILE="$SCRIPT_DIR/job_data.json"
TEMP_DIR=$(mktemp -d)

# Cleanup on exit
cleanup() {
    rm -rf "$TEMP_DIR"
}
trap cleanup EXIT

echo "============================================"
echo "  Job Application Tracker"
echo "============================================"
echo ""

# ── Load existing job data ────────────────────────────────────
EXISTING_DATA="[]"
NEXT_ID=1
if [ -f "$JOB_DATA_FILE" ]; then
    EXISTING_DATA=$(cat "$JOB_DATA_FILE")
    # Calculate next Job ID from existing data
    NEXT_ID=$(python3 -c "
import json, re
data = json.loads('''$EXISTING_DATA''')
ids = [int(re.search(r'JOB-(\d+)', e.get('job_id','')).group(1)) for e in data if re.search(r'JOB-(\d+)', e.get('job_id',''))]
print(max(ids) + 1 if ids else 1)
" 2>/dev/null || echo "1")
    EXISTING_COUNT=$(python3 -c "import json; print(len(json.loads('''$EXISTING_DATA''')))" 2>/dev/null || echo "0")
    echo "  Loaded $EXISTING_COUNT existing job applications from database."
else
    echo "  No existing job database found. Starting fresh."
fi
echo ""

# ── Step 1: Extract emails from Mail.app ──────────────────────
echo "Step 1/3: Extracting job-related emails from Mail.app (last 7 days)..."
echo "  (This may take a moment depending on mailbox size)"

RAW_OUTPUT=$(osascript "$SCRIPT_DIR/extract_emails.applescript" 2>/dev/null) || {
    echo "Error: Failed to run AppleScript. Make sure Mail.app has permission."
    echo "Go to: System Settings > Privacy & Security > Automation > Terminal > Mail"
    exit 1
}

if [ "$RAW_OUTPUT" = "NO_EMAILS_FOUND" ] || [ -z "$RAW_OUTPUT" ]; then
    echo "  No new job-related emails found in the last 7 days."
    if [ -f "$JOB_DATA_FILE" ]; then
        echo "  Re-exporting existing data to Excel..."
        source "$VENV_DIR/bin/activate"
        python3 "$SCRIPT_DIR/write_excel.py" "$JOB_DATA_FILE" "$OUTPUT_FILE"
        echo ""
        echo "  File: $OUTPUT_FILE"
    fi
    exit 0
fi

# Save raw emails to temp file
echo "$RAW_OUTPUT" > "$TEMP_DIR/raw_emails.txt"
EMAIL_COUNT=$(grep -c "===EMAIL_START===" "$TEMP_DIR/raw_emails.txt" || echo "0")
echo "  Found $EMAIL_COUNT job-related emails."
echo ""

# ── Step 2: Process with Claude Code AI ───────────────────────
echo "Step 2/3: Processing emails with Claude AI..."

# Write the existing data to a temp file for Claude to reference
echo "$EXISTING_DATA" > "$TEMP_DIR/existing_jobs.json"

# Build prompt file to avoid argument length limits
cat > "$TEMP_DIR/prompt.txt" << 'PROMPT_DELIM'
You are a job application tracker with a persistent database. You must merge new emails into an existing job database while maintaining Job IDs.

## EXISTING JOB DATABASE:
PROMPT_DELIM

cat "$TEMP_DIR/existing_jobs.json" >> "$TEMP_DIR/prompt.txt"

cat >> "$TEMP_DIR/prompt.txt" << PROMPT_DELIM

## NEXT AVAILABLE JOB ID: JOB-$(printf '%03d' "$NEXT_ID")

## NEW EMAILS TO PROCESS:
PROMPT_DELIM

cat "$TEMP_DIR/raw_emails.txt" >> "$TEMP_DIR/prompt.txt"

cat >> "$TEMP_DIR/prompt.txt" << 'PROMPT_DELIM'

## YOUR TASK:

Analyze the new emails and merge them with the existing database. For each email:

1. **MATCH to existing job**: If an email is about the same company+role as an existing entry, UPDATE that entry:
   - Keep the same `job_id`
   - Update `status` if the email indicates a status change (e.g., Applied → Interview → Offer → Rejected)
   - Update `last_updated` to the email date
   - Append key info to `notes` (keep it concise)
   - Update `status_history` array with the new status and date

2. **NEW job**: If the email is about a company+role NOT in the existing database, create a new entry with the next available Job ID (JOB-XXX, incrementing).

For each job entry, output:
- `job_id`: e.g., "JOB-001" (preserve existing IDs, assign new sequential ones for new jobs)
- `company`: Clean company name
- `role`: Job title/position ("Unknown Role" if unclear)
- `job_url`: URL link to the job posting/listing if found in the email body (look for URLs containing keywords like "job", "career", "position", "apply", "lever", "greenhouse", "workday", "ashby", "icims", "smartrecruiters", "myworkdayjobs", or any link that clearly points to a job listing page). Use "" (empty string) if no job URL is found. NEVER fabricate a URL — only extract real URLs from the email content.
- `status`: EXACTLY one of: Applied, Interview, Offer, Rejected, Follow-up, Other
- `date`: Date first seen (YYYY-MM-DD)
- `last_updated`: Date of most recent email about this job (YYYY-MM-DD)
- `subject`: Most recent email subject
- `notes`: Brief summary of latest activity
- `status_history`: Array of {"status": "...", "date": "YYYY-MM-DD"} tracking all status changes

## STATUS PROGRESSION (use this to determine status):
Applied → Interview → Offer → Accepted/Rejected
An email about scheduling = Interview
An email about "we regret" / "not moving forward" = Rejected
An email about compensation/start date = Offer
A generic confirmation = Applied

## RULES:
- ALWAYS preserve ALL existing entries, even if no new emails match them
- Only include emails genuinely about job applications — skip newsletters, spam, promotions
- If you cannot determine the company, use the sender's organization name or domain
- Deduplicate: multiple emails about same company+role = one entry with updated status
- Output ONLY a valid JSON array. No markdown fences, no explanation, no extra text.
PROMPT_DELIM

# Pipe prompt to Claude
CLAUDE_OUTPUT=$(claude -p "$(cat "$TEMP_DIR/prompt.txt")" 2>/dev/null) || {
    echo "Error: Failed to run Claude. Make sure 'claude' CLI is available."
    echo "Check: which claude"
    exit 1
}

# Save Claude's output
echo "$CLAUDE_OUTPUT" > "$TEMP_DIR/processed.json"

# Validate and clean JSON
python3 << PYEOF
import re, json, sys

with open("$TEMP_DIR/processed.json", "r") as f:
    text = f.read()

# Remove markdown fences if present
text = re.sub(r'\`\`\`json?\n?', '', text)
text = re.sub(r'\`\`\`', '', text)
text = text.strip()

# Try to parse directly
try:
    data = json.loads(text)
except json.JSONDecodeError:
    # Try to find JSON array in output
    match = re.search(r'\[.*\]', text, re.DOTALL)
    if match:
        data = json.loads(match.group())
    else:
        print("Error: Could not extract valid JSON from Claude output", file=sys.stderr)
        sys.exit(1)

# Validate structure
if not isinstance(data, list):
    print("Error: Expected JSON array", file=sys.stderr)
    sys.exit(1)

# Ensure all entries have required fields
for entry in data:
    entry.setdefault("job_id", "JOB-000")
    entry.setdefault("company", "Unknown")
    entry.setdefault("role", "Unknown Role")
    entry.setdefault("job_url", "")
    entry.setdefault("status", "Other")
    entry.setdefault("date", "")
    entry.setdefault("last_updated", entry.get("date", ""))
    entry.setdefault("subject", "")
    entry.setdefault("notes", "")
    entry.setdefault("status_history", [])

with open("$TEMP_DIR/processed.json", "w") as f:
    json.dump(data, f, indent=2)

print(f"  Processed {len(data)} job applications.")
PYEOF

if [ $? -ne 0 ]; then
    echo "Error: Could not parse Claude's output as JSON."
    exit 1
fi

# ── Save updated database ────────────────────────────────────
cp "$TEMP_DIR/processed.json" "$JOB_DATA_FILE"
echo "  Database saved to: $JOB_DATA_FILE"
echo ""

# ── Step 3: Write to Excel ────────────────────────────────────
echo "Step 3/3: Writing Excel spreadsheet..."

source "$VENV_DIR/bin/activate"
python3 "$SCRIPT_DIR/write_excel.py" "$JOB_DATA_FILE" "$OUTPUT_FILE"

echo ""
echo "============================================"
echo "  Done! Your job tracker is ready."
echo "  File: $OUTPUT_FILE"
echo "============================================"
echo ""
echo "To open it now, run:"
echo "  open \"$OUTPUT_FILE\""
