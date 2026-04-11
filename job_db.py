#!/usr/bin/env python3
"""SQLite database layer for Job Tracker.

Usage:
    job_db.py init
    job_db.py migrate --from <json_file>
    job_db.py match <extracted_json>
    job_db.py export
    job_db.py count
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
from datetime import datetime, timedelta

DB_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(DB_DIR, "job_tracker.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id       TEXT PRIMARY KEY,
    company      TEXT NOT NULL,
    role         TEXT NOT NULL DEFAULT 'Unknown Role',
    job_url      TEXT DEFAULT '',
    source       TEXT DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'Other',
    date         TEXT NOT NULL,
    last_updated TEXT NOT NULL,
    subject      TEXT DEFAULT '',
    notes        TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS status_history (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id   TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
    status   TEXT NOT NULL,
    date     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS company_aliases (
    alias   TEXT PRIMARY KEY,
    company TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS processed_emails (
    email_hash TEXT PRIMARY KEY,
    processed_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_jobs_company ON jobs(company COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_last_updated ON jobs(last_updated);
CREATE INDEX IF NOT EXISTS idx_status_history_job_id ON status_history(job_id);
"""


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


# ── init ────────────────────────────────────────────────────────

def cmd_init(_args):
    conn = get_conn()
    conn.executescript(SCHEMA)
    # Migrate existing databases: add columns that may not exist yet
    for col, default in [("source", "''")]:
        try:
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} TEXT DEFAULT {default}")
        except sqlite3.OperationalError:
            pass  # Column already exists
    conn.close()
    print("Database initialized.")


# ── migrate ─────────────────────────────────────────────────────

def generate_aliases(company):
    """Generate domain-style aliases from a company name."""
    aliases = set()
    name = company.strip()
    aliases.add(name.lower())

    # Strip common suffixes
    clean = re.sub(r'\s*(Inc\.?|LLC|Ltd\.?|Group|Corp\.?|Consulting)\s*$', '', name, flags=re.IGNORECASE).strip()
    if clean:
        aliases.add(clean.lower())

    # Domain-style: "Solomon Page Group LLC" -> "solomonpage"
    words = re.findall(r'[A-Za-z0-9]+', clean)
    if words:
        aliases.add(''.join(w.lower() for w in words))
        # Also add with dots/hyphens for domain matching
        aliases.add('.'.join(w.lower() for w in words))

    # Common domain patterns
    slug = ''.join(w.lower() for w in words) if words else name.lower()
    aliases.add(f"{slug}.com")

    return aliases


def cmd_migrate(args):
    json_file = args.source
    if not os.path.exists(json_file):
        print(f"Error: {json_file} not found.", file=sys.stderr)
        sys.exit(1)

    with open(json_file) as f:
        data = json.load(f)

    if not isinstance(data, list):
        print("Error: Expected JSON array.", file=sys.stderr)
        sys.exit(1)

    conn = get_conn()
    conn.executescript(SCHEMA)

    with conn:
        job_count = 0
        history_count = 0
        alias_count = 0
        companies_seen = set()

        for entry in data:
            job_id = entry.get("job_id", "")
            company = entry.get("company", "Unknown")

            conn.execute(
                """INSERT OR REPLACE INTO jobs
                   (job_id, company, role, job_url, source, status, date, last_updated, subject, notes)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    job_id,
                    company,
                    entry.get("role", "Unknown Role"),
                    entry.get("job_url", ""),
                    entry.get("source", ""),
                    entry.get("status", "Other"),
                    entry.get("date", ""),
                    entry.get("last_updated", entry.get("date", "")),
                    entry.get("subject", ""),
                    entry.get("notes", ""),
                ),
            )
            job_count += 1

            # Delete existing history for this job (in case of re-migration)
            conn.execute("DELETE FROM status_history WHERE job_id = ?", (job_id,))
            for sh in entry.get("status_history", []):
                conn.execute(
                    "INSERT INTO status_history (job_id, status, date) VALUES (?, ?, ?)",
                    (job_id, sh.get("status", ""), sh.get("date", "")),
                )
                history_count += 1

            # Build aliases
            if company not in companies_seen:
                companies_seen.add(company)
                for alias in generate_aliases(company):
                    try:
                        conn.execute(
                            "INSERT OR IGNORE INTO company_aliases (alias, company) VALUES (?, ?)",
                            (alias, company),
                        )
                        alias_count += 1
                    except sqlite3.IntegrityError:
                        pass

    conn.close()
    print(f"Migrated {job_count} jobs, {history_count} status history entries, {alias_count} company aliases.")


# ── match ───────────────────────────────────────────────────────

# Status progression order — higher index = further along
STATUS_ORDER = {
    "Applied": 0,
    "Follow-up": 1,
    "Interview": 2,
    "Offer": 3,
    "Accepted": 4,
    "Rejected": 5,
    "No Response": 6,
    "Other": -1,
}


def normalize_company(name):
    """Normalize company name for fuzzy matching."""
    name = name.lower().strip()
    name = re.sub(r'\s*(inc\.?|llc|ltd\.?|group|corp\.?|consulting|co\.?)\s*$', '', name, flags=re.IGNORECASE)
    name = re.sub(r'[^a-z0-9]', '', name)
    return name


def find_matching_job(conn, company, role):
    """Find an existing job matching company+role. Returns the row or None."""
    norm_company = normalize_company(company)

    # 1. Try exact company+role match (case-insensitive)
    row = conn.execute(
        "SELECT * FROM jobs WHERE company COLLATE NOCASE = ? AND role COLLATE NOCASE = ?",
        (company, role),
    ).fetchone()
    if row:
        return row

    # 2. Try normalized company name match + role
    all_jobs = conn.execute("SELECT * FROM jobs").fetchall()
    for job in all_jobs:
        if normalize_company(job['company']) == norm_company and job['role'].lower() == role.lower():
            return job

    # 3. Try company alias match + role
    alias_row = conn.execute(
        "SELECT company FROM company_aliases WHERE alias = ?", (norm_company,)
    ).fetchone()
    if alias_row:
        row = conn.execute(
            "SELECT * FROM jobs WHERE company = ? AND role COLLATE NOCASE = ?",
            (alias_row['company'], role),
        ).fetchone()
        if row:
            return row

    # 4. Try company match only (for "Unknown Role" entries)
    if role == "Unknown Role":
        row = conn.execute(
            "SELECT * FROM jobs WHERE company COLLATE NOCASE = ? ORDER BY last_updated DESC LIMIT 1",
            (company,),
        ).fetchone()
        if not row:
            for job in all_jobs:
                if normalize_company(job['company']) == norm_company:
                    return job

    return None


def cmd_match(args):
    """Match Claude's extracted entries against existing jobs in SQLite.

    For each extracted entry:
    - If it matches an existing job by company+role: update status/notes/dates
    - If no match: insert as a new job with the next available ID
    Status is never downgraded (e.g., Interview won't go back to Applied).
    """
    json_file = args.extracted_json
    if not os.path.exists(json_file):
        print(f"Error: {json_file} not found.", file=sys.stderr)
        sys.exit(1)

    with open(json_file) as f:
        data = json.load(f)

    if not isinstance(data, list):
        print("Error: Expected JSON array.", file=sys.stderr)
        sys.exit(1)

    if not data:
        print("  No entries to match.")
        return

    # Backup DB before mutation
    if os.path.exists(DB_PATH):
        shutil.copy2(DB_PATH, DB_PATH + ".bak")

    conn = get_conn()

    # Every insert/update in this run gets tagged with today's date so
    # `last_updated` reflects when the tracker actually touched the record,
    # not the date of the underlying email.
    today = datetime.now().strftime("%Y-%m-%d")

    # Get next available job ID
    row = conn.execute(
        "SELECT job_id FROM jobs WHERE job_id LIKE 'JOB-%' ORDER BY CAST(SUBSTR(job_id, 5) AS INTEGER) DESC LIMIT 1"
    ).fetchone()
    if row:
        m = re.search(r'JOB-(\d+)', row['job_id'])
        next_id = int(m.group(1)) + 1 if m else 1
    else:
        next_id = 1

    matched = 0
    inserted = 0

    with conn:
        for entry in data:
            company = entry.get("company", "Unknown")
            role = entry.get("role", "Unknown Role")
            new_status = entry.get("status", "Other")
            new_date = entry.get("date", "")
            subject = entry.get("subject", "")
            notes = entry.get("notes", "")
            job_url = entry.get("job_url", "")
            source = entry.get("source", "")

            existing = find_matching_job(conn, company, role)

            if existing:
                # Update existing job — never downgrade status
                old_status = existing['status']
                old_order = STATUS_ORDER.get(old_status, -1)
                new_order = STATUS_ORDER.get(new_status, -1)

                # Rejected always wins (it's a terminal state from any position)
                if new_status == "Rejected":
                    final_status = "Rejected"
                elif new_order > old_order:
                    final_status = new_status
                else:
                    final_status = old_status

                # last_updated = when the tracker touched the row (today).
                final_updated = today

                # Append notes
                old_notes = existing['notes'] or ""
                if notes and notes not in old_notes:
                    final_notes = f"{old_notes} {notes}".strip()
                else:
                    final_notes = old_notes

                # Update job_url and source if we didn't have them
                final_url = existing['job_url'] or job_url
                final_source = existing['source'] or source

                conn.execute(
                    """UPDATE jobs SET status=?, last_updated=?, subject=?, notes=?, job_url=?, source=?
                       WHERE job_id=?""",
                    (final_status, final_updated, subject or existing['subject'],
                     final_notes, final_url, final_source, existing['job_id']),
                )

                # Add status history entry if status changed
                if final_status != old_status:
                    conn.execute(
                        "INSERT INTO status_history (job_id, status, date) VALUES (?, ?, ?)",
                        (existing['job_id'], final_status, new_date or final_updated),
                    )

                matched += 1
            else:
                # Insert new job
                job_id = f"JOB-{next_id:03d}"
                next_id += 1

                conn.execute(
                    """INSERT INTO jobs
                       (job_id, company, role, job_url, source, status, date, last_updated, subject, notes)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (job_id, company, role, job_url, source, new_status,
                     new_date, today, subject, notes),
                )

                conn.execute(
                    "INSERT INTO status_history (job_id, status, date) VALUES (?, ?, ?)",
                    (job_id, new_status, new_date),
                )

                # Add company aliases for new companies
                for alias in generate_aliases(company):
                    conn.execute(
                        "INSERT OR IGNORE INTO company_aliases (alias, company) VALUES (?, ?)",
                        (alias, company),
                    )

                inserted += 1

    conn.close()
    print(f"  Matched {matched} existing jobs, added {inserted} new jobs.")


# ── export ──────────────────────────────────────────────────────

def cmd_export(_args):
    conn = get_conn()
    rows = conn.execute("SELECT * FROM jobs ORDER BY last_updated DESC, date DESC").fetchall()

    result = []
    for row in rows:
        job = dict(row)
        history = conn.execute(
            "SELECT status, date FROM status_history WHERE job_id = ? ORDER BY id",
            (row['job_id'],),
        ).fetchall()
        job['status_history'] = [dict(h) for h in history]
        result.append(job)

    conn.close()
    print(json.dumps(result, indent=2))


# ── count ───────────────────────────────────────────────────────

def cmd_count(_args):
    conn = get_conn()
    row = conn.execute("SELECT COUNT(*) as cnt FROM jobs").fetchone()
    conn.close()
    print(row['cnt'])


# ── dedup ──────────────────────────────────────────────────────

def parse_raw_emails(text):
    """Parse raw email text into individual email blocks."""
    emails = []
    blocks = text.split("===EMAIL_START===")
    for block in blocks:
        if "===EMAIL_END===" not in block:
            continue
        content = block.split("===EMAIL_END===")[0].strip()
        if content:
            emails.append(content)
    return emails


def hash_email(email_text):
    """Generate a stable hash from an email block's key fields."""
    subject = ""
    sender = ""
    date = ""
    for line in email_text.split("\n"):
        if line.startswith("Subject: "):
            subject = line[9:].strip()
        elif line.startswith("From: "):
            sender = line[6:].strip()
        elif line.startswith("Date: "):
            date = line[6:].strip()
    key = f"{subject}|{sender}|{date}"
    return hashlib.sha256(key.encode()).hexdigest()


def cmd_dedup(args):
    """Filter out already-processed emails. Outputs only new emails to stdout."""
    raw_file = args.raw_file
    if not os.path.exists(raw_file):
        print(f"Error: {raw_file} not found.", file=sys.stderr)
        sys.exit(1)

    with open(raw_file) as f:
        text = f.read()

    emails = parse_raw_emails(text)
    if not emails:
        print("NO_NEW_EMAILS", end="")
        return

    conn = get_conn()
    conn.executescript(SCHEMA)

    new_emails = []
    for email_text in emails:
        h = hash_email(email_text)
        row = conn.execute("SELECT 1 FROM processed_emails WHERE email_hash = ?", (h,)).fetchone()
        if not row:
            new_emails.append(email_text)

    conn.close()

    if not new_emails:
        print("NO_NEW_EMAILS", end="")
        return

    # Reassemble into the same format
    output = ""
    for email_text in new_emails:
        output += "===EMAIL_START===\n"
        output += email_text + "\n"
        output += "===EMAIL_END===\n\n"

    print(output, end="")


def cmd_mark_processed(args):
    """Mark all emails in a raw file as processed."""
    raw_file = args.raw_file
    if not os.path.exists(raw_file):
        print(f"Error: {raw_file} not found.", file=sys.stderr)
        sys.exit(1)

    with open(raw_file) as f:
        text = f.read()

    emails = parse_raw_emails(text)
    if not emails:
        return

    conn = get_conn()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with conn:
        for email_text in emails:
            h = hash_email(email_text)
            conn.execute(
                "INSERT OR IGNORE INTO processed_emails (email_hash, processed_at) VALUES (?, ?)",
                (h, now),
            )

    conn.close()
    print(f"  Marked {len(emails)} emails as processed.")


# ── stale ──────────────────────────────────────────────────────

def cmd_stale(args):
    """Mark jobs stuck in 'Applied' for too long as 'No Response'."""
    days = args.days
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    conn = get_conn()
    rows = conn.execute(
        """SELECT job_id, company, role, last_updated FROM jobs
           WHERE status = 'Applied' AND last_updated < ? AND last_updated != ''""",
        (cutoff,),
    ).fetchall()

    if not rows:
        conn.close()
        print(f"  No stale jobs found (threshold: {days} days).")
        return

    with conn:
        for row in rows:
            conn.execute(
                "UPDATE jobs SET status = 'No Response' WHERE job_id = ?",
                (row['job_id'],),
            )
            conn.execute(
                "INSERT INTO status_history (job_id, status, date) VALUES (?, ?, ?)",
                (row['job_id'], "No Response", datetime.now().strftime("%Y-%m-%d")),
            )

    conn.close()
    print(f"  Marked {len(rows)} jobs as 'No Response' (no activity in {days}+ days).")


# ── CLI ─────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Job Tracker SQLite database layer")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("init", help="Initialize the database")

    migrate_p = sub.add_parser("migrate", help="Migrate from job_data.json")
    migrate_p.add_argument("--from", dest="source", required=True, help="Path to job_data.json")

    match_p = sub.add_parser("match", help="Match extracted entries to existing jobs")
    match_p.add_argument("extracted_json", help="Path to Claude extracted JSON")

    sub.add_parser("export", help="Export database as JSON")
    sub.add_parser("count", help="Print total job count")

    dedup_p = sub.add_parser("dedup", help="Filter out already-processed emails")
    dedup_p.add_argument("raw_file", help="Path to raw emails text file")

    mark_p = sub.add_parser("mark-processed", help="Mark emails as processed")
    mark_p.add_argument("raw_file", help="Path to raw emails text file")

    stale_p = sub.add_parser("stale", help="Mark stale Applied jobs as No Response")
    stale_p.add_argument("--days", type=int, default=30, help="Days threshold (default: 30)")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    commands = {
        "init": cmd_init,
        "migrate": cmd_migrate,
        "match": cmd_match,
        "export": cmd_export,
        "count": cmd_count,
        "dedup": cmd_dedup,
        "mark-processed": cmd_mark_processed,
        "stale": cmd_stale,
    }

    commands[args.command](args)


if __name__ == "__main__":
    main()
