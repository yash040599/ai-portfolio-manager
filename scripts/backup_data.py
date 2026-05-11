"""
Two-way sync between local project data and a private Git backup repo.

Pulls the latest backup repo, then syncs in both directions:
  - Files only in local  -> copied to backup repo
  - Files only in remote -> copied to local project
  - Files in both but different -> asks which to keep (l/r)
  - SQLite databases (.db) -> MERGED row-by-row (new rows from each
    side are added to the other, nothing is deleted)

After syncing, commits and pushes changes to the backup repo.

Two normal flows:

  1. EOD VM run -> coding machine
     Bot writes new rows + new reports on the VM, pushes via this script.
     Coding machine pulls via this script — append-merge handles every-
     thing without prompts.

  2. Manual data fix on the coding machine -> VM
     You edit a DB row or a report .txt to correct bad data (e.g. a
     missed trade injected after the fact). Re-run with `--prefer local`
     so your edits become the source of truth — DB rows are UPSERTed
     (existing keys overwritten with your version, rows only on the other
     side preserved); conflicting files are kept from local. The opposite
     flow (`--prefer remote`) lets you nuke local edits and adopt remote.

Usage
-----
    python scripts/backup_data.py              # full two-way sync (HTTPS)
    python scripts/backup_data.py --ssh        # use SSH URL (for Linux VMs)
    python scripts/backup_data.py --dry-run    # show what would change (no writes)

    # Smart conflict resolution (non-interactive) — for the manual-fix flow
    python scripts/backup_data.py --prefer local   # local wins all conflicts (UPSERT into remote)
    python scripts/backup_data.py --prefer remote  # remote wins all conflicts (UPSERT into local)

    # Nuclear reset (also deletes files not on the chosen side)
    python scripts/backup_data.py --all-local  # push ALL local data to remote (full overwrite)
    python scripts/backup_data.py --all-remote # pull ALL remote data to local (full overwrite)
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
    "copilot",
]

# Skip these within synced folders
SKIP_NAMES = {
    "__pycache__", ".DS_Store", "Thumbs.db", "desktop.ini",
    "access_token.json", "ZerodhaTaxPL",
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
        print(f"  ! git pull failed: {result.stderr.strip()}")
        return False
    msg = result.stdout.strip()
    if "Already up to date" in msg:
        print("  Backup repo already up to date.")
    else:
        print("  Pulled latest from backup repo.")
    return True


def git_push(msg: str) -> bool:
    """Stage all, commit, and push in the backup repo.

    On failure surfaces the underlying git stdout/stderr (otherwise
    silent — historically masked GitHub's 100 MB rejection)."""
    def run(cmd):
        try:
            subprocess.run(cmd, cwd=BACKUP_ROOT, check=True,
                           capture_output=True, text=True)
        except subprocess.CalledProcessError as e:
            print(f"\n  ! git command failed: {' '.join(cmd)}")
            if e.stdout:
                print(f"    stdout:\n{e.stdout.rstrip()}")
            if e.stderr:
                print(f"    stderr:\n{e.stderr.rstrip()}")
            raise

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
            f"    != {rel_path}\n"
            f"      Keep (l)ocal or (r)emote? [l/r]: "
        ).strip().lower()
        if choice in ("l", "r"):
            return choice
        print("      Please enter 'l' or 'r'.")


# ================================================================
# SQLITE DATABASE MERGING
# ================================================================

# Tables with UNIQUE constraints — use INSERT OR IGNORE / OR REPLACE.
# Defer to the SQL-level UNIQUE index for dedup (do NOT add a Python-side
# key here — it would drift from the index and cause IntegrityError on
# rows that match the SQL key but differ on the Python-key columns).
#   trades:              UNIQUE(date, symbol, side, entry_time)
#                        — see services/performance_tracker.py idx_trades_dedup
#   intraday_tax_ledger: UNIQUE(...) defined at table create
#   capital_gains_ledger: UNIQUE(...) defined at table create
UNIQUE_TABLES = {
    "trades",
    "intraday_tax_ledger",
    "capital_gains_ledger",
}

# Tables without UNIQUE constraints — deduplicate on the listed key columns
# (Python-side existence check + INSERT). Only put a table here if it has
# NO SQL-level UNIQUE index — otherwise the Python key can disagree with
# the index and the INSERT path will hit IntegrityError on collisions.
APPEND_TABLES = {
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
    upsert: bool = False,
) -> int:
    """
    Merge rows from src into dst for one table.
    Returns the number of new rows inserted.

    upsert=False (default, append-merge): existing keys preserved; only
        new rows from src are added. Equivalent to INSERT OR IGNORE.
    upsert=True (preferred-side mode): existing keys are OVERWRITTEN
        with src's values. Use when src is the trusted side after a
        manual data fix. Rows that exist only in dst are left untouched.
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
        # Tables with UNIQUE constraints — INSERT OR IGNORE for append,
        # INSERT OR REPLACE for upsert (overwrites on key collision).
        rows = src_conn.execute(
            f"SELECT {', '.join(cols)} FROM {table}"
        ).fetchall()
        if not rows:
            return 0
        placeholders = ", ".join("?" for _ in cols)
        before = dst_conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        verb = "REPLACE" if upsert else "IGNORE"
        dst_conn.executemany(
            f"INSERT OR {verb} INTO {table} ({', '.join(cols)}) "
            f"VALUES ({placeholders})",
            rows,
        )
        after = dst_conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        # In upsert mode the row count may not change (key collisions
        # overwrite in place); return rows touched (inserts + updates).
        if upsert:
            return len(rows)
        return after - before

    elif table in APPEND_TABLES:
        # Tables without UNIQUE constraints — deduplicate on key columns.
        # Use SQLite's `IS` operator (null-safe equality) so rows with
        # NULL key fields (e.g. legacy trades with no entry_time) still
        # match each other and don't duplicate on every sync.
        key_cols = APPEND_TABLES[table]
        key_cols_sql = ", ".join(key_cols)
        placeholders_key = " AND ".join(f"{c} IS ?" for c in key_cols)
        all_placeholders = ", ".join("?" for _ in cols)

        rows = src_conn.execute(
            f"SELECT {', '.join(cols)} FROM {table}"
        ).fetchall()
        col_idx = {c: i for i, c in enumerate(cols)}
        inserted = 0
        updated  = 0
        for row in rows:
            key_vals = tuple(row[col_idx[c]] for c in key_cols)
            exists = dst_conn.execute(
                f"SELECT 1 FROM {table} WHERE {placeholders_key}",
                key_vals,
            ).fetchone()
            if exists:
                if upsert:
                    # Preferred-side wins: delete the dst row(s) matching
                    # this key and re-insert src's version. Handles the
                    # rare case of multiple rows per key (defensive).
                    dst_conn.execute(
                        f"DELETE FROM {table} WHERE {placeholders_key}",
                        key_vals,
                    )
                    dst_conn.execute(
                        f"INSERT INTO {table} ({', '.join(cols)}) "
                        f"VALUES ({all_placeholders})",
                        row,
                    )
                    updated += 1
                # append-mode: skip silently
            else:
                dst_conn.execute(
                    f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({all_placeholders})",
                    row,
                )
                inserted += 1
        return inserted + updated

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
        print(f"    ! Could not read log file for merge: {rel} ({e})")
        return False

    local_set = set(local_entries)
    remote_set = set(remote_entries)
    merged = sorted(local_set | remote_set)

    new_in_local = len(remote_set - local_set)
    new_in_remote = len(local_set - remote_set)

    if new_in_local == 0 and new_in_remote == 0:
        return False

    if dry_run:
        print(f"    <-> merge:   {rel} "
              f"({new_in_local} entry(s) <- remote, {new_in_remote} entry(s) -> remote)")
        return True

    if new_in_local:
        print(f"    <- {new_in_local} entry(s) from remote: {rel}")
    if new_in_remote:
        print(f"    -> {new_in_remote} entry(s) to remote:  {rel}")

    try:
        merged_text = "".join(merged)
        with open(local_path, "w", encoding="utf-8") as f:
            f.write(merged_text)
        with open(remote_path, "w", encoding="utf-8") as f:
            f.write(merged_text)
    except OSError as e:
        print(f"    ! Failed to write merged log: {rel} ({e})")
        return False

    return True


# Log files to merge instead of asking l/r
MERGE_LOG_FILES = {
    os.path.join("logs", "portfolio.log"),
}


def merge_databases(local_db: str, remote_db: str, dry_run: bool,
                    prefer: str | None = None) -> bool:
    """
    Merge two SQLite databases.

    prefer=None (default, append-merge):
        Bidirectional row union. Rows existing on either side are kept;
        no row is ever overwritten. Safe for the normal "VM appends new
        trades" flow. After merge, local is copied to remote so both
        sides hold the union.

    prefer="local" / "remote":
        UPSERT mode. Rows from the preferred side WIN on key collisions
        (their column values overwrite the other side's). Rows that
        exist only on the non-preferred side are still preserved (we do
        NOT delete). Use after a manual data fix on the preferred side
        when you need that fix to propagate to existing rows.

    Returns True if any rows were inserted/updated or the file changed.
    """
    if not os.path.isfile(local_db) or not os.path.isfile(remote_db):
        return False

    if dry_run:
        if prefer:
            print(f"    <-> upsert:  data/trades.db ({prefer} wins on key collisions)")
        else:
            print(f"    <-> merge:   data/trades.db (would merge rows from both sides)")
        return True

    total_inserted = 0
    total_upserted = 0

    local_conn = sqlite3.connect(local_db)
    remote_conn = sqlite3.connect(remote_db)

    try:
        local_tables  = set(_get_user_tables(local_conn))
        remote_tables = set(_get_user_tables(remote_conn))
        mergeable = (UNIQUE_TABLES | set(APPEND_TABLES.keys()))

        # Step A: pull preferred-side rows into the OTHER side first.
        # In append mode this is a no-op pass on local (only the
        # remote->local pull happens). In upsert mode this is where
        # the user's edits propagate.
        if prefer == "local":
            # Local is truth: UPSERT local rows into remote conn (in-mem),
            # then copy local file over remote at the end.
            for table in sorted(mergeable):
                if table in local_tables and table in remote_tables:
                    n = _merge_table(remote_conn, local_conn, table,
                                     "local->remote", upsert=True)
                    if n > 0:
                        print(f"    -> {n} row(s) UPSERTed into remote: {table}")
                        total_upserted += n
        elif prefer == "remote":
            # Remote is truth: UPSERT remote rows into local.
            for table in sorted(mergeable):
                if table not in remote_tables:
                    continue
                if table not in local_tables:
                    schema = remote_conn.execute(
                        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                        (table,),
                    ).fetchone()
                    if schema:
                        local_conn.execute(schema[0])
                        local_tables.add(table)
                if table in local_tables:
                    n = _merge_table(local_conn, remote_conn, table,
                                     "remote->local", upsert=True)
                    if n > 0:
                        print(f"    <- {n} row(s) UPSERTed into local: {table}")
                        total_upserted += n

        # Step B: standard append-merge of the OTHER direction so rows
        # that exist only on the non-preferred side are preserved.
        # Direction below = "pull rows we're missing into local".
        for table in sorted(mergeable):
            if table not in remote_tables:
                continue
            if table not in local_tables:
                schema = remote_conn.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                    (table,),
                ).fetchone()
                if schema:
                    local_conn.execute(schema[0])
                    local_tables.add(table)

            if table in local_tables:
                # Always do an append-merge from remote into local so
                # any rows only-on-remote (e.g. from another machine)
                # survive. Skip when prefer=="remote" because step A
                # already pulled everything.
                if prefer == "remote":
                    continue
                n = _merge_table(local_conn, remote_conn, table,
                                 "remote->local", upsert=False)
                if n > 0:
                    print(f"    <- {n} new row(s) from remote: {table}")
                    total_inserted += n

        local_conn.commit()
        remote_conn.commit()
    finally:
        remote_conn.close()
        local_conn.close()

    if total_inserted:
        print(f"    <-> merged {total_inserted} new row(s) into local DB")
    if total_upserted:
        print(f"    <-> upserted {total_upserted} row(s) using {prefer}-wins policy")

    # Sync the merged result back the other way so both files hold the
    # same union. After append-merge or prefer=local, local has every-
    # thing -> copy local to remote. After prefer=remote, the freshly-
    # upserted local file is also the union -> still copy local->remote
    # so the remote git checkout reflects it on next push.
    shutil.copy2(local_db, remote_db)

    changed = (total_inserted + total_upserted) > 0 \
              or not filecmp.cmp(local_db, remote_db, shallow=False)
    return changed


def main():
    parser = argparse.ArgumentParser(description="Two-way sync data with private backup repo.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would change without making any writes.")
    parser.add_argument("--ssh", action="store_true",
                        help="Use SSH URL for cloning (for VMs with SSH key auth).")
    parser.add_argument("--prefer", choices=("local", "remote"), default=None,
                        help="Non-interactive conflict resolution. Files: chosen "
                             "side wins. DBs: row-level UPSERT from chosen side "
                             "(existing keys overwritten with chosen-side values; "
                             "rows only on the other side are still preserved). "
                             "Use after a manual data fix to propagate edits.")
    parser.add_argument("--all-local", action="store_true",
                        help="Push ALL local data to remote (full overwrite, "
                             "including DELETING remote files not present locally).")
    parser.add_argument("--all-remote", action="store_true",
                        help="Pull ALL remote data to local (full overwrite, "
                             "including DELETING local files not present remotely).")
    args = parser.parse_args()

    if args.all_local and args.all_remote:
        print("  \u2717 Cannot use --all-local and --all-remote together.")
        sys.exit(1)
    if (args.all_local or args.all_remote) and args.prefer:
        print("  \u2717 --prefer is incompatible with --all-local / --all-remote "
              "(--all-* deletes; --prefer never deletes).")
        sys.exit(1)

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
        print(f"  ok Cloned successfully.")

    if not os.path.isdir(os.path.join(BACKUP_ROOT, ".git")):
        print(f"\n  {BACKUP_ROOT} exists but is not a git repo.")
        sys.exit(1)

    mode = "DRY RUN" if args.dry_run else "SYNC"
    print(f"\n  [{mode}] Two-way sync: local <-> {os.path.basename(BACKUP_ROOT)}/")

    # Step 1: Pull latest remote data
    if not args.dry_run:
        if not git_pull():
            sys.exit(1)
    print()

    # -- Full one-directional sync ---------------------------------
    if args.all_local or args.all_remote:
        direction = "local -> remote" if args.all_local else "remote -> local"
        src_root  = PROJECT_ROOT if args.all_local else BACKUP_ROOT
        dst_root  = BACKUP_ROOT  if args.all_local else PROJECT_ROOT
        print(f"  [{direction}] Full overwrite of {'remote' if args.all_local else 'local'} data\n")

        # Destructive — confirm unless dry-run.
        if not args.dry_run:
            side_label = "remote backup repo" if args.all_local else "local project"
            confirm = input(
                f"  ! This will OVERWRITE the {side_label} (and DELETE any "
                f"files not on the {'local' if args.all_local else 'remote'} "
                f"side). Continue? [y/n]: "
            ).strip().lower()
            if confirm != "y":
                print("  Aborted.")
                return

        copied = 0
        for item in SYNC_ITEMS:
            src_files = collect_files(src_root, item)
            dst_files = collect_files(dst_root, item)
            # Copy all source files to destination
            for rel, abs_src in src_files.items():
                abs_dst = os.path.join(dst_root, rel)
                if not args.dry_run:
                    os.makedirs(os.path.dirname(abs_dst), exist_ok=True)
                    shutil.copy2(abs_src, abs_dst)
                print(f"    {'->' if args.all_local else '<-'} {rel}")
                copied += 1
            # Remove destination files that don't exist in source
            for rel in dst_files:
                if rel not in src_files:
                    abs_dst = os.path.join(dst_root, rel)
                    if not args.dry_run:
                        os.remove(abs_dst)
                    print(f"    X removed: {rel}")

        print(f"\n  Summary: {copied} file(s) copied {direction}")
        if not args.dry_run and args.all_local:
            if git_push(f"sync: full overwrite from local"):
                print("  ok Pushed to remote.\n")
            else:
                print()
        return

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
            # Only in local -> copy to backup repo
            print(f"    -> remote:  {rel}")
            copy_file(local_files[rel], os.path.join(BACKUP_ROOT, rel), args.dry_run)
            copied_to_remote += 1

        elif in_remote and not in_local:
            # Only in remote -> copy to local project
            print(f"    <- local:   {rel}")
            copy_file(remote_files[rel], os.path.join(PROJECT_ROOT, rel), args.dry_run)
            copied_to_local += 1

        else:
            # Both exist — check if they differ
            identical = filecmp.cmp(local_files[rel], remote_files[rel], shallow=False)
            if identical:
                unchanged += 1
                continue

            # SQLite databases — merge rows (or upsert if --prefer set)
            if rel.endswith(".db"):
                db_merged = merge_databases(
                    local_files[rel], remote_files[rel], args.dry_run,
                    prefer=args.prefer,
                )
                if not args.dry_run and not db_merged:
                    unchanged += 1
                else:
                    copied_to_remote += 1
                continue

            # Log files — merge lines from both sides (always — logs are
            # append-only by nature; --prefer doesn't apply)
            if rel in MERGE_LOG_FILES:
                log_merged = merge_log_files(
                    local_files[rel], remote_files[rel], args.dry_run,
                )
                if not args.dry_run and not log_merged:
                    unchanged += 1
                else:
                    copied_to_remote += 1
                continue

            # Other file types — resolve conflict
            if args.prefer == "local":
                if not args.dry_run:
                    copy_file(local_files[rel], os.path.join(BACKUP_ROOT, rel), False)
                print(f"    -> remote (prefer local): {rel}")
                copied_to_remote += 1
                continue
            if args.prefer == "remote":
                if not args.dry_run:
                    copy_file(remote_files[rel], os.path.join(PROJECT_ROOT, rel), False)
                print(f"    <- local (prefer remote): {rel}")
                copied_to_local += 1
                continue

            # Default: ask
            conflicts += 1
            if args.dry_run:
                print(f"    != conflict: {rel}")
            else:
                choice = ask_conflict(rel)
                if choice == "l":
                    copy_file(local_files[rel], os.path.join(BACKUP_ROOT, rel), False)
                    print(f"      -> kept local")
                    copied_to_remote += 1
                else:
                    copy_file(remote_files[rel], os.path.join(PROJECT_ROOT, rel), False)
                    print(f"      <- kept remote")
                    copied_to_local += 1

    # Summary
    print(f"\n  Summary: {copied_to_remote} -> remote, {copied_to_local} <- local, "
          f"{conflicts} conflict(s), {unchanged} unchanged")

    if args.dry_run:
        return

    # Step 4: Push changes to backup repo
    if args.prefer:
        push_msg = f"sync: prefer-{args.prefer} (manual data fix propagated)"
    else:
        push_msg = "sync: two-way data sync"
    if git_push(push_msg):
        print("  ok Pushed to remote.\n")
    else:
        print()


if __name__ == "__main__":
    main()
