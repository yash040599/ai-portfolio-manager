"""
Backup all gitignored data to a separate private Git repo.

Copies data/, reports/, and logs/ to the companion repo at
../ai-portfolio-manager-data/ and pushes the changes.

Excludes: .env, access_token.json, __pycache__, IDE files, OS junk.

Usage
─────
    python scripts/backup_data.py              # sync + commit + push
    python scripts/backup_data.py --dry-run    # show what would be copied (no git)
"""

import argparse
import os
import shutil
import subprocess
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKUP_ROOT  = os.path.join(os.path.dirname(PROJECT_ROOT), "ai-portfolio-manager-data")

# Folders/files to back up (relative to PROJECT_ROOT)
BACKUP_ITEMS = [
    "data",
    "reports",
    "logs",
]

# Skip these within the backed-up folders
SKIP_NAMES = {
    "__pycache__", ".DS_Store", "Thumbs.db", "desktop.ini",
    "access_token.json",
}


def should_skip(name: str) -> bool:
    return name in SKIP_NAMES or name.endswith((".pyc", ".pyo", ".swp", ".swo"))


def sync_tree(src: str, dst: str, dry_run: bool) -> int:
    """Recursively copy src → dst, skipping unwanted files. Returns file count."""
    count = 0
    for root, dirs, files in os.walk(src):
        # Filter out skipped directories in-place
        dirs[:] = [d for d in dirs if not should_skip(d)]

        rel = os.path.relpath(root, src)
        dst_dir = os.path.join(dst, rel) if rel != "." else dst

        if not dry_run:
            os.makedirs(dst_dir, exist_ok=True)

        for f in files:
            if should_skip(f):
                continue
            src_file = os.path.join(root, f)
            dst_file = os.path.join(dst_dir, f)

            # Only copy if source is newer or dest doesn't exist
            if not os.path.exists(dst_file) or \
               os.path.getmtime(src_file) > os.path.getmtime(dst_file):
                if dry_run:
                    rel_path = os.path.relpath(src_file, PROJECT_ROOT)
                    print(f"    → {rel_path}")
                else:
                    shutil.copy2(src_file, dst_file)
                count += 1

    return count


def clean_deleted(src: str, dst: str, dry_run: bool) -> int:
    """Remove files from dst that no longer exist in src."""
    removed = 0
    for root, dirs, files in os.walk(dst, topdown=False):
        rel = os.path.relpath(root, dst)
        src_dir = os.path.join(src, rel) if rel != "." else src

        for f in files:
            src_file = os.path.join(src_dir, f)
            if not os.path.exists(src_file):
                dst_file = os.path.join(root, f)
                if dry_run:
                    print(f"    ✕ (deleted) {os.path.relpath(dst_file, BACKUP_ROOT)}")
                else:
                    os.remove(dst_file)
                removed += 1

        # Remove empty directories
        if not dry_run and os.path.isdir(root) and not os.listdir(root):
            os.rmdir(root)

    return removed


def git_push(msg: str):
    """Pull remote changes, stage all, commit, and push in the backup repo."""
    def run(cmd):
        subprocess.run(cmd, cwd=BACKUP_ROOT, check=True,
                       capture_output=True, text=True)

    # Pull any data pushed from another machine first
    try:
        run(["git", "pull", "--no-rebase"])
    except subprocess.CalledProcessError:
        pass  # first push to empty repo — pull fails, that's fine

    run(["git", "add", "-A"])

    # Check if there's anything to commit
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=BACKUP_ROOT, capture_output=True, text=True,
    )
    if not result.stdout.strip():
        print("\n  No changes to push — backup is already up to date.")
        return False

    run(["git", "commit", "-m", msg])
    run(["git", "push"])
    return True


def main():
    parser = argparse.ArgumentParser(description="Backup gitignored data to private repo.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be copied without making changes.")
    args = parser.parse_args()

    if not os.path.isdir(BACKUP_ROOT):
        print(f"\n  Backup repo not found at: {BACKUP_ROOT}")
        print(f"  Clone it first:")
        print(f"    git clone https://github.com/yash040599/-ai-portfolio-manager-data.git")
        sys.exit(1)

    if not os.path.isdir(os.path.join(BACKUP_ROOT, ".git")):
        print(f"\n  {BACKUP_ROOT} exists but is not a git repo.")
        sys.exit(1)

    mode = "DRY RUN" if args.dry_run else "BACKUP"
    print(f"\n  [{mode}] Syncing data → {os.path.basename(BACKUP_ROOT)}/")

    total_copied = 0
    total_removed = 0

    for item in BACKUP_ITEMS:
        src = os.path.join(PROJECT_ROOT, item)
        dst = os.path.join(BACKUP_ROOT, item)

        if not os.path.exists(src):
            continue

        if os.path.isfile(src):
            if not args.dry_run:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
            print(f"    → {item}")
            total_copied += 1
        else:
            copied = sync_tree(src, dst, args.dry_run)
            removed = clean_deleted(src, dst, args.dry_run) if os.path.exists(dst) else 0
            total_copied += copied
            total_removed += removed

    if args.dry_run:
        print(f"\n  Would copy/update {total_copied} file(s), remove {total_removed} file(s).")
        return

    print(f"\n  Synced {total_copied} file(s), removed {total_removed} stale file(s).")

    if git_push("backup: sync data, reports, logs"):
        print("  ✓ Pushed to remote.\n")
    else:
        print()


if __name__ == "__main__":
    main()
