"""
Recover gitignored data from the private backup Git repo.

Pulls the latest from ../ai-portfolio-manager-data/ and copies
data/, reports/, and logs/ back into this project.

Usage
─────
    python scripts/recover_data.py              # pull + restore all
    python scripts/recover_data.py --dry-run    # show what would be restored
"""

import argparse
import os
import shutil
import subprocess
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKUP_ROOT  = os.path.join(os.path.dirname(PROJECT_ROOT), "ai-portfolio-manager-data")

RESTORE_ITEMS = [
    "data",
    "reports",
    "logs",
]

SKIP_NAMES = {
    "__pycache__", ".DS_Store", "Thumbs.db", "desktop.ini",
}


def should_skip(name: str) -> bool:
    return name in SKIP_NAMES or name.endswith((".pyc", ".pyo", ".swp", ".swo"))


def sync_tree(src: str, dst: str, dry_run: bool) -> int:
    """Recursively copy src → dst, skipping unwanted files. Returns file count."""
    count = 0
    for root, dirs, files in os.walk(src):
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

            if not os.path.exists(dst_file) or \
               os.path.getmtime(src_file) > os.path.getmtime(dst_file):
                if dry_run:
                    rel_path = os.path.relpath(src_file, BACKUP_ROOT)
                    print(f"    → {rel_path}")
                else:
                    shutil.copy2(src_file, dst_file)
                count += 1

    return count


def git_pull():
    """Pull latest from the backup repo."""
    result = subprocess.run(
        ["git", "pull"], cwd=BACKUP_ROOT,
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"  ⚠ git pull failed: {result.stderr.strip()}")
        return False
    msg = result.stdout.strip()
    if "Already up to date" in msg:
        print(f"  Backup repo already up to date.")
    else:
        print(f"  Pulled latest from backup repo.")
    return True


def main():
    parser = argparse.ArgumentParser(description="Recover data from private backup repo.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be restored without making changes.")
    args = parser.parse_args()

    if not os.path.isdir(BACKUP_ROOT):
        print(f"\n  Backup repo not found at: {BACKUP_ROOT}")
        print(f"  Clone it first:")
        print(f"    git clone https://github.com/yash040599/-ai-portfolio-manager-data.git")
        sys.exit(1)

    if not os.path.isdir(os.path.join(BACKUP_ROOT, ".git")):
        print(f"\n  {BACKUP_ROOT} exists but is not a git repo.")
        sys.exit(1)

    mode = "DRY RUN" if args.dry_run else "RECOVER"
    print(f"\n  [{mode}] Restoring data from {os.path.basename(BACKUP_ROOT)}/")

    if not args.dry_run:
        if not git_pull():
            sys.exit(1)

    total = 0
    for item in RESTORE_ITEMS:
        src = os.path.join(BACKUP_ROOT, item)
        dst = os.path.join(PROJECT_ROOT, item)

        if not os.path.exists(src):
            continue

        if os.path.isfile(src):
            if not args.dry_run:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
            print(f"    → {item}")
            total += 1
        else:
            total += sync_tree(src, dst, args.dry_run)

    if args.dry_run:
        print(f"\n  Would restore {total} file(s).")
    else:
        print(f"\n  ✓ Restored {total} file(s).\n")


if __name__ == "__main__":
    main()
