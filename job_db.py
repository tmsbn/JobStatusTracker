#!/usr/bin/env python3
"""SQLite database layer for Job Tracker.

Usage:
    job_db.py init
    job_db.py migrate --from <json_file>
    job_db.py prematch <raw_emails_file>
    job_db.py upsert <claude_output_json>
    job_db.py export [--json]
    job_db.py next-id
    job_db.py count
"""

import argparse
import json
import os
import re
import shutil
import sqlite3
import sys
from collections import defaultdict
from email.utils import parseaddr

DB_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(DB_DIR, "job_tracker.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id       TEXT PRIMARY KEY,
    company      TEXT NOT NULL,
    role         TEXT NOT NULL DEFAULT 'Unknown Role',
    job_url      TEXT DEFAULT '',
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
                   (job_id, company, role, job_url, status, date, last_updated, subject, notes)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    job_id,
                    company,
                    entry.get("role", "Unknown Role"),
                    entry.get("job_url", ""),
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


# ── prematch ────────────────────────────────────────────────────

def parse_raw_emails(filepath):
    """Parse the ===EMAIL_START===/===EMAIL_END=== delimited email file."""
    with open(filepath) as f:
        text = f.read()

    emails = []
    blocks = re.split(r'===EMAIL_START===', text)
    for block in blocks[1:]:  # skip text before first marker
        end_idx = block.find('===EMAIL_END===')
        if end_idx == -1:
            continue
        content = block[:end_idx].strip()
        email = {}
        for line in content.split('\n'):
            if ':' in line:
                key, _, val = line.partition(':')
                key = key.strip().lower()
                val = val.strip()
                if key in ('from', 'to', 'subject', 'date', 'body', 'account', 'direction'):
                    if key == 'body':
                        # Body may be multiline — capture rest
                        body_start = content.find('Body:')
                        if body_start != -1:
                            email['body'] = content[body_start + 5:].strip()
                        break
                    email[key] = val
        emails.append(email)
    return emails


def extract_domains_and_keywords(emails):
    """Extract company-identifying signals from emails."""
    domains = set()
    keywords = set()

    for email in emails:
        # Extract domain from From address
        from_addr = email.get('from', '')
        _, addr = parseaddr(from_addr)
        if not addr and '<' in from_addr:
            match = re.search(r'<([^>]+)>', from_addr)
            if match:
                addr = match.group(1)
        if '@' in addr:
            domain = addr.split('@')[1].lower()
            domains.add(domain)
            # Also add the domain without TLD: "meta.com" -> "meta"
            domain_name = domain.split('.')[0]
            if domain_name not in ('gmail', 'yahoo', 'hotmail', 'outlook', 'icloud',
                                   'mail', 'noreply', 'no-reply', 'email', 'notifications'):
                domains.add(domain_name)

        # Extract domain from To address (for sent emails)
        to_addr = email.get('to', '')
        for addr_part in to_addr.split(','):
            _, addr = parseaddr(addr_part.strip())
            if '@' in addr:
                domain = addr.split('@')[1].lower()
                domains.add(domain)
                domain_name = domain.split('.')[0]
                if domain_name not in ('gmail', 'yahoo', 'hotmail', 'outlook', 'icloud',
                                       'mail', 'noreply', 'no-reply', 'email', 'notifications'):
                    domains.add(domain_name)

        # Extract potential company names from subject
        subject = email.get('subject', '')
        # Look for capitalized words/phrases that might be company names
        words = subject.split()
        for word in words:
            clean = re.sub(r'[^A-Za-z0-9]', '', word)
            if clean and len(clean) > 2:
                keywords.add(clean.lower())

    return domains, keywords


def cmd_prematch(args):
    emails = parse_raw_emails(args.emails_file)
    if not emails:
        print("[]")
        return

    domains, keywords = extract_domains_and_keywords(emails)

    conn = get_conn()

    matched_companies = set()

    # 1. Look up company_aliases table for domain matches
    if domains:
        placeholders = ','.join('?' * len(domains))
        rows = conn.execute(
            f"SELECT DISTINCT company FROM company_aliases WHERE alias IN ({placeholders})",
            list(domains),
        ).fetchall()
        for row in rows:
            matched_companies.add(row['company'])

    # 2. Direct company name match from keywords
    all_companies = [r['company'] for r in conn.execute("SELECT DISTINCT company FROM jobs").fetchall()]
    for company in all_companies:
        company_lower = company.lower()
        for kw in keywords | domains:
            if kw in company_lower or company_lower in kw:
                matched_companies.add(company)
                break

    # 3. Safety net: include jobs updated in the last 14 days
    recent_rows = conn.execute(
        "SELECT DISTINCT company FROM jobs WHERE last_updated >= date('now', '-14 days')"
    ).fetchall()
    for row in recent_rows:
        matched_companies.add(row['company'])

    # Query matched jobs
    if matched_companies:
        placeholders = ','.join('?' * len(matched_companies))
        job_rows = conn.execute(
            f"SELECT * FROM jobs WHERE company IN ({placeholders}) ORDER BY last_updated DESC",
            list(matched_companies),
        ).fetchall()
    else:
        job_rows = []

    # Build output with status_history
    result = []
    for row in job_rows:
        job = dict(row)
        history = conn.execute(
            "SELECT status, date FROM status_history WHERE job_id = ? ORDER BY id",
            (row['job_id'],),
        ).fetchall()
        job['status_history'] = [dict(h) for h in history]
        result.append(job)

    conn.close()

    print(json.dumps(result, indent=2))
    print(f"Pre-matched {len(result)} jobs from {len(matched_companies)} companies.", file=sys.stderr)


# ── upsert ──────────────────────────────────────────────────────

def cmd_upsert(args):
    json_file = args.claude_output
    if not os.path.exists(json_file):
        print(f"Error: {json_file} not found.", file=sys.stderr)
        sys.exit(1)

    with open(json_file) as f:
        data = json.load(f)

    if not isinstance(data, list):
        print("Error: Expected JSON array.", file=sys.stderr)
        sys.exit(1)

    # Backup DB before mutation
    if os.path.exists(DB_PATH):
        shutil.copy2(DB_PATH, DB_PATH + ".bak")

    conn = get_conn()
    updated = 0
    inserted = 0

    with conn:
        for entry in data:
            job_id = entry.get("job_id", "")
            company = entry.get("company", "Unknown")

            existing = conn.execute("SELECT job_id FROM jobs WHERE job_id = ?", (job_id,)).fetchone()

            conn.execute(
                """INSERT OR REPLACE INTO jobs
                   (job_id, company, role, job_url, status, date, last_updated, subject, notes)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    job_id,
                    company,
                    entry.get("role", "Unknown Role"),
                    entry.get("job_url", ""),
                    entry.get("status", "Other"),
                    entry.get("date", ""),
                    entry.get("last_updated", entry.get("date", "")),
                    entry.get("subject", ""),
                    entry.get("notes", ""),
                ),
            )

            if existing:
                updated += 1
            else:
                inserted += 1

            # Replace status_history for this job
            conn.execute("DELETE FROM status_history WHERE job_id = ?", (job_id,))
            for sh in entry.get("status_history", []):
                conn.execute(
                    "INSERT INTO status_history (job_id, status, date) VALUES (?, ?, ?)",
                    (job_id, sh.get("status", ""), sh.get("date", "")),
                )

            # Update company aliases
            for alias in generate_aliases(company):
                conn.execute(
                    "INSERT OR IGNORE INTO company_aliases (alias, company) VALUES (?, ?)",
                    (alias, company),
                )

    conn.close()
    print(f"Upserted: {inserted} new, {updated} updated.")


# ── match ───────────────────────────────────────────────────────

# Status progression order — higher index = further along
STATUS_ORDER = {
    "Applied": 0,
    "Follow-up": 1,
    "Interview": 2,
    "Offer": 3,
    "Accepted": 4,
    "Rejected": 5,
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
    skipped = 0

    with conn:
        for entry in data:
            company = entry.get("company", "Unknown")
            role = entry.get("role", "Unknown Role")
            new_status = entry.get("status", "Other")
            new_date = entry.get("date", "")
            subject = entry.get("subject", "")
            notes = entry.get("notes", "")
            job_url = entry.get("job_url", "")

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

                # Update last_updated if this email is newer
                old_updated = existing['last_updated'] or ""
                final_updated = max(old_updated, new_date) if new_date else old_updated

                # Append notes
                old_notes = existing['notes'] or ""
                if notes and notes not in old_notes:
                    final_notes = f"{old_notes} {notes}".strip()
                else:
                    final_notes = old_notes

                # Update job_url if we didn't have one
                final_url = existing['job_url'] or job_url

                conn.execute(
                    """UPDATE jobs SET status=?, last_updated=?, subject=?, notes=?, job_url=?
                       WHERE job_id=?""",
                    (final_status, final_updated, subject or existing['subject'],
                     final_notes, final_url, existing['job_id']),
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
                       (job_id, company, role, job_url, status, date, last_updated, subject, notes)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (job_id, company, role, job_url, new_status,
                     new_date, new_date, subject, notes),
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


# ── next-id ─────────────────────────────────────────────────────

def cmd_next_id(_args):
    conn = get_conn()
    row = conn.execute(
        "SELECT job_id FROM jobs WHERE job_id LIKE 'JOB-%' ORDER BY CAST(SUBSTR(job_id, 5) AS INTEGER) DESC LIMIT 1"
    ).fetchone()
    conn.close()

    if row:
        match = re.search(r'JOB-(\d+)', row['job_id'])
        if match:
            print(int(match.group(1)) + 1)
            return
    print(1)


# ── count ───────────────────────────────────────────────────────

def cmd_count(_args):
    conn = get_conn()
    row = conn.execute("SELECT COUNT(*) as cnt FROM jobs").fetchone()
    conn.close()
    print(row['cnt'])


# ── CLI ─────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Job Tracker SQLite database layer")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("init", help="Initialize the database")

    migrate_p = sub.add_parser("migrate", help="Migrate from job_data.json")
    migrate_p.add_argument("--from", dest="source", required=True, help="Path to job_data.json")

    prematch_p = sub.add_parser("prematch", help="Pre-match emails to existing jobs")
    prematch_p.add_argument("emails_file", help="Path to raw emails file")

    upsert_p = sub.add_parser("upsert", help="Upsert Claude output into database")
    upsert_p.add_argument("claude_output", help="Path to Claude output JSON")

    match_p = sub.add_parser("match", help="Match extracted entries to existing jobs")
    match_p.add_argument("extracted_json", help="Path to Claude extracted JSON")

    sub.add_parser("export", help="Export database as JSON")
    sub.add_parser("next-id", help="Print next available job ID number")
    sub.add_parser("count", help="Print total job count")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    commands = {
        "init": cmd_init,
        "migrate": cmd_migrate,
        "prematch": cmd_prematch,
        "upsert": cmd_upsert,
        "match": cmd_match,
        "export": cmd_export,
        "next-id": cmd_next_id,
        "count": cmd_count,
    }

    commands[args.command](args)


if __name__ == "__main__":
    main()
