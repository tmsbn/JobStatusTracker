# Job Application Tracker

An automated system that extracts job application emails from Apple Mail, uses Claude AI to intelligently parse and deduplicate them, and outputs to both a formatted Excel spreadsheet and a Google Spreadsheet with persistent Job ID tracking.

## How It Works

```
                                                          ┌──> Excel (.xlsx)
Apple Mail.app ──> AppleScript ──> Claude Code AI ──> Python
    (extract)       (raw text)      (parse & dedupe)      └──> Google Sheets
```

1. **AppleScript** queries all connected Gmail accounts in Mail.app for job-related emails (inbox + sent)
2. **Claude Code CLI** (`claude -p`) analyzes the raw emails with AI to extract structured data — company, role, status — and deduplicates multiple emails about the same application
3. **Python** writes to both a local Excel file (`openpyxl`) and a Google Spreadsheet (`gspread`)

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
| `write_gsheet.py` | Writes structured JSON to Google Spreadsheet with formatting |
| `credentials.json` | Google OAuth2 client credentials (you provide this — see setup below) |
| `token.json` | Google auth token (auto-generated after first login) |
| `.gsheet_id` | Stores the Google Spreadsheet ID for reuse (auto-generated) |
| `job_data.json` | Persistent database of all tracked applications (auto-generated) |
| `logs/` | Run logs with timestamps (last 30 retained) |
| `.venv/` | Python virtual environment with `openpyxl` |

## Output

**Excel:** `~/Documents/Job Tracker.xlsx`
**Google Sheets:** Auto-created spreadsheet named "Job Tracker" (URL printed on each run)

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

A `launchd` agent runs the tracker **twice daily at 9:00 AM and 9:00 PM**, checking the last 1 day of emails each time.

- **Plist location:** `~/Library/LaunchAgents/com.merinpeter.jobtracker.plist`
- If the laptop is asleep at the scheduled time, it runs automatically **when the laptop is next opened**
- Logs are written to `~/Documents/Career/Job Tracker/logs/`

### Useful Commands

```bash
# Run manually
~/Documents/Career/Job\ Tracker/run.sh

# Run manually with custom lookback
~/Documents/Career/Job\ Tracker/run.sh --days 3

# Trigger the scheduled job immediately
launchctl start com.merinpeter.jobtracker

# Check if the agent is loaded
launchctl list | grep jobtracker

# View the latest run log
ls -t ~/Documents/Career/Job\ Tracker/logs/*.log | head -1 | xargs cat

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

The default search window is **7 days**. Override it with the `--days` flag:

```bash
./run.sh --days 3    # Look back 3 days
./run.sh --days 30   # Look back a month
```

The scheduled automation uses `--days 1` to check only the previous day's emails on each run.

## Setting Up on a New Mac

### Prerequisites

- macOS (tested on macOS Sequoia / Apple Silicon)
- Apple Mail.app configured with at least one Gmail account
- [Homebrew](https://brew.sh/) installed

### Step 1: Install Homebrew (if not already installed)

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

After installing, follow the instructions printed in the terminal to add Homebrew to your PATH (usually involves adding a line to `~/.zprofile`).

### Step 2: Install Python 3

```bash
brew install python3
```

Verify with `python3 --version` — you need Python 3.9+.

### Step 3: Install Claude Code CLI

Install Claude Code following the instructions at [claude.ai/claude-code](https://claude.ai/claude-code). The script expects the `claude` binary to be available at `~/.local/bin/claude`.

Verify with:

```bash
~/.local/bin/claude --version
```

### Step 4: Clone the repository

```bash
cd ~/Documents/Career
git clone https://github.com/tmsbn/JobStatusTracker.git "Job Tracker"
cd "Job Tracker"
```

### Step 5: Create the Python virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install openpyxl gspread google-auth-oauthlib
deactivate
```

### Step 6: Create the logs directory

```bash
mkdir -p logs
```

### Step 7: Grant Mail.app automation permission

The first time you run the script, macOS will prompt you to allow Terminal (or your terminal app) to control Mail.app. Click **Allow**.

If you miss the prompt or need to re-enable it:

1. Open **System Settings** > **Privacy & Security** > **Automation**
2. Find **Terminal** (or your terminal app, e.g., iTerm2)
3. Enable the toggle for **Mail**

### Step 8: Set up Google Sheets (optional)

If you want job data synced to a Google Spreadsheet:

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project (or select an existing one)
3. Enable these APIs:
   - **Google Sheets API** (search for it in "APIs & Services" > "Library")
   - **Google Drive API**
4. Go to **Credentials** > **Create Credentials** > **OAuth client ID**
   - Application type: **Desktop app**
   - Name: anything (e.g., "Job Tracker")
5. Download the JSON file
6. Save it as `~/Documents/Career/Job Tracker/credentials.json`
7. Run the script once manually (`./run.sh`) — a browser window will open for Google sign-in
8. After signing in, `token.json` is saved and all future runs are fully automatic

**Note:** If you skip this step, the Google Sheets upload will be silently skipped and the script still works.

### Step 9: Run it

```bash
cd ~/Documents/Career/Job\ Tracker
./run.sh
```

On a successful run, you'll see output like:

```
============================================
  Job Application Tracker
============================================

Step 1/3: Extracting job-related emails from Mail.app (last 7 days)...
Step 2/3: Processing emails with Claude AI...
Step 3/3: Updating Google Spreadsheet...

============================================
  Done! Your job tracker is ready.
============================================
```

### Step 10: Set up twice-daily automation (optional)

To have the tracker run automatically at 9 AM and 9 PM, create a `launchd` agent:

```bash
cat > ~/Library/LaunchAgents/com.merinpeter.jobtracker.plist << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.merinpeter.jobtracker</string>

    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>$HOME/Documents/Career/Job Tracker/run_scheduled.sh</string>
    </array>

    <key>StartCalendarInterval</key>
    <array>
        <dict>
            <key>Hour</key>
            <integer>9</integer>
            <key>Minute</key>
            <integer>0</integer>
        </dict>
        <dict>
            <key>Hour</key>
            <integer>21</integer>
            <key>Minute</key>
            <integer>0</integer>
        </dict>
    </array>

    <key>StandardOutPath</key>
    <string>$HOME/Documents/Career/Job Tracker/logs/launchd_stdout.log</string>
    <key>StandardErrorPath</key>
    <string>$HOME/Documents/Career/Job Tracker/logs/launchd_stderr.log</string>
</dict>
</plist>
EOF
```

**Important:** Also edit `run_scheduled.sh` and update the `HOME` variable on line 5 to your own home directory path (e.g., `/Users/yourname`).

Then load the agent:

```bash
launchctl load ~/Library/LaunchAgents/com.merinpeter.jobtracker.plist
```

If the Mac is asleep at the scheduled time, the job will run automatically when the laptop is next opened.

**Note:** The first run must be manual (Step 9) so you can complete the Google sign-in and approve the Mail.app automation prompt. After that, scheduled runs work unattended.

## Requirements

- macOS with Apple Mail.app connected to Gmail accounts
- [Claude Code CLI](https://claude.ai/claude-code) installed at `~/.local/bin/claude`
- Python 3 (via Homebrew)
- `openpyxl`, `gspread`, `google-auth-oauthlib` (installed in `.venv`)
- Mail.app automation permission for Terminal (System Settings > Privacy & Security > Automation)
- Google Cloud credentials for Sheets (optional — Excel works without it)
