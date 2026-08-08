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
    python scripts/shared/backup_data.py              # full two-way sync (HTTPS)
    python scripts/shared/backup_data.py --ssh        # use SSH URL (for Linux VMs)
    python scripts/shared/backup_data.py --dry-run    # show what would change (no writes)
    python scripts/shared/backup_data.py --include-env --all-local
                                                     # one-time machine migration:
                                                     # include .env in private repo

    # Smart conflict resolution (non-interactive) — for the manual-fix flow
    python scripts/shared/backup_data.py --prefer local   # local wins all conflicts (UPSERT into remote)
    python scripts/shared/backup_data.py --prefer remote  # remote wins all conflicts (UPSERT into local)

    # Nuclear reset (also deletes files not on the chosen side)
    python scripts/shared/backup_data.py --all-local  # push ALL local data to remote (full overwrite)
    python scripts/shared/backup_data.py --all-remote # pull ALL remote data to local (full overwrite)

    # Canonical-trades replace (#270 — deletion-aware)
    python scripts/shared/backup_data.py --canonical-trades --dry-run  # show diff
    python scripts/shared/backup_data.py --canonical-trades            # back up remote DB,
                                                                # replace it with local
"""

import argparse
import filecmp
import os
import shutil
import sqlite3
import subprocess
import sys

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv not installed; we'll just rely on real env vars.

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── Backup repo URL — read from .env (BACKUP_REPO_URL_HTTPS / _SSH) ──
# This script syncs the repository's runtime data (data/, reports/,
# logs/, copilot/) to a SEPARATE *private* GitHub repo so it survives
# reinstalls and is shareable across machines (e.g. dev laptop ↔ VM).
# .env is intentionally opt-in via --include-env because it contains
# API keys/secrets. Use that flag for a private, one-time machine move.
#
# Set ONE or BOTH of these in .env (HTTPS for laptops with `gh auth`,
# SSH for headless VMs with an SSH key on the GitHub account):
#
#     BACKUP_REPO_URL_HTTPS=https://github.com/<your-username>/<your-data-repo>.git
#     BACKUP_REPO_URL_SSH=git@github.com:<your-username>/<your-data-repo>.git
#
# The local folder name is derived from the repo name (the bit after
# the last "/" minus the .git suffix), placed alongside this project's
# root so backup is always at "../<repo-name>/" relative to the code.
GITHUB_REPO_URL     = os.getenv("BACKUP_REPO_URL_HTTPS", "").strip()
GITHUB_REPO_URL_SSH = os.getenv("BACKUP_REPO_URL_SSH",   "").strip()


def _backup_folder_name() -> str:
    """Derive ``<repo-name>`` from whichever URL the user has configured.

    Falls back to a generic name when no URL is configured so error
    messages stay readable. Never raises.
    """
    for url in (GITHUB_REPO_URL, GITHUB_REPO_URL_SSH):
        if not url:
            continue
        # Handle both ``https://host/owner/repo.git`` and
        # ``git@host:owner/repo.git`` shapes uniformly.
        tail = url.rsplit("/", 1)[-1]
        if tail.endswith(".git"):
            tail = tail[:-4]
        if tail:
            return tail
    return "backup-repo"


BACKUP_ROOT = os.path.join(os.path.dirname(PROJECT_ROOT), _backup_folder_name())


def _require_backup_url(want_ssh: bool) -> str:
    """Return the configured URL for the requested transport, or exit
    with a clear message telling the user which env var to set."""
    url = GITHUB_REPO_URL_SSH if want_ssh else GITHUB_REPO_URL
    var = "BACKUP_REPO_URL_SSH" if want_ssh else "BACKUP_REPO_URL_HTTPS"
    if not url:
        # Plain ASCII so the message renders on Windows cp1252 too.
        print(
            f"\n  ERROR: {var} is not set in your .env file.\n"
            f"  Add it (and/or the other transport variant), e.g.:\n\n"
            f"      BACKUP_REPO_URL_HTTPS=https://github.com/<your-username>/<your-data-repo>.git\n"
            f"      BACKUP_REPO_URL_SSH=git@github.com:<your-username>/<your-data-repo>.git\n\n"
            f"  See README.md -> 'Data sync' for the full bring-up guide.\n"
        )
        sys.exit(1)
    return url

# Folders/files to sync (relative to PROJECT_ROOT / BACKUP_ROOT)
SYNC_ITEMS = [
    "data",
    "reports",
    "logs",
    "copilot",
]

OPTIONAL_ENV_ITEM = ".env"

# Skip these within synced folders
SKIP_NAMES = {
    "__pycache__", ".DS_Store", "Thumbs.db", "desktop.ini",
    "access_token.json", "ZerodhaTaxPL",
}


def should_skip(name: str) -> bool:
    # SQLite WAL/SHM/journal sidecars are transient — they may vanish
    # between listing and comparison and must never be synced.
    return (
        name in SKIP_NAMES
        or name.endswith((".pyc", ".pyo", ".swp", ".swo"))
        or name.endswith(("-shm", "-wal", "-journal"))
    )


def collect_files(base: str, folder: str) -> dict[str, str]:
    """
    Walk a folder or single file and return {relative_path: absolute_path}
    for all non-skipped files, relative to `base`.
    """
    result = {}
    full = os.path.join(base, folder)
    if os.path.isfile(full):
        name = os.path.basename(full)
        if not should_skip(name):
            result[folder] = full
        return result
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


def sync_items(include_env: bool) -> list[str]:
    """Return the relative roots/files included in this sync run."""
    items = list(SYNC_ITEMS)
    if include_env:
        items.append(OPTIONAL_ENV_ITEM)
    return items


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
#                        — see modes/trade/performance_tracker.py idx_trades_dedup
#   intraday_tax_ledger: UNIQUE(...) defined at table create
#   capital_gains_ledger: UNIQUE(...) defined at table create
UNIQUE_TABLES = {
    "trades",
    "intraday_tax_ledger",
    "capital_gains_ledger",
    "intraday_candidates",
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
            print("    <-> merge:   data/trades.db (would merge rows from both sides)")
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


# ================================================================
# CANONICAL-TRADES REPLACE (Roadmap #270)
# ================================================================

# Files to canonical-replace. Each is a single-source-of-truth SQLite
# DB whose row deletions need to propagate (the default append-merge
# path is only correct for append-only tables). data/trades.db is the
# source of truth for trade lifecycle, intraday tax, capital gains, and
# (since #259) candidate telemetry. data/volume_baseline.db (Roadmap
# #260) is rebuilt nightly from candle_cache and any rebuild-with-
# different-lookback or builder bug-fix should propagate as a full
# replace, not an append-merge. Adding more canonical DBs in the
# future just means appending to this tuple.
CANONICAL_DBS = (
    os.path.join("data", "trades.db"),
    os.path.join("data", "volume_baseline.db"),
)


def _table_row_counts(db_path: str) -> dict[str, int]:
    """Return {table_name: row_count} for every user table in db_path."""
    counts: dict[str, int] = {}
    if not os.path.isfile(db_path):
        return counts
    try:
        conn = sqlite3.connect(db_path)
        try:
            for name, in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'"
            ):
                try:
                    counts[name] = conn.execute(
                        f"SELECT COUNT(*) FROM {name}"
                    ).fetchone()[0]
                except sqlite3.DatabaseError:
                    counts[name] = -1
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        pass
    return counts


def _file_sha256(path: str) -> str:
    """Hex SHA-256 of file contents — quick integrity proof for the diff."""
    import hashlib
    if not os.path.isfile(path):
        return "<missing>"
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def canonical_trades_replace(dry_run: bool) -> int:
    """
    Replace each canonical SQLite DB on the remote side with the local
    file, with deletions propagated. Backs up the existing remote file
    to <file>.bak.<UTC timestamp> before overwriting. Idempotent: a
    second consecutive run produces no diffs and no second backup.

    Returns 0 on success, 1 on any preflight error.
    """
    print("  [#270] Canonical-trades DB replace (deletion-aware)\n")

    overall_changes = 0
    for rel in CANONICAL_DBS:
        local_path  = os.path.join(PROJECT_ROOT, rel)
        remote_path = os.path.join(BACKUP_ROOT, rel)

        if not os.path.isfile(local_path):
            print(f"  ! Local DB missing — skipping: {rel}")
            continue

        local_counts  = _table_row_counts(local_path)
        remote_counts = _table_row_counts(remote_path)
        local_hash    = _file_sha256(local_path)
        remote_hash   = _file_sha256(remote_path)

        all_tables = sorted(set(local_counts) | set(remote_counts))

        diffs: list[str] = []
        for t in all_tables:
            l = local_counts.get(t, 0)
            r = remote_counts.get(t, 0)
            if l != r:
                delta = l - r
                arrow = "+" if delta > 0 else ""
                diffs.append(f"      {t:<24} local={l:>6}  remote={r:>6}  delta={arrow}{delta}")
            else:
                diffs.append(f"      {t:<24} local={l:>6}  remote={r:>6}  (same)")

        print(f"    {rel}:")
        print(f"      sha256: local={local_hash}  remote={remote_hash}")
        for line in diffs:
            print(line)

        if local_hash == remote_hash and remote_hash != "<missing>":
            print("      -> already in sync, no replace needed.\n")
            continue

        overall_changes += 1
        if dry_run:
            print("      [DRY RUN] would back up remote and replace.\n")
            continue

        # Real replace.
        os.makedirs(os.path.dirname(remote_path), exist_ok=True)
        try:
            if os.path.isfile(remote_path):
                from datetime import datetime as _dt
                stamp = _dt.utcnow().strftime("%Y%m%dT%H%M%SZ")
                bak_path = f"{remote_path}.bak.{stamp}"
                shutil.copy2(remote_path, bak_path)
                print(f"      -> backed up remote to: {os.path.basename(bak_path)}")
            shutil.copy2(local_path, remote_path)
            print("      -> replaced remote with local.")
        except (PermissionError, OSError) as e:
            print(
                f"      ! file copy failed: {e}\n"
                f"      ! likely cause: the bot or another process is holding "
                f"a lock on {os.path.basename(remote_path)} or "
                f"{os.path.basename(local_path)}.\n"
                f"      ! stop the bot (or close any DB browser holding the "
                f"file) and re-run this command. Aborting without partial "
                f"writes."
            )
            return 1

        # Sanity re-check post-write.
        post_hash = _file_sha256(remote_path)
        if post_hash != local_hash:
            print(f"      ! post-write hash mismatch (got {post_hash}, "
                  f"expected {local_hash}). Investigate before pushing.")
            return 1
        print(f"      -> post-write sha256 verified: {post_hash}\n")

    if overall_changes == 0:
        print("  All canonical DBs already in sync. Nothing to push.\n")
        return 0

    if dry_run:
        print(
            "  [DRY RUN] No files written. Re-run without --dry-run to "
            "perform the replace.\n"
        )
        return 0

    push_msg = "sync: canonical-trades replace (#270, deletion-aware)"
    if git_push(push_msg):
        print("  ok Pushed canonical replace to remote.\n")
    else:
        print("  (no git changes detected — push skipped)\n")
    return 0


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
    parser.add_argument("--canonical-trades", action="store_true",
                        help="(#270) Replace remote data/trades.db ENTIRELY "
                             "with the local file, propagating row deletions "
                             "from a manual repair (e.g. May 8 / May 11 ghost "
                             "rows). Backs up the existing remote DB first to "
                             "<file>.bak.<UTC stamp>. Combine with --dry-run "
                             "to preview row-count and checksum diffs only.")
    parser.add_argument("--include-env", action="store_true",
                        help="Include top-level .env in the private backup sync. "
                             "Use only for trusted private repos / machine migration.")
    parser.add_argument("--yes", action="store_true",
                        help="Confirm --all-local/--all-remote without an interactive prompt.")
    args = parser.parse_args()

    if args.all_local and args.all_remote:
        print("  \u2717 Cannot use --all-local and --all-remote together.")
        sys.exit(1)
    if (args.all_local or args.all_remote) and args.prefer:
        print("  \u2717 --prefer is incompatible with --all-local / --all-remote "
              "(--all-* deletes; --prefer never deletes).")
        sys.exit(1)
    if args.canonical_trades and (args.all_local or args.all_remote or args.prefer):
        print("  \u2717 --canonical-trades is a self-contained DB replace. "
              "Do not combine with --prefer / --all-local / --all-remote.")
        sys.exit(1)

    if not os.path.isdir(BACKUP_ROOT):
        clone_url = _require_backup_url(want_ssh=args.ssh)
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
                print("  Make sure your SSH key is added to GitHub.")
            else:
                print("  Make sure you're authenticated with GitHub (run: gh auth login)")
                print("  On Linux VMs with SSH keys, use: python scripts/shared/backup_data.py --ssh")
            sys.exit(1)
        print("  ok Cloned successfully.")

    if not os.path.isdir(os.path.join(BACKUP_ROOT, ".git")):
        print(f"\n  {BACKUP_ROOT} exists but is not a git repo.")
        sys.exit(1)

    mode = "DRY RUN" if args.dry_run else "SYNC"
    print(f"\n  [{mode}] Two-way sync: local <-> {os.path.basename(BACKUP_ROOT)}/")
    if args.include_env:
        print("  Including .env in this run. Ensure the backup repo is private.")

    # Step 1: Pull latest remote data
    if not args.dry_run:
        if not git_pull():
            sys.exit(1)
    print()

    # -- Canonical-trades replace (#270) ---------------------------
    # Append-merge can never delete a row that exists only on the
    # destination side, so a manual data fix that REMOVED ghost rows
    # (e.g. May 8 / May 11 reconciliation) cannot propagate via the
    # default flow. This branch replaces the remote DB file with the
    # local one after a checksum/row-count diff and a timestamped
    # backup of the remote file. Targets only the canonical SQLite
    # databases (data/trades.db). Other files use the normal flow.
    if args.canonical_trades:
        rc = canonical_trades_replace(args.dry_run)
        sys.exit(rc)

    # -- Full one-directional sync ---------------------------------
    if args.all_local or args.all_remote:
        direction = "local -> remote" if args.all_local else "remote -> local"
        src_root  = PROJECT_ROOT if args.all_local else BACKUP_ROOT
        dst_root  = BACKUP_ROOT  if args.all_local else PROJECT_ROOT
        print(f"  [{direction}] Full overwrite of {'remote' if args.all_local else 'local'} data\n")

        if args.include_env:
            env_src = os.path.join(src_root, OPTIONAL_ENV_ITEM)
            if not os.path.isfile(env_src):
                print(
                    f"  ! --include-env requested, but source .env is missing: {env_src}\n"
                    f"    A real run would remove .env from the destination. "
                    f"Push .env from the old machine first, or omit --include-env."
                )
                if not args.dry_run:
                    return 1

        # Destructive — confirm unless dry-run.
        if not args.dry_run:
            if args.yes:
                print("  Auto-confirmed by --yes.")
            else:
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
        for item in sync_items(args.include_env):
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
            if git_push("sync: full overwrite from local"):
                print("  ok Pushed to remote.\n")
            else:
                print()
        return

    # Step 2: Collect all files from both sides
    local_files  = {}
    remote_files = {}
    for item in sync_items(args.include_env):
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
                    print("      -> kept local")
                    copied_to_remote += 1
                else:
                    copy_file(remote_files[rel], os.path.join(PROJECT_ROOT, rel), False)
                    print("      <- kept remote")
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
