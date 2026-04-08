"""
Two-way sync between local project data and a private Git backup repo.

Pulls the latest backup repo, then syncs in both directions:
  - Files only in local  → copied to backup repo
  - Files only in remote → copied to local project
  - Files in both but different → asks which to keep (l/r)
  - SQLite databases (.db) → MERGED row-by-row (new rows from each
    side are added to the other, nothing is deleted)

After syncing, commits and pushes changes to the backup repo.

Usage
─────
    python scripts/backup_data.py              # full two-way sync (HTTPS)
    python scripts/backup_data.py --ssh        # use SSH URL (for Linux VMs)
    python scripts/backup_data.py --dry-run    # show what would change (no writes)
    python scripts/backup_data.py --overwrite-db  # overwrite DB in one direction (asks l/r)
"""

import argparse
import filecmp
import os
import shutil
import sqlite3
import subprocess
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKUP_ROOT  = os.path.join(os.path.dirname(PROJECT_ROOT), "ai-portfolio-manager-data")

GITHUB_REPO_URL     = "https://github.com/yash040599/ai-portfolio-manager-data.git"
GITHUB_REPO_URL_SSH = "git@github.com:yash040599/ai-portfolio-manager-data.git"

# Folders/files to sync (relative to PROJECT_ROOT / BACKUP_ROOT)
SYNC_ITEMS = [
    "data",
    "reports",
    "logs",
]

# Skip these within synced folders
SKIP_NAMES = {
    "__pycache__", ".DS_Store", "Thumbs.db", "desktop.ini",
    "access_token.json",
}


def should_skip(name: str) -> bool:
    return name in SKIP_NAMES or name.endswith((".pyc", ".pyo", ".swp", ".swo"))


def collect_files(base: str, folder: str) -> dict[str, str]:
    """
    Walk a folder and return {relative_path: absolute_path} for all
    non-skipped files, relative to `base`.
    """
    result = {}
    full = os.path.join(base, folder)
    if not os.path.isdir(full):
        return result
    for root, dirs, files in os.walk(full):
        dirs[:] = [d for d in dirs if not should_skip(d)]
        for f in files:
            if should_skip(f):
                continue
            abs_path = os.path.join(root, f)
            rel_path = os.path.relpath(abs_path, base)
            result[rel_path] = abs_path
    return result


def copy_file(src: str, dst: str, dry_run: bool):
    """Copy src to dst, creating parent dirs as needed."""
    if dry_run:
        return
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst)


def git_pull():
    """Pull latest from the backup repo. Returns True on success."""
    result = subprocess.run(
        ["git", "pull", "--no-rebase"], cwd=BACKUP_ROOT,
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        # First push to empty repo — pull fails, that's fine
        if "couldn't find remote ref" in result.stderr.lower() or \
           "no such ref" in result.stderr.lower():
            print("  Backup repo is empty (first sync).")
            return True
        print(f"  ⚠ git pull failed: {result.stderr.strip()}")
        return False
    msg = result.stdout.strip()
    if "Already up to date" in msg:
        print("  Backup repo already up to date.")
    else:
        print("  Pulled latest from backup repo.")
    return True


def git_push(msg: str) -> bool:
    """Stage all, commit, and push in the backup repo."""
    def run(cmd):
        subprocess.run(cmd, cwd=BACKUP_ROOT, check=True,
                       capture_output=True, text=True)

    run(["git", "add", "-A"])

    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=BACKUP_ROOT, capture_output=True, text=True,
    )
    if not result.stdout.strip():
        print("\n  No changes to push — backup repo is already up to date.")
        return False

    run(["git", "commit", "-m", msg])
    run(["git", "push"])
    return True


def ask_conflict(rel_path: str) -> str:
    """Ask user which version to keep for a conflicting file."""
    while True:
        choice = input(
            f"    ≠ {rel_path}\n"
            f"      Keep (l)ocal or (r)emote? [l/r]: "
        ).strip().lower()
        if choice in ("l", "r"):
            return choice
        print("      Please enter 'l' or 'r'.")


# ================================================================
# SQLITE DATABASE MERGING
# ================================================================

# Tables with UNIQUE constraints — use INSERT OR IGNORE
UNIQUE_TABLES = {
    "intraday_tax_ledger",
    "capital_gains_ledger",
}

# Tables without UNIQUE constraints — deduplicate on all data columns
APPEND_TABLES = {
    "trades":             ("date", "symbol", "side", "entry_price", "exit_price",
                           "qty", "pnl", "exit_reason"),
    "portfolio_analyses": ("date", "symbol", "action", "conviction", "current_price",
                           "invested_value", "current_value"),
}


def _get_user_tables(conn: sqlite3.Connection) -> list[str]:
    """Return all user-created table names in a SQLite DB."""
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return [r[0] for r in rows]


def _get_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    """Return column names for a table (excluding 'id' autoincrement PK)."""
    rows = conn.execute(f"PRAGMA table_info('{table}')").fetchall()
    return [r[1] for r in rows if r[1] != "id"]


def _merge_table(
    dst_conn: sqlite3.Connection,
    src_conn: sqlite3.Connection,
    table: str,
    direction: str,
) -> int:
    """
    Merge rows from src into dst for one table.
    Returns the number of new rows inserted.
    """
    dst_cols = _get_columns(dst_conn, table)
    src_cols = _get_columns(src_conn, table)
    if not dst_cols or not src_cols:
        return 0

    # Use only columns that exist in BOTH databases (handles schema drift
    # when one side has new columns the other hasn't migrated yet).
    cols = [c for c in dst_cols if c in src_cols]
    if not cols:
        return 0

    if table in UNIQUE_TABLES:
        # Tables with UNIQUE constraints — INSERT OR IGNORE handles dedup
        rows = src_conn.execute(
            f"SELECT {', '.join(cols)} FROM {table}"
        ).fetchall()
        if not rows:
            return 0
        placeholders = ", ".join("?" for _ in cols)
        before = dst_conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        dst_conn.executemany(
            f"INSERT OR IGNORE INTO {table} ({', '.join(cols)}) "
            f"VALUES ({placeholders})",
            rows,
        )
        after = dst_conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        return after - before

    elif table in APPEND_TABLES:
        # Tables without UNIQUE constraints — deduplicate on key columns
        key_cols = APPEND_TABLES[table]
        key_cols_sql = ", ".join(key_cols)
        placeholders_key = " AND ".join(f"{c}=?" for c in key_cols)
        all_placeholders = ", ".join("?" for _ in cols)

        rows = src_conn.execute(
            f"SELECT {', '.join(cols)} FROM {table}"
        ).fetchall()
        col_idx = {c: i for i, c in enumerate(cols)}
        inserted = 0
        for row in rows:
            key_vals = tuple(row[col_idx[c]] for c in key_cols)
            exists = dst_conn.execute(
                f"SELECT 1 FROM {table} WHERE {placeholders_key}",
                key_vals,
            ).fetchone()
            if not exists:
                dst_conn.execute(
                    f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({all_placeholders})",
                    row,
                )
                inserted += 1
        return inserted

    else:
        # Unknown table — skip merge (copy as-is would be handled by file sync)
        return 0


def _parse_log_entries(lines: list[str]) -> list[str]:
    """
    Group raw lines into logical log entries.  A new entry starts with a
    timestamp like '2026-03-16 15:03:29,741'.  Continuation lines (stack
    traces, multi-line messages) are attached to the preceding entry.
    Returns a list of entry strings (each may contain embedded newlines).
    """
    import re
    ts_re = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")
    entries: list[str] = []
    for line in lines:
        if ts_re.match(line) or not entries:
            entries.append(line)
        else:
            entries[-1] += line
    return entries


def merge_log_files(local_path: str, remote_path: str, dry_run: bool) -> bool:
    """
    Merge two log files by combining unique log entries from both sides,
    sorted naturally (timestamps ensure chronological order).
    Multi-line entries (stack traces) are kept intact.
    Returns True if any new entries were added.
    """
    if not os.path.isfile(local_path) or not os.path.isfile(remote_path):
        return False

    rel = os.path.relpath(local_path, PROJECT_ROOT)

    try:
        with open(local_path, "r", encoding="utf-8", errors="replace") as f:
            local_entries = _parse_log_entries(f.readlines())
        with open(remote_path, "r", encoding="utf-8", errors="replace") as f:
            remote_entries = _parse_log_entries(f.readlines())
    except OSError as e:
        print(f"    ⚠ Could not read log file for merge: {rel} ({e})")
        return False

    local_set = set(local_entries)
    remote_set = set(remote_entries)
    merged = sorted(local_set | remote_set)

    new_in_local = len(remote_set - local_set)
    new_in_remote = len(local_set - remote_set)

    if new_in_local == 0 and new_in_remote == 0:
        return False

    if dry_run:
        print(f"    ↔ merge:   {rel} "
              f"({new_in_local} entry(s) ← remote, {new_in_remote} entry(s) → remote)")
        return True

    if new_in_local:
        print(f"    ← {new_in_local} entry(s) from remote: {rel}")
    if new_in_remote:
        print(f"    → {new_in_remote} entry(s) to remote:  {rel}")

    try:
        merged_text = "".join(merged)
        with open(local_path, "w", encoding="utf-8") as f:
            f.write(merged_text)
        with open(remote_path, "w", encoding="utf-8") as f:
            f.write(merged_text)
    except OSError as e:
        print(f"    ⚠ Failed to write merged log: {rel} ({e})")
        return False

    return True


# Log files to merge instead of asking l/r
MERGE_LOG_FILES = {
    os.path.join("logs", "portfolio.log"),
}


def merge_databases(local_db: str, remote_db: str, dry_run: bool) -> bool:
    """
    Merge two SQLite databases bidirectionally:
      1. New rows from remote → local
      2. Copy merged local → remote (so remote gets all rows too)
    Returns True if any rows were merged.
    """
    if not os.path.isfile(local_db) or not os.path.isfile(remote_db):
        return False

    if dry_run:
        print(f"    ↔ merge:   data/trades.db (would merge rows from both sides)")
        return True

    total_inserted = 0

    # Open both databases
    local_conn = sqlite3.connect(local_db)
    remote_conn = sqlite3.connect(remote_db)

    try:
        # Ensure tables exist in local (in case remote has tables local doesn't)
        local_tables = set(_get_user_tables(local_conn))
        remote_tables = set(_get_user_tables(remote_conn))

        mergeable = (UNIQUE_TABLES | set(APPEND_TABLES.keys()))

        for table in sorted(mergeable):
            if table not in remote_tables:
                continue
            if table not in local_tables:
                # Table exists in remote but not local — create it from remote schema
                schema = remote_conn.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                    (table,),
                ).fetchone()
                if schema:
                    local_conn.execute(schema[0])
                    local_tables.add(table)

            if table in local_tables:
                n = _merge_table(local_conn, remote_conn, table, "remote→local")
                if n > 0:
                    print(f"    ← {n} new row(s) from remote: {table}")
                    total_inserted += n

        local_conn.commit()
    finally:
        remote_conn.close()
        local_conn.close()

    if total_inserted > 0:
        print(f"    ↔ merged {total_inserted} total new row(s) into local DB")

    # Copy merged local → remote so both sides are identical
    shutil.copy2(local_db, remote_db)

    return total_inserted > 0 or not filecmp.cmp(local_db, remote_db, shallow=False)


def main():
    parser = argparse.ArgumentParser(description="Two-way sync data with private backup repo.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would change without making any writes.")
    parser.add_argument("--ssh", action="store_true",
                        help="Use SSH URL for cloning (for VMs with SSH key auth).")
    parser.add_argument("--overwrite-db", action="store_true",
                        help="Overwrite DB in one direction instead of merging. "
                             "Asks which side to keep (l/r) with confirmation.")
    args = parser.parse_args()

    if not os.path.isdir(BACKUP_ROOT):
        clone_url = GITHUB_REPO_URL_SSH if args.ssh else GITHUB_REPO_URL
        print(f"\n  Backup repo not found at: {BACKUP_ROOT}")
        print(f"  Cloning from {clone_url} ...")
        parent_dir = os.path.dirname(BACKUP_ROOT)
        result = subprocess.run(
            ["git", "clone", clone_url],
            cwd=parent_dir, capture_output=True, text=True,
        )
        if result.returncode != 0:
            print(f"  \u2717 Clone failed: {result.stderr.strip()}")
            if args.ssh:
                print(f"  Make sure your SSH key is added to GitHub.")
            else:
                print(f"  Make sure you're authenticated with GitHub (run: gh auth login)")
                print(f"  On Linux VMs with SSH keys, use: python scripts/backup_data.py --ssh")
            sys.exit(1)
        print(f"  ✓ Cloned successfully.")

    if not os.path.isdir(os.path.join(BACKUP_ROOT, ".git")):
        print(f"\n  {BACKUP_ROOT} exists but is not a git repo.")
        sys.exit(1)

    mode = "DRY RUN" if args.dry_run else "SYNC"
    print(f"\n  [{mode}] Two-way sync: local ↔ {os.path.basename(BACKUP_ROOT)}/")

    # Step 1: Pull latest remote data
    if not args.dry_run:
        if not git_pull():
            sys.exit(1)
    print()

    # Step 2: Collect all files from both sides
    local_files  = {}
    remote_files = {}
    for item in SYNC_ITEMS:
        local_files.update(collect_files(PROJECT_ROOT, item))
        remote_files.update(collect_files(BACKUP_ROOT, item))

    all_paths = sorted(set(local_files) | set(remote_files))

    # Step 3: Classify and sync each file
    copied_to_remote = 0
    copied_to_local  = 0
    conflicts        = 0
    unchanged        = 0
    db_merged        = False

    for rel in all_paths:
        in_local  = rel in local_files
        in_remote = rel in remote_files

        if in_local and not in_remote:
            # Only in local → copy to backup repo
            print(f"    → remote:  {rel}")
            copy_file(local_files[rel], os.path.join(BACKUP_ROOT, rel), args.dry_run)
            copied_to_remote += 1

        elif in_remote and not in_local:
            # Only in remote → copy to local project
            print(f"    ← local:   {rel}")
            copy_file(remote_files[rel], os.path.join(PROJECT_ROOT, rel), args.dry_run)
            copied_to_local += 1

        else:
            # Both exist — check if they differ
            identical = filecmp.cmp(local_files[rel], remote_files[rel], shallow=False)
            if identical and not (args.overwrite_db and rel.endswith(".db")):
                unchanged += 1
                continue

            # SQLite databases — merge rows (or overwrite if --overwrite-db)
            if rel.endswith(".db"):
                if args.overwrite_db:
                    if args.dry_run:
                        print(f"    ≠ overwrite-db: {rel} (will ask l/r)")
                        copied_to_remote += 1
                    else:
                        # Ask which side to keep
                        while True:
                            choice = input(
                                f"    ≠ {rel}\n"
                                f"      Keep (l)ocal or (r)emote? [l/r]: "
                            ).strip().lower()
                            if choice in ("l", "r"):
                                break
                            print("      Please enter 'l' or 'r'.")
                        # Confirm — this is destructive
                        src = "LOCAL" if choice == "l" else "REMOTE"
                        dst = "remote" if choice == "l" else "local"
                        while True:
                            confirm = input(
                                f"      ⚠ This will OVERWRITE the {dst} DB with {src}. "
                                f"Are you sure? [y/n]: "
                            ).strip().lower()
                            if confirm in ("y", "n"):
                                break
                            print("      Please enter 'y' or 'n'.")
                        if confirm == "y":
                            if choice == "l":
                                copy_file(local_files[rel], remote_files[rel], False)
                                print(f"      → overwrote remote with local")
                                copied_to_remote += 1
                            else:
                                copy_file(remote_files[rel], local_files[rel], False)
                                print(f"      ← overwrote local with remote")
                                copied_to_local += 1
                        else:
                            print(f"      ✗ skipped (no overwrite)")
                            unchanged += 1
                else:
                    db_merged = merge_databases(
                        local_files[rel], remote_files[rel], args.dry_run,
                    )
                    if not args.dry_run and not db_merged:
                        unchanged += 1
                    else:
                        copied_to_remote += 1
                continue

            # Log files — merge lines from both sides
            if rel in MERGE_LOG_FILES:
                log_merged = merge_log_files(
                    local_files[rel], remote_files[rel], args.dry_run,
                )
                if not args.dry_run and not log_merged:
                    unchanged += 1
                else:
                    copied_to_remote += 1
                continue

            # Content differs — ask user
            conflicts += 1
            if args.dry_run:
                print(f"    ≠ conflict: {rel}")
            else:
                choice = ask_conflict(rel)
                if choice == "l":
                    copy_file(local_files[rel], os.path.join(BACKUP_ROOT, rel), False)
                    print(f"      → kept local")
                    copied_to_remote += 1
                else:
                    copy_file(remote_files[rel], os.path.join(PROJECT_ROOT, rel), False)
                    print(f"      ← kept remote")
                    copied_to_local += 1

    # Summary
    print(f"\n  Summary: {copied_to_remote} → remote, {copied_to_local} ← local, "
          f"{conflicts} conflict(s), {unchanged} unchanged")

    if args.dry_run:
        return

    # Step 4: Push changes to backup repo
    if git_push("sync: two-way data sync"):
        print("  ✓ Pushed to remote.\n")
    else:
        print()


if __name__ == "__main__":
    main()
