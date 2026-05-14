"""
Sync the private replay/backtest data repository beside the project.

This is intentionally simpler than backup_data.py. Operational trading data
needs row-level SQLite merges; replay datasets should be versioned snapshots
that a dev machine or Linux VM can clone, pull, and push as a normal Git repo.
By default this clones to ../ai-portfolio-backtest-data, mirroring the
operational data repo layout used by backup_data.py.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - dependency is in requirements.txt
    load_dotenv = None


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_REPO_NAME = "ai-portfolio-backtest-data"
DEFAULT_DATA_PATH = PROJECT_ROOT.parent / DEFAULT_DATA_REPO_NAME
MAX_GITHUB_FILE_BYTES = 100 * 1024 * 1024


def _load_env() -> None:
    if load_dotenv is not None:
        load_dotenv(PROJECT_ROOT / ".env")


def _run(
    command: list[str],
    *,
    cwd: Path | None = None,
    dry_run: bool = False,
    check: bool = True,
) -> subprocess.CompletedProcess[str] | None:
    location = f" (cwd={cwd})" if cwd else ""
    if dry_run:
        print(f"DRY-RUN: {' '.join(command)}{location}")
        return None

    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True)
    if check and result.returncode != 0:
        if result.stdout.strip():
            print(result.stdout.strip())
        if result.stderr.strip():
            print(result.stderr.strip(), file=sys.stderr)
        raise SystemExit(result.returncode)
    return result


def _existing_remote_url(data_path: Path) -> str:
    if not (data_path / ".git").is_dir():
        return ""
    result = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=data_path,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return result.stdout.strip()
    return ""


def _select_repo_url(args: argparse.Namespace, data_path: Path) -> str:
    if args.repo_url:
        return args.repo_url.strip()

    https_url = os.getenv("BACKTEST_DATA_REPO_URL_HTTPS", "").strip()
    ssh_url = os.getenv("BACKTEST_DATA_REPO_URL_SSH", "").strip()

    if args.ssh:
        selected = ssh_url
        label = "BACKTEST_DATA_REPO_URL_SSH"
    else:
        selected = https_url or ssh_url
        label = "BACKTEST_DATA_REPO_URL_HTTPS or BACKTEST_DATA_REPO_URL_SSH"

    if selected:
        return selected

    existing_url = _existing_remote_url(data_path)
    if existing_url:
        return existing_url

    raise SystemExit(
        "Missing backtest data repo URL. Set one of "
        f"{label}, or pass --repo-url."
    )


def _data_path(args: argparse.Namespace) -> Path:
    configured = args.path or os.getenv("BACKTEST_DATA_PATH", "").strip()
    if configured:
        candidate = Path(configured)
        if not candidate.is_absolute():
            candidate = PROJECT_ROOT / candidate
        return candidate.resolve()
    return DEFAULT_DATA_PATH


def _ensure_repo(data_path: Path, repo_url: str, *, dry_run: bool) -> None:
    if data_path.exists() and not (data_path / ".git").is_dir():
        raise SystemExit(
            f"{data_path} exists but is not a Git repo. Move it aside or set "
            "BACKTEST_DATA_PATH to a clean location."
        )

    if (data_path / ".git").is_dir():
        return

    data_path.parent.mkdir(parents=True, exist_ok=True)
    _run(["git", "clone", repo_url, str(data_path)], dry_run=dry_run)


def _remote_has_heads(data_path: Path) -> bool:
    result = _run(
        ["git", "ls-remote", "--heads", "origin"],
        cwd=data_path,
        check=False,
    )
    return bool(result and result.returncode == 0 and result.stdout.strip())


def _pull(data_path: Path, *, dry_run: bool) -> None:
    if dry_run:
        _run(["git", "pull", "--ff-only"], cwd=data_path, dry_run=True)
        return

    if not _remote_has_heads(data_path):
        print("Remote repo has no branches yet; local clone is ready for initial data.")
        return

    _run(["git", "pull", "--ff-only"], cwd=data_path)


def _print_status(data_path: Path, *, dry_run: bool) -> None:
    result = _run(
        ["git", "status", "--short", "--branch"],
        cwd=data_path,
        dry_run=dry_run,
        check=False,
    )
    if result and result.stdout.strip():
        print(result.stdout.rstrip())


def _oversized_files(data_path: Path) -> list[Path]:
    oversized: list[Path] = []
    for candidate in data_path.rglob("*"):
        if ".git" in candidate.parts:
            continue
        if candidate.is_file() and candidate.stat().st_size >= MAX_GITHUB_FILE_BYTES:
            oversized.append(candidate)
    return oversized


def _has_changes(data_path: Path) -> bool:
    result = _run(
        ["git", "status", "--short"],
        cwd=data_path,
        check=False,
    )
    return bool(result and result.stdout.strip())


def _commit_if_needed(data_path: Path, message: str, *, dry_run: bool) -> None:
    oversized = _oversized_files(data_path)
    if oversized:
        print("Refusing to commit files at or above GitHub's 100 MB file limit:")
        for path in oversized:
            size_mb = path.stat().st_size / (1024 * 1024)
            print(f"  {path.relative_to(data_path)} ({size_mb:.1f} MB)")
        raise SystemExit(1)

    if dry_run:
        _run(["git", "add", "-A"], cwd=data_path, dry_run=True)
        _run(["git", "commit", "-m", message], cwd=data_path, dry_run=True)
        return

    if not _has_changes(data_path):
        print("No backtest-data changes to commit.")
        return

    _run(["git", "add", "-A"], cwd=data_path)
    _run(["git", "commit", "-m", message], cwd=data_path)


def _push(data_path: Path, *, dry_run: bool) -> None:
    if dry_run:
        _run(["git", "push"], cwd=data_path, dry_run=True)
        return

    result = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=data_path)
    current_branch = result.stdout.strip() if result else ""
    if not current_branch or current_branch == "HEAD":
        raise SystemExit("Cannot push from a detached HEAD or repo without a branch.")

    upstream = _run(
        ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
        cwd=data_path,
        check=False,
    )
    if upstream and upstream.returncode == 0:
        _run(["git", "push"], cwd=data_path)
    else:
        _run(["git", "push", "-u", "origin", current_branch], cwd=data_path)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Clone, pull, status, and push the private backtest-data repo."
    )
    parser.add_argument("--ssh", action="store_true", help="Use BACKTEST_DATA_REPO_URL_SSH.")
    parser.add_argument("--repo-url", help="Override BACKTEST_DATA_REPO_URL_* for this run.")
    parser.add_argument("--path", help="Local clone path. Defaults to ../ai-portfolio-backtest-data.")
    parser.add_argument("--pull", action="store_true", help="Clone if needed, then git pull --ff-only.")
    parser.add_argument("--status", action="store_true", help="Show git status for the data repo.")
    parser.add_argument("--push", action="store_true", help="Push committed data repo changes.")
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Stage and commit all data repo changes before --push.",
    )
    parser.add_argument(
        "--message",
        default="update backtest data",
        help="Commit message used with --commit.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print git commands without writes.")
    args = parser.parse_args()

    if args.commit and not args.push:
        raise SystemExit("--commit is only supported with --push.")

    _load_env()
    data_path = _data_path(args)
    repo_url = _select_repo_url(args, data_path)

    if not (args.pull or args.status or args.push):
        args.pull = True
        args.status = True

    _ensure_repo(data_path, repo_url, dry_run=args.dry_run)

    if args.pull:
        _pull(data_path, dry_run=args.dry_run)
    if args.status:
        _print_status(data_path, dry_run=args.dry_run)
    if args.push:
        if args.commit:
            _commit_if_needed(data_path, args.message, dry_run=args.dry_run)
        _push(data_path, dry_run=args.dry_run)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())