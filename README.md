# Job Application Tracker

An automated system that extracts job application emails from Apple Mail, uses Claude AI to intelligently parse and deduplicate them, and outputs a formatted Excel spreadsheet with persistent Job ID tracking.

## How It Works

```
Apple Mail.app ──> AppleScript ──> Claude Code AI ──> Python ──> Excel
    (extract)       (raw text)      (parse & dedupe)   (format)   (output)
```

1. **AppleScript** queries all connected Gmail accounts in Mail.app for job-related emails from the last 7 days (inbox + sent)
2. **Claude Code CLI** (`claude -p`) analyzes the raw emails with AI to extract structured data — company, role, status — and deduplicates multiple emails about the same application
3. **Python** (`openpyxl`) writes everything to a formatted, color-coded Excel spreadsheet

## Job ID System

Every job application is assigned a unique, persistent ID (`JOB-001`, `JOB-002`, ...).

- IDs are stored in `job_data.json` and preserved across runs
- When the script runs again, Claude AI matches new emails to existing Job IDs by company + role
- Status is updated if it has changed (e.g., Applied -> Interview -> Offer)
- A full **status history** is maintained for each application, tracking every transition with dates

### Status Progression

```
Applied -> Interview -> Offer -> Accepted
                    \-> Rejected
       \-> Follow-up
       \-> Rejected
```

## Files

| File | Description |
|------|-------------|
| `run.sh` | Main orchestration script — runs the full pipeline |
| `run_scheduled.sh` | Wrapper for launchd — sets up PATH and logging |
| `extract_emails.applescript` | Searches Mail.app for job-related emails (last 7 days) |
| `write_excel.py` | Writes structured JSON to formatted Excel with color-coded statuses |
| `job_data.json` | Persistent database of all tracked applications (auto-generated) |
| `logs/` | Run logs with timestamps (last 30 retained) |
| `.venv/` | Python virtual environment with `openpyxl` |

## Output

**Location:** `~/Documents/Job Tracker.xlsx`

### Job Applications Sheet

| Column | Description |
|--------|-------------|
| Job ID | Unique persistent identifier (`JOB-001`) |
| Company | Company name |
| Role | Job title / position |
| Status | Applied, Interview, Offer, Rejected, Follow-up, Other (color-coded) |
| Date Applied | Date first seen |
| Last Updated | Date of most recent email |
| Email Subject | Most recent email subject line |
| Status History | Full progression trail (e.g., `Applied (03-20) -> Interview (03-25)`) |
| Notes | AI-generated summary of latest activity |

### Summary Sheet

- Total application count
- Breakdown by status
- Recent activity list

## Scheduled Automation

A `launchd` agent runs the tracker **daily at 6:00 PM**.

- **Plist location:** `~/Library/LaunchAgents/com.merinpeter.jobtracker.plist`
- If the laptop is asleep or closed at 6 PM, it runs automatically **when the laptop is next opened**
- Logs are written to `~/Documents/Job Tracker/logs/`

### Useful Commands

```bash
# Run manually
~/Documents/Job\ Tracker/run.sh

# Trigger the scheduled job immediately
launchctl start com.merinpeter.jobtracker

# Check if the agent is loaded
launchctl list | grep jobtracker

# View the latest run log
ls -t ~/Documents/Job\ Tracker/logs/*.log | head -1 | xargs cat

# Disable the schedule
launchctl unload ~/Library/LaunchAgents/com.merinpeter.jobtracker.plist

# Re-enable the schedule
launchctl load ~/Library/LaunchAgents/com.merinpeter.jobtracker.plist
```

## Email Keywords

The AppleScript searches for emails containing these keywords in the subject line:

`application`, `applied`, `interview`, `offer`, `rejected`, `hiring`, `position`, `candidate`, `recruitment`, `resume`, `job`, `career`, `opportunity`, `recruiter`, `onboarding`, `background check`, `cover letter`

To add more keywords, edit `extract_emails.applescript`.

## Changing the Search Window

The default search window is **7 days**. To change it, edit the first line in `extract_emails.applescript`:

```applescript
set oneWeekAgo to (current date) - (7 * days)
-- Change 7 to any number of days, e.g., 30 for a month
```

## Requirements

- macOS with Apple Mail.app connected to Gmail accounts
- [Claude Code CLI](https://claude.ai/claude-code) installed at `~/.local/bin/claude`
- Python 3 (via Homebrew)
- `openpyxl` (installed in `.venv`)
- Mail.app automation permission for Terminal (System Settings > Privacy & Security > Automation)
