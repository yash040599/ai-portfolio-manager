import unittest
from contextlib import closing
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory

from scripts.shared.backup_data import LocalSnapshotBackup, merge_databases, should_skip, sync_items
from scripts.shared.repository_audit import PublicationAudit
from scripts.trade.export_backtest_data import _append_candle_store


class TestBacktestAppend(unittest.TestCase):
    def test_existing_candles_survive_overlapping_sources(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "candles.sqlite"
            original = ("DEMO", None, "NSE", "2026-01-01T09:15:00+05:30", "15minute", 100, 102, 99, 101, 10, "original", "2026-01-02")
            duplicate = original[:8] + (999, 10, "cache", "2026-01-03")
            added = original[:3] + ("2026-01-02T09:15:00+05:30",) + original[4:]
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute("CREATE TABLE candles (symbol TEXT, instrument_token TEXT, exchange TEXT, ts_ist TEXT, interval TEXT, open REAL, high REAL, low REAL, close REAL, volume INTEGER, source TEXT, created_at_ist TEXT, PRIMARY KEY (symbol, interval, ts_ist, source))")
                connection.execute("INSERT INTO candles VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", original)

            self.assertEqual(_append_candle_store(path, [duplicate, added]), 1)
            self.assertEqual(_append_candle_store(path, [duplicate, added]), 0)
            with closing(sqlite3.connect(path)) as connection:
                rows = connection.execute("SELECT close, source FROM candles ORDER BY ts_ist").fetchall()
            self.assertEqual(rows, [(101.0, "original"), (101.0, "original")])


class TestPublicationAudit(unittest.TestCase):
    def test_personal_matches_report_locations_without_values(self):
        audit = PublicationAudit(Path.cwd(), identifiers=("demo-owner",))
        address = "private" + "@" + "sample.org"
        findings = audit.inspect_text("example.md", "Owner: demo-owner\nContact: " + address)

        self.assertEqual([finding.category for finding in findings], ["personal_identifier", "email_address"])
        self.assertNotIn("demo-owner", str(findings))
        self.assertNotIn(address, str(findings))

    def test_example_credentials_are_allowed_but_token_backups_are_not(self):
        self.assertFalse(PublicationAudit.credential_path(".env.example"))
        self.assertTrue(PublicationAudit.credential_path("data/access_token.json.invalid"))

    def test_windows_and_macos_home_paths_are_reported_without_values(self):
        audit = PublicationAudit(Path.cwd())
        homes = (
            "\\".join(("C:", "Users", "demo-user")),
            "/".join(("C:", "Users", "demo-user")),
            "/" + "/".join(("Users", "demo-user")),
        )
        for home in homes:
            with self.subTest(home=home):
                findings = audit.inspect_text("example.md", "Report: " + home + "/portfolio")
                self.assertEqual([finding.category for finding in findings], ["personal_machine_path"])
                self.assertNotIn(home, str(findings))


class TestBackupPrivacy(unittest.TestCase):
    def test_snapshot_preserves_previous_database_and_never_merges_into_source(self):
        with TemporaryDirectory() as folder:
            source = Path(folder) / "source"
            destination = Path(folder) / "backup"
            for root, value in ((source, "local"), (destination, "remote")):
                (root / "data").mkdir(parents=True)
                with closing(sqlite3.connect(root / "data" / "book.db")) as connection, connection:
                    connection.execute("CREATE TABLE holdings (name TEXT)")
                    connection.execute("INSERT INTO holdings VALUES (?)", (value,))
            (source / "data" / "access_token.json.invalid").write_text("synthetic", encoding="utf-8")
            (destination / "data" / "remote-only.txt").write_text("keep", encoding="utf-8")

            snapshot = LocalSnapshotBackup(str(source), str(destination))
            self.assertEqual(snapshot.sync(), 1)
            self.assertEqual(snapshot.sync(), 0)
            self.assertTrue((destination / "data" / "remote-only.txt").exists())
            self.assertFalse((destination / "data" / "access_token.json.invalid").exists())
            for path, expected in ((source / "data" / "book.db", "local"),
                                   (destination / "data" / "book.db", "local"),
                                   (destination / "reports" / "backup_history" / snapshot.stamp / "data" / "book.db", "remote")):
                with closing(sqlite3.connect(path)) as connection:
                    self.assertEqual(connection.execute("SELECT name FROM holdings").fetchone()[0], expected)

    def test_unknown_database_schema_is_not_silently_overwritten(self):
        with TemporaryDirectory() as folder:
            paths = [Path(folder) / name for name in ("local.db", "remote.db")]
            for path in paths:
                with closing(sqlite3.connect(path)) as connection, connection:
                    connection.execute("CREATE TABLE holdings (name TEXT)")
            with self.assertRaisesRegex(ValueError, "without a merge contract"):
                merge_databases(str(paths[0]), str(paths[1]), False)

    def test_secrets_and_transient_files_are_never_synced(self):
        for name in (".env", ".env.local", "access_token.json", "access_token.json.invalid",
                     "access_token.json.bak", "credentials.json", "id_rsa", "client.pem",
                     "trades.db-wal", "trades.db-shm", "cache.sqlite-journal"):
            with self.subTest(name=name):
                self.assertTrue(should_skip(name))

    def test_operational_data_remains_eligible_for_private_backup(self):
        for name in ("trades.db", "fundamentals_seed.json", "README.md", "report.json"):
            with self.subTest(name=name):
                self.assertFalse(should_skip(name))

    def test_environment_backup_is_rejected_even_for_legacy_callers(self):
        with self.assertRaisesRegex(ValueError, "Secrets cannot be backed up"):
            sync_items(True)
        self.assertNotIn(".env", sync_items(False))


if __name__ == "__main__":
    unittest.main()