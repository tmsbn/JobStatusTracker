#!/usr/bin/env python3
"""Writes job application data to a Google Spreadsheet with formatting."""

import json
import sys
import os
from datetime import datetime

import gspread
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CREDENTIALS_FILE = os.path.join(SCRIPT_DIR, "credentials.json")
TOKEN_FILE = os.path.join(SCRIPT_DIR, "token.json")
SHEET_ID_FILE = os.path.join(SCRIPT_DIR, ".gsheet_id")
SPREADSHEET_NAME = "Job Tracker"

HEADERS = ["Job ID", "Company", "Role", "Status", "Date Applied", "Last Updated",
           "Email Subject", "Status History", "Notes", "Job URL"]

STATUS_COLORS = {
    "applied":    {"red": 0.74, "green": 0.84, "blue": 0.93},   # Light blue
    "interview":  {"red": 1.0,  "green": 0.92, "blue": 0.61},   # Yellow
    "offer":      {"red": 0.78, "green": 0.94, "blue": 0.81},   # Green
    "accepted":   {"red": 0.0,  "green": 0.69, "blue": 0.31},   # Dark green
    "rejected":   {"red": 1.0,  "green": 0.78, "blue": 0.81},   # Red
    "follow-up":  {"red": 0.89, "green": 0.94, "blue": 0.85},   # Light green
}
DEFAULT_STATUS_COLOR = {"red": 0.85, "green": 0.85, "blue": 0.85}  # Grey


def get_status_color(status):
    status_lower = status.lower()
    for key, color in STATUS_COLORS.items():
        if key in status_lower:
            return color
    return DEFAULT_STATUS_COLOR


def format_status_history(history):
    if not history or not isinstance(history, list):
        return ""
    return " -> ".join(f"{h.get('status', '?')} ({h.get('date', '?')})" for h in history)


def authenticate():
    """Authenticate with Google using OAuth2. Opens browser on first run."""
    creds = None

    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CREDENTIALS_FILE):
                print("Error: credentials.json not found!")
                print(f"Expected at: {CREDENTIALS_FILE}")
                print()
                print("To set up Google Sheets access:")
                print("1. Go to https://console.cloud.google.com/")
                print("2. Create a project (or select existing)")
                print("3. Enable 'Google Sheets API' and 'Google Drive API'")
                print("4. Go to Credentials > Create Credentials > OAuth client ID")
                print("5. Choose 'Desktop app', download the JSON")
                print(f"6. Save it as: {CREDENTIALS_FILE}")
                sys.exit(1)

            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)

        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())

    return creds


def get_or_create_spreadsheet(gc):
    """Open existing spreadsheet or create a new one. Stores sheet ID for reuse."""
    sheet_id = None

    if os.path.exists(SHEET_ID_FILE):
        with open(SHEET_ID_FILE, "r") as f:
            sheet_id = f.read().strip()
        try:
            spreadsheet = gc.open_by_key(sheet_id)
            print(f"  Updating existing spreadsheet: {spreadsheet.url}")
            return spreadsheet
        except Exception:
            # Sheet was deleted or inaccessible, create new
            pass

    spreadsheet = gc.create(SPREADSHEET_NAME)
    with open(SHEET_ID_FILE, "w") as f:
        f.write(spreadsheet.id)

    print(f"  Created new spreadsheet: {spreadsheet.url}")
    return spreadsheet


def read_manual_entries(ws, automated_job_ids):
    """Read rows from the sheet that were manually added (not in job_data.json).

    A row is considered manual if its Job ID is not in the automated set
    and it has at least a company or role filled in.
    """
    manual_rows = []
    try:
        all_rows = ws.get_all_values()
        if len(all_rows) <= 1:
            return manual_rows
        for row in all_rows[1:]:  # skip header
            job_id = row[0].strip() if row else ""
            has_content = any(cell.strip() for cell in row[1:4])  # company, role, or status
            if has_content and job_id not in automated_job_ids:
                manual_rows.append(row)
    except Exception:
        pass
    return manual_rows


def write_gsheet(data, creds):
    gc = gspread.authorize(creds)
    spreadsheet = get_or_create_spreadsheet(gc)

    # Sort by last_updated descending
    try:
        data.sort(key=lambda x: x.get("last_updated", x.get("date", "")), reverse=True)
    except Exception:
        pass

    # Collect automated Job IDs so we can identify manual entries
    automated_job_ids = {entry.get("job_id", "") for entry in data}

    # ── Job Applications sheet ────────────────────────────────
    try:
        ws = spreadsheet.worksheet("Job Applications")
        manual_entries = read_manual_entries(ws, automated_job_ids)
        ws.clear()
    except gspread.WorksheetNotFound:
        ws = spreadsheet.sheet1
        ws.update_title("Job Applications")
        manual_entries = []

    # Build all rows
    rows = [HEADERS]
    for entry in data:
        role = entry.get("role", "Unknown")
        job_url = entry.get("job_url", "")

        # Make role a hyperlink if URL exists
        if job_url:
            role_cell = f'=HYPERLINK("{job_url}", "{role}")'
        else:
            role_cell = role

        rows.append([
            entry.get("job_id", ""),
            entry.get("company", "Unknown"),
            role_cell,
            entry.get("status", "Other"),
            entry.get("date", ""),
            entry.get("last_updated", ""),
            entry.get("subject", ""),
            format_status_history(entry.get("status_history", [])),
            entry.get("notes", ""),
            job_url,
        ])

    # Append manual entries (rows added by hand in the sheet, not in job_data.json)
    for row in manual_entries:
        # Pad or trim to match column count
        padded = (row + [""] * len(HEADERS))[:len(HEADERS)]
        rows.append(padded)

    if manual_entries:
        print(f"  Preserved {len(manual_entries)} manually added entries.")

    # Write all data at once
    ws.update(rows, value_input_option="USER_ENTERED")

    # Resize to fit
    ws.resize(rows=len(rows), cols=len(HEADERS))

    # ── Formatting ────────────────────────────────────────────
    # Header formatting
    ws.format("A1:J1", {
        "backgroundColor": {"red": 0.18, "green": 0.33, "blue": 0.59},
        "textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}, "fontSize": 11},
        "horizontalAlignment": "CENTER",
    })

    # Freeze header row
    ws.freeze(rows=1)

    # Column widths (approximate via resize)
    col_widths = [90, 180, 220, 120, 110, 110, 300, 250, 280, 300]
    requests_list = []
    sheet_id = ws.id
    for i, width in enumerate(col_widths):
        requests_list.append({
            "updateDimensionProperties": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "COLUMNS",
                    "startIndex": i,
                    "endIndex": i + 1
                },
                "properties": {"pixelSize": width},
                "fields": "pixelSize"
            }
        })

    # Status column color coding
    for row_idx, entry in enumerate(data, 2):
        status = entry.get("status", "Other")
        color = get_status_color(status)
        requests_list.append({
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": row_idx - 1,
                    "endRowIndex": row_idx,
                    "startColumnIndex": 3,
                    "endColumnIndex": 4
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": color,
                        "textFormat": {"bold": True},
                        "horizontalAlignment": "CENTER"
                    }
                },
                "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment)"
            }
        })

    # Job ID column: bold + centered
    if len(data) > 0:
        requests_list.append({
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 1,
                    "endRowIndex": len(data) + 1,
                    "startColumnIndex": 0,
                    "endColumnIndex": 1
                },
                "cell": {
                    "userEnteredFormat": {
                        "textFormat": {"bold": True, "foregroundColor": {"red": 0.18, "green": 0.33, "blue": 0.59}},
                        "horizontalAlignment": "CENTER"
                    }
                },
                "fields": "userEnteredFormat(textFormat,horizontalAlignment)"
            }
        })

    # Apply all formatting in one batch
    if requests_list:
        spreadsheet.batch_update({"requests": requests_list})

    # ── Summary sheet ─────────────────────────────────────────
    try:
        summary_ws = spreadsheet.worksheet("Summary")
        summary_ws.clear()
    except gspread.WorksheetNotFound:
        summary_ws = spreadsheet.add_worksheet("Summary", rows=30, cols=3)

    status_counts = {}
    for entry in data:
        s = entry.get("status", "Other")
        status_counts[s] = status_counts.get(s, 0) + 1

    summary_rows = [
        ["Job Application Summary", "", ""],
        [f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}", "", ""],
        [f"Total Applications Tracked: {len(data)}", "", ""],
        ["", "", ""],
        ["Status", "Count", ""],
    ]
    for status, count in sorted(status_counts.items()):
        summary_rows.append([status, count, ""])
    summary_rows.append(["Total", len(data), ""])
    summary_rows.append(["", "", ""])
    summary_rows.append(["Recent Activity", "", ""])
    for entry in data[:10]:
        summary_rows.append([
            f"{entry.get('job_id', '')} - {entry.get('company', '')}",
            entry.get("status", ""),
            entry.get("last_updated", "")
        ])

    summary_ws.update(summary_rows, value_input_option="USER_ENTERED")

    # Format summary header
    summary_ws.format("A1", {
        "textFormat": {"bold": True, "fontSize": 14}
    })
    summary_ws.format("A5:B5", {
        "textFormat": {"bold": True, "fontSize": 11}
    })

    total_row = 6 + len(status_counts)
    summary_ws.format(f"A{total_row}:B{total_row}", {
        "textFormat": {"bold": True}
    })

    print(f"  Saved {len(data)} job applications to Google Sheets")
    print(f"  URL: {spreadsheet.url}")
    return spreadsheet.url


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: write_gsheet.py <input.json>")
        sys.exit(1)

    with open(sys.argv[1], "r") as f:
        content = f.read().strip()
        if content.startswith("```"):
            content = "\n".join(content.split("\n")[1:])
        if content.endswith("```"):
            content = "\n".join(content.split("\n")[:-1])
        data = json.loads(content)

    creds = authenticate()
    write_gsheet(data, creds)
