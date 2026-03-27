# ================================================================
# core/logger.py
# ================================================================
# Centralised logging used by every class in the project.
#
# Writes coloured output to the terminal AND appends to a rotating
# log file at logs/portfolio.log (max 5MB, keeps last 3 files).
#
# Usage from any class:
#   from core.logger import Logger
#   self.log = Logger("ClassName")
#   self.log.info("plain message")
#   self.log.success("green ✓ message")
#   self.log.warning("yellow ⚠ message")
#   self.log.error("red ✗ message")
#   self.log.section("SECTION TITLE")
# ================================================================

import os
import logging
from logging.handlers import RotatingFileHandler


class Logger:

    # ANSI colour codes
    _GREEN  = "\033[92m"
    _YELLOW = "\033[93m"
    _RED    = "\033[91m"
    _BOLD   = "\033[1m"
    _RESET  = "\033[0m"

    # Shared rotating file handler — created once, reused by all instances
    _file_handler: RotatingFileHandler | None = None

    def __init__(self, name: str):
        self._name   = name
        self._logger = logging.getLogger(name)
        self._logger.setLevel(logging.DEBUG)

        # Attach shared file handler on first Logger instantiation
        if Logger._file_handler is None:
            os.makedirs("logs", exist_ok=True)
            Logger._file_handler = RotatingFileHandler(
                "logs/portfolio.log",
                maxBytes    = 5 * 1024 * 1024,   # 5 MB per file
                backupCount = 3,                   # keep last 3 rotated files
                encoding    = "utf-8",
            )
            Logger._file_handler.setFormatter(
                logging.Formatter(
                    "%(asctime)s [%(name)-20s] %(levelname)-8s — %(message)s"
                )
            )

        if Logger._file_handler not in self._logger.handlers:
            self._logger.addHandler(Logger._file_handler)

    # ── Public methods ────────────────────────────────────────────

    def info(self, message: str):
        """Plain informational message — no colour prefix."""
        self._clear_status_line()
        print(f"  {message}")
        self._logger.info(message)

    def success(self, message: str):
        """Green ✓ — for completed actions."""
        self._clear_status_line()
        self._print(self._GREEN, "✓", message)
        self._logger.info(f"SUCCESS — {message}")

    def warning(self, message: str):
        """Yellow ⚠ — for non-fatal issues."""
        self._clear_status_line()
        self._print(self._YELLOW, "⚠", message)
        self._logger.warning(message)

    def error(self, message: str):
        """Red ✗ — for failures."""
        self._clear_status_line()
        self._print(self._RED, "✗", message)
        self._logger.error(message)

    def section(self, title: str):
        """Prints a prominent section header."""
        self._clear_status_line()
        bar = "=" * 58
        print(f"\n{bar}\n  {title}\n{bar}")
        self._logger.info(f"=== {title} ===")

    def blank(self):
        """Prints a blank line — for visual spacing in terminal."""
        self._clear_status_line()
        print()

    # ── Internal helpers ──────────────────────────────────────────

    @staticmethod
    def _clear_status_line():
        """Erase the in-place status line so the next print starts clean."""
        print(f"\r{' ' * 100}\r", end="", flush=True)

    def _print(self, colour: str, symbol: str, message: str):
        bold_sym = f"{self._BOLD}{symbol}{self._RESET}"
        print(f"  {colour}{bold_sym}{self._RESET} {message}")
