"""One-shot mechanical merge: inline V1 PortfolioManager into V2 PortfolioManagerV2.

Reads:
    portfolio/manager.py     (V1 base, ~2228 lines, 37 methods)
    portfolio/manager_v2.py  (V2 subclass, ~1429 lines, 15 methods)

Writes:
    portfolio/manager.py     (merged: V2 methods + V1 methods V2 didn't override,
                              class renamed to PortfolioManager,
                              imports rewritten, V1 banner replaced)

Deletes:
    portfolio/manager_v2.py  (no longer needed)

The merge rules are:
  1. Take V2's class body as authoritative (it has the 5 overrides + 10 new
     methods we want to keep).
  2. Append every V1 method whose name is NOT defined in V2.
  3. Strip V1 deprecation banner; replace with the V2 banner.
  4. Update imports (drop StockScanner, keep StockScannerV2 — and we'll rename
     StockScannerV2 -> StockScanner in a separate scanner-merge pass).
  5. Class header becomes `class PortfolioManager:` (no inheritance).

Run once from the repo root:
    python tools/_merge_manager_v1_into_v2.py

The script is idempotent: rerunning on already-merged source does nothing
(checks for the marker line `# MERGED 2026-05-12`).
"""

from __future__ import annotations

import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
V1_PATH = os.path.join(ROOT, "portfolio", "manager.py")
V2_PATH = os.path.join(ROOT, "portfolio", "manager_v2.py")
MERGED_MARKER = "# MERGED 2026-05-12 — inlined V1 PortfolioManager"


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def _class_body_lines(src: str, class_name: str) -> tuple[list[str], int, int]:
    """Return (body_lines, start_line, end_line) for ``class_name``.

    Lines are 0-indexed positions within ``src.splitlines()``. Body lines
    INCLUDE the indentation, so a simple `''.join` after de-indenting by
    one level reconstructs the class without the header.
    """
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            start = node.lineno - 1            # ast.lineno is 1-based
            end = node.end_lineno - 1
            return src.splitlines(keepends=True)[start:end + 1], start, end
    raise SystemExit(f"class {class_name} not found")


def _method_names(src: str, class_name: str) -> set[str]:
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return {n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    return set()


def _split_methods(src: str, class_name: str) -> list[tuple[str, str]]:
    """Return [(method_name, raw_method_source), ...] preserving order
    and the comment/blank-line preamble immediately above each method.

    The preamble lets us keep the ``# ====...`` section banners that
    sit above each method block.
    """
    tree = ast.parse(src)
    out: list[tuple[str, str]] = []
    for node in tree.body:
        if not (isinstance(node, ast.ClassDef) and node.name == class_name):
            continue
        lines = src.splitlines(keepends=True)
        prev_end = node.lineno  # body starts on the line after the class header
        # Skip past the docstring if present
        body = list(node.body)
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                and isinstance(body[0].value.value, str):
            prev_end = body[0].end_lineno
            body = body[1:]
        for member in body:
            if not isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # class-level attribute (e.g. `_BEARISH_REVERSAL_PATTERNS = ...`).
                # We attach it to the *next* method via the preamble, so just
                # let the next iteration pull it in via `prev_end` math.
                continue
            start = prev_end                     # line right after previous block (1-based)
            end = member.end_lineno              # inclusive last line of this method (1-based)
            chunk = "".join(lines[start:end])    # preamble + method body
            out.append((member.name, chunk))
            prev_end = end
    return out


def merge() -> None:
    v1_src = _read(V1_PATH)
    v2_src = _read(V2_PATH)

    if MERGED_MARKER in v1_src:
        print("Already merged — nothing to do.")
        return

    v1_methods = _split_methods(v1_src, "PortfolioManager")
    v2_methods = _split_methods(v2_src, "PortfolioManagerV2")
    v2_method_names = {name for name, _ in v2_methods}

    print(f"V1 methods: {len(v1_methods)}")
    print(f"V2 methods: {len(v2_methods)} (of which {sum(1 for n,_ in v2_methods if n in {n2 for n2,_ in v1_methods})} override V1)")
    overridden = v2_method_names & {n for n, _ in v1_methods}
    new_in_v2 = v2_method_names - {n for n, _ in v1_methods}
    only_in_v1 = {n for n, _ in v1_methods} - v2_method_names
    print(f"  overridden: {sorted(overridden)}")
    print(f"  new in V2 : {sorted(new_in_v2)}")
    print(f"  V1-only   : {len(only_in_v1)} methods retained")

    # ── Build the merged module ───────────────────────────────────
    out: list[str] = []

    out.append(
        "# ================================================================\n"
        "# portfolio/manager.py\n"
        "# ================================================================\n"
        "# Intraday trading bot orchestrator.\n"
        "#\n"
        "# Single-class bot that runs the full trading day:\n"
        "#   1. Waits for pre-market time (MARKET_OPEN - PRE_MARKET_MINUTES_BEFORE)\n"
        "#   2. Logs into Zerodha, fetches account funds and budget regime\n"
        "#   3. Pre-market scan via the candle-pattern + indicator scanner\n"
        "#   4. Waits for market open + entry-delay observation window\n"
        "#   5. Enters positions that confirmed direction during observation\n"
        "#   6. Monitors prices in a loop:\n"
        "#        - Every PRICE_POLL_SECONDS (faster near SL/target)\n"
        "#        - Every V2_CANDLE_RESCAN_MINUTES: free re-scan of open\n"
        "#          positions for signal-reversal / decay / contrary-pattern\n"
        "#          exits and SL tightening\n"
        "#        - Every POSITION_REVIEW_MINUTES: stagnant-exit (NoAI) or\n"
        "#          Claude review (--ai mode)\n"
        "#        - Every OPPORTUNITY_RESCAN_MINUTES: scan for new trades\n"
        "#          when slots are free\n"
        "#   7. At SQUARE_OFF time (default 15:10 IST): close everything\n"
        "#   8. Generate full P&L report with taxes and charges\n"
        "#\n"
        "# Two operating modes (CLI flag — see main.py):\n"
        "#   --noai (default)  pure rule-based, zero Claude API calls\n"
        "#   --ai              Claude reviews scanner candidates + open positions\n"
        "#\n"
        "# Safety features (always on):\n"
        "#   - DRY_RUN mode: no real orders, simulated P&L on live prices\n"
        "#   - Circuit breaker, peak-drawdown, soft-stop, directional-pause,\n"
        "#     loss-streak guard — all in services/order_engine.py\n"
        "#   - Graceful shutdown: Ctrl+C squares off all positions first\n"
        "#   - Crash recovery: rehydrates open positions on restart\n"
        "#\n"
        f"{MERGED_MARKER}\n"
        "# (was: portfolio/manager.py + portfolio/manager_v2.py inheritance pair)\n"
        "# ================================================================\n"
        "\n"
    )

    # Imports — superset of V1 + V2, deduplicated, with V1's StockScanner
    # dropped (the candle-aware scanner becomes the only scanner, renamed
    # StockScannerV2 -> StockScanner in a follow-up scanner-merge pass).
    out.append(
        "import signal\n"
        "import sys\n"
        "import time\n"
        "import datetime\n"
        "import json\n"
        "import os\n"
        "\n"
        "from config                        import Config, now_ist\n"
        "from core.logger                   import Logger\n"
        "from core.zerodha_client           import ZerodhaClient\n"
        "from core.claude_client            import ClaudeClient\n"
        "from services.stock_scanner_v2     import StockScannerV2\n"
        "from services                      import candle_patterns\n"
        "from services.order_engine         import OrderEngine\n"
        "from services.report_writer        import ReportWriter\n"
        "from services.performance_tracker  import PerformanceTracker\n"
        "from services.technical_indicators import adx as _calc_adx\n"
        "\n"
        "\n"
        "class PortfolioManager:\n"
        '    """Intraday trading bot. See module docstring for the lifecycle."""\n'
        "\n"
    )

    # Take V2's __init__ + class-level attrs (e.g. _BEARISH_REVERSAL_PATTERNS).
    # Then append every V1 method NOT overridden by V2.
    # Then append every V2 method NOT in V1 (the new exit gates etc).
    v1_by_name = dict(v1_methods)
    v2_by_name = dict(v2_methods)
    seen: set[str] = set()

    # Order: walk V1 first (preserves the lifecycle reading order), but for
    # any method V2 overrides, emit V2's version. Then append V2-only methods
    # at the end (they're auxiliary exit gates / protective helpers).
    for name, _ in v1_methods:
        if name in v2_by_name:
            chunk = v2_by_name[name]
        else:
            chunk = v1_by_name[name]
        out.append(chunk)
        seen.add(name)

    for name, chunk in v2_methods:
        if name in seen:
            continue
        out.append(chunk)
        seen.add(name)

    # Inject V2 class-level attributes (frozensets) right before the method
    # they belong to. V2 has _BEARISH_REVERSAL_PATTERNS / _BULLISH_… right
    # above _signal_reversal_exit. Easiest: search the V2 source for those
    # assignments and prepend them to that method's chunk.
    full_src = "".join(out)
    if "_BEARISH_REVERSAL_PATTERNS" not in full_src:
        # Find them in V2 source and inject right before _signal_reversal_exit.
        attrs_block = (
            "    # Reversal pattern sets — single source of truth in\n"
            "    # services/candle_patterns.\n"
            "    _BEARISH_REVERSAL_PATTERNS = candle_patterns.BEARISH_REVERSAL_PATTERNS\n"
            "    _BULLISH_REVERSAL_PATTERNS = candle_patterns.BULLISH_REVERSAL_PATTERNS\n"
            "\n"
        )
        # Locate _signal_reversal_exit and insert attrs immediately above it.
        marker_def = "def _signal_reversal_exit"
        joined = "".join(out)
        pos = joined.find(marker_def)
        if pos != -1:
            # Walk back to find the start of the section comment block above
            # the method (or the start of the line if no comment).
            section_start = joined.rfind("    # =", 0, pos)
            if section_start == -1:
                section_start = joined.rfind("\n", 0, pos) + 1
            joined = joined[:section_start] + attrs_block + joined[section_start:]
            out = [joined]

    merged = "".join(out)

    # Validate by parsing
    try:
        ast.parse(merged)
    except SyntaxError as e:
        sys.stderr.write(f"Merged source has SyntaxError: {e}\n")
        # Dump for debugging
        debug_path = os.path.join(ROOT, "portfolio", "manager.py.merge-debug")
        with open(debug_path, "w", encoding="utf-8") as f:
            f.write(merged)
        sys.stderr.write(f"Wrote debug copy to {debug_path}\n")
        sys.exit(1)

    # Write the merged result + delete V2 file
    with open(V1_PATH, "w", encoding="utf-8") as f:
        f.write(merged)
    print(f"Wrote merged: {V1_PATH} ({len(merged.splitlines())} lines)")

    os.remove(V2_PATH)
    print(f"Deleted: {V2_PATH}")


if __name__ == "__main__":
    merge()
