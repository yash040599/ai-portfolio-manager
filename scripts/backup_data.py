"""
Two-way sync between local project data and a private Git backup repo.

Pulls the latest backup repo, then syncs in both directions:
  - Files only in local  → copied to backup repo
  - Files only in remote → copied to local project
  - Files in both but different → asks which to keep (l/r)

After syncing, commits and pushes changes to the backup repo.

Usage
─────
    python scripts/backup_data.py              # full two-way sync (HTTPS)
    python scripts/backup_data.py --ssh        # use SSH URL (for Linux VMs)
    python scripts/backup_data.py --dry-run    # show what would change (no writes)
"""

import argparse
import filecmp
import os
import shutil
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


def main():
    parser = argparse.ArgumentParser(description="Two-way sync data with private backup repo.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would change without making any writes.")
    parser.add_argument("--ssh", action="store_true",
                        help="Use SSH URL for cloning (for VMs with SSH key auth).")
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
            if filecmp.cmp(local_files[rel], remote_files[rel], shallow=False):
                unchanged += 1
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
