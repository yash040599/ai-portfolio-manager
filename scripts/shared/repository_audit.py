"""Read-only publication and dataset checks; never print matched private values."""

from __future__ import annotations

import argparse
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import subprocess


PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEXT_SUFFIXES = {".py", ".md", ".txt", ".json", ".csv", ".yml", ".yaml", ".toml", ".ini", ".cfg", ".sh", ".ps1", ".sql"}


@dataclass(frozen=True)
class Finding:
    repository: str
    path: str
    category: str
    line: int | None = None


class PublicationAudit:
    """Audit tracked and publishable new files, respecting each repo's privacy role."""

    def __init__(self, root: Path, *, private: bool = False, identifiers: tuple[str, ...] = ()):
        self.root = root.resolve()
        self.private = private
        self.identifiers = tuple(value.lower() for value in identifiers if value)

    @staticmethod
    def credential_path(path: str) -> bool:
        for part in Path(path).parts:
            name = part.lower()
            if name.startswith(("access_token", "credentials", "id_rsa", "id_ed25519")):
                return True
            if name.startswith(".env") and name != ".env.example":
                return True
        return Path(path).suffix.lower() in {".pem", ".key"}

    def files(self) -> list[str]:
        result = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=self.root, check=True, capture_output=True,
        )
        return sorted(set(result.stdout.decode("utf-8").rstrip("\0").split("\0")) - {""})

    def inspect_text(self, path: str, text: str) -> list[Finding]:
        findings = []
        for line_number, line in enumerate(text.splitlines(), 1):
            if any(identifier in line.lower() for identifier in self.identifiers):
                findings.append(Finding(self.root.name, path, "personal_identifier", line_number))
            if re.search(r"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|AIza[A-Za-z0-9_-]{30,}|-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----)", line):
                findings.append(Finding(self.root.name, path, "credential_content", line_number))
            if re.search(r"(?:api[_-]?key|api[_-]?secret|access[_-]?token|password|totp_secret)\s*[=:]\s*[\"'][A-Za-z0-9_+/=-]{20,}[\"']", line, re.IGNORECASE):
                findings.append(Finding(self.root.name, path, "credential_assignment", line_number))
            if re.search(r"[A-Za-z]:[\\/]+Users[\\/]+[A-Za-z0-9_.-]+", line):
                findings.append(Finding(self.root.name, path, "personal_machine_path", line_number))
            if re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", line) and "git@github.com" not in line:
                if not re.search(r"@(example\.(com|org|net)|[\w.-]+\.invalid)\b", line, re.IGNORECASE):
                    findings.append(Finding(self.root.name, path, "email_address", line_number))
        return findings

    def run(self) -> list[Finding]:
        findings = []
        for relative in self.files():
            path = self.root / relative
            if self.credential_path(relative):
                findings.append(Finding(self.root.name, relative, "credential_file"))
                continue
            if self.private or not path.is_file():
                continue
            if relative.split("/")[0] in {"data", "reports", "logs"}:
                findings.append(Finding(self.root.name, relative, "operational_data_in_public_repo"))
            if path.suffix.lower() in TEXT_SUFFIXES and path.stat().st_size < 5_000_000:
                findings.extend(self.inspect_text(relative, path.read_text(encoding="utf-8", errors="replace")))
            if path.suffix.lower() in {".db", ".sqlite"}:
                try:
                    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
                        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    if tables & {"trades", "intraday_tax_ledger", "capital_gains_ledger", "portfolio_analyses", "positions", "mf_holdings"}:
                        findings.append(Finding(self.root.name, relative, "personal_ledger_in_public_repo"))
                except sqlite3.DatabaseError:
                    findings.append(Finding(self.root.name, relative, "database_not_materialized"))
        return findings


class BacktestManifestAudit:
    """Compare declared data versions with local checksums and read-only SQLite facts."""

    def __init__(self, root: Path):
        self.root = root.resolve()

    @staticmethod
    def checksum(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def run(self) -> dict:
        manifest = json.loads((self.root / "manifest.json").read_text(encoding="utf-8"))
        issues = []
        coverage = []
        for relative, expected in manifest.get("checksums", {}).items():
            path = (self.root / relative).resolve()
            if not path.is_relative_to(self.root) or not path.is_file():
                issues.append({"path": relative, "category": "missing_dataset_file"})
            elif self.checksum(path) != expected:
                issues.append({"path": relative, "category": "checksum_mismatch"})
        declared = {interval["file"]: interval for interval in manifest.get("intervals", [])}
        for path in sorted((self.root / "candles").glob("*.sqlite")):
            relative = path.relative_to(self.root).as_posix()
            try:
                with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
                    integrity = connection.execute("PRAGMA quick_check").fetchone()[0]
                    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    if "candles" not in tables:
                        issues.append({"path": relative, "category": "missing_candle_table"})
                        continue
                    rows, symbols, first, last = connection.execute(
                        "SELECT COUNT(*), COUNT(DISTINCT symbol), MIN(ts_ist), MAX(ts_ist) FROM candles"
                    ).fetchone()
                actual = {"file": relative, "rows": rows, "symbols": symbols, "start": first, "end": last, "integrity": integrity}
                coverage.append(actual)
                if integrity != "ok":
                    issues.append({"path": relative, "category": "sqlite_integrity"})
                expected = declared.get(relative)
                if expected is None:
                    issues.append({"path": relative, "category": "undeclared_candle_store"})
                elif rows != expected.get("rows") or symbols != expected.get("symbols"):
                    issues.append({"path": relative, "category": "coverage_mismatch"})
                elif first != expected.get("date_range", {}).get("start") or last != expected.get("date_range", {}).get("end"):
                    issues.append({"path": relative, "category": "date_range_mismatch"})
            except sqlite3.DatabaseError:
                issues.append({"path": relative, "category": "unreadable_sqlite_or_lfs_pointer"})
        return {"dataset_version": manifest.get("dataset_version"), "coverage": coverage, "issues": issues}

    def refresh_inventory(self) -> dict:
        """Explicitly update metadata only; never rebuild or replace market data."""
        report = self.run()
        blocking = {"missing_dataset_file", "sqlite_integrity", "missing_candle_table", "unreadable_sqlite_or_lfs_pointer"}
        if any(issue["category"] in blocking for issue in report["issues"]):
            raise ValueError("Dataset files must be intact before refreshing the inventory.")
        path = self.root / "manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        now = datetime.now(timezone(timedelta(hours=5, minutes=30)))
        coverage = report["coverage"]
        symbols = set()
        for item in coverage:
            with closing(sqlite3.connect((self.root / item["file"]).as_uri() + "?mode=ro", uri=True)) as connection:
                symbols.update(row[0] for row in connection.execute("SELECT DISTINCT symbol FROM candles"))
        names = {"daily.sqlite": "day", "intraday_15m.sqlite": "15minute", "intraday_5m.sqlite": "5minute"}
        manifest["dataset_version"] = f"inventory-{now.date()}"
        manifest["status"] = "historical_research_snapshot"
        manifest["inventory_updated_at_ist"] = now.isoformat(timespec="seconds")
        manifest["inventory_updated_by"] = "scripts/shared/repository_audit.py --refresh-manifest"
        manifest["universe"] = {
            "name": "observed_candle_symbols", "symbols": len(symbols),
            "policy": "Observed dataset coverage, not historical index membership. Symbol CSVs retain their own effective dates.",
        }
        manifest["intervals"] = [
            {"name": names[Path(item["file"]).name], "file": item["file"], "rows": item["rows"],
             "symbols": item["symbols"], "date_range": {"start": item["start"], "end": item["end"]}}
            for item in coverage
        ]
        manifest["date_range"] = {"start": min(item["start"] for item in coverage), "end": max(item["end"] for item in coverage)}
        paths = set(manifest.get("checksums", {})) | {item["file"] for item in coverage}
        manifest["checksums"] = {relative: self.checksum(self.root / relative) for relative in sorted(paths)}
        manifest["notes"] = [
            "Inventory reconciled from existing local databases; no candles downloaded, rebuilt or removed.",
            "The original source and generated_by blocks describe the May 2026 seed, not the later expanded stores.",
            "Candle source columns retain row provenance; complete later acquisition history is not reconstructed here.",
            "The inventory date is not the latest market-data date; use each interval's date_range.",
            "Current universe metadata is not historical membership; survivorship and corporate-action limitations remain.",
            "Research data is not proof of strategy profitability or permission to redistribute provider data.",
        ]
        path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        return self.run()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all-repos", action="store_true")
    parser.add_argument("--identifier", action="append", default=[])
    parser.add_argument("--check-manifest", action="store_true")
    parser.add_argument("--refresh-manifest", action="store_true",
                        help="Explicitly reconcile backtest inventory/checksums without changing datasets.")
    args = parser.parse_args()
    repos = [(PROJECT_ROOT, False)]
    if args.all_repos:
        repos.extend((PROJECT_ROOT.parent / name, private) for name, private in (
            ("ai-portfolio-backtest-data", False), ("ai-portfolio-manager-data", True)))
    findings = []
    for root, private in repos:
        if not (root / ".git").exists():
            findings.append(Finding(root.name, ".git", "repository_missing"))
            continue
        findings.extend(PublicationAudit(root, private=private, identifiers=tuple(args.identifier)).run())
    output = {"findings": [asdict(finding) for finding in findings]}
    if args.check_manifest or args.refresh_manifest:
        audit = BacktestManifestAudit(PROJECT_ROOT.parent / "ai-portfolio-backtest-data")
        output["backtest"] = audit.refresh_inventory() if args.refresh_manifest else audit.run()
    print(json.dumps(output, indent=2))
    return int(bool(findings or output.get("backtest", {}).get("issues")))


if __name__ == "__main__":
    raise SystemExit(main())