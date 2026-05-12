"""Mechanical merge: inline V1 StockScanner into V2 StockScannerV2.

Mirror of tools/_merge_manager_v1_into_v2.py for the scanner pair:
    services/stock_scanner.py     (V1, ~724 lines, 12 methods + 4 NIFTY tier
                                    constants + _parse_price/_parse_int helpers)
    services/stock_scanner_v2.py  (V2 subclass, ~1985 lines, 18 methods)

Output:
    services/stock_scanner.py     (single class StockScanner, all V2 methods +
                                    every V1 method V2 didn't override; class
                                    rename + inheritance dropped)

Deletes:
    services/stock_scanner_v2.py

Idempotent — checks for the marker line.
"""

from __future__ import annotations

import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
V1_PATH = os.path.join(ROOT, "services", "stock_scanner.py")
V2_PATH = os.path.join(ROOT, "services", "stock_scanner_v2.py")
MERGED_MARKER = "# MERGED 2026-05-12 — inlined V1 StockScanner"


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def _split_methods(src: str, class_name: str) -> list[tuple[str, str]]:
    tree = ast.parse(src)
    out: list[tuple[str, str]] = []
    for node in tree.body:
        if not (isinstance(node, ast.ClassDef) and node.name == class_name):
            continue
        lines = src.splitlines(keepends=True)
        prev_end = node.lineno
        body = list(node.body)
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                and isinstance(body[0].value.value, str):
            prev_end = body[0].end_lineno
            body = body[1:]
        for member in body:
            if not isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            start = prev_end
            end = member.end_lineno
            chunk = "".join(lines[start:end])
            out.append((member.name, chunk))
            prev_end = end
    return out


def _module_preamble(src: str, class_name: str) -> str:
    """Everything BEFORE ``class class_name:`` — module docstring, imports,
    helper functions (``_parse_price``, ``_parse_int``), and module-level
    constants (NIFTY50 / NIFTY100_EXTRA / etc).
    """
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            preamble_end = node.lineno - 1   # 0-based exclusive
            return "".join(src.splitlines(keepends=True)[:preamble_end])
    raise SystemExit(f"class {class_name} not found in {src[:80]!r}")


def merge() -> None:
    v1_src = _read(V1_PATH)
    v2_src = _read(V2_PATH)

    if MERGED_MARKER in v1_src:
        print("Already merged — nothing to do.")
        return

    v1_preamble = _module_preamble(v1_src, "StockScanner")
    v2_preamble = _module_preamble(v2_src, "StockScannerV2")

    v1_methods = _split_methods(v1_src, "StockScanner")
    v2_methods = _split_methods(v2_src, "StockScannerV2")
    v2_method_names = {n for n, _ in v2_methods}
    v1_method_names = {n for n, _ in v1_methods}

    print(f"V1 scanner methods: {len(v1_methods)}")
    print(f"V2 scanner methods: {len(v2_methods)}")
    print(f"  overridden: {sorted(v2_method_names & v1_method_names)}")
    print(f"  new in V2 : {sorted(v2_method_names - v1_method_names)}")
    print(f"  V1-only   : {sorted(v1_method_names - v2_method_names)}")

    # ── Build the merged module ───────────────────────────────────
    # V1 preamble carries the NIFTY tier constants + _parse_price/_parse_int
    # helpers — keep it as the base.  Then strip the V1 docstring header and
    # replace with a unified one.  V2's preamble (constants like SECTOR_MAP,
    # MAX_CANDIDATES, MAX_PER_SECTOR) gets appended after V1 helpers.
    out: list[str] = []

    out.append(
        "# ================================================================\n"
        "# services/stock_scanner.py\n"
        "# ================================================================\n"
        "# Stock scanner — candle-pattern + technical-indicator pre-filter.\n"
        "#\n"
        "# WHAT IT DOES\n"
        "# ------------\n"
        "# Walks the configured stock universe (NIFTY 50 / 100 / 150 / 200) and:\n"
        "#   1. Fetches 15-min and daily candles from Zerodha (via candle\n"
        "#      cache so we re-use prior session data when available)\n"
        "#   2. Detects 14 candlestick patterns + 14 technical indicators\n"
        "#      (EMA, RSI, VWAP w/ bands, MACD, SuperTrend, ADX, ATR,\n"
        "#       StochRSI, BollingerBands, ORB, Gap, Hourly EMA, etc.)\n"
        "#   3. Computes a composite score (-25 .. +25) per symbol\n"
        "#   4. Filters by V2_MIN_SCORE, applies sector diversification,\n"
        "#      tape-breadth penalty, and Nifty hard-filter\n"
        "#   5. Returns the top-N candidates ready for OrderEngine.enter_trade\n"
        "#\n"
        "# Two scanning entry points:\n"
        "#   scan_noai(...)  — used by the default --noai path; pure rules,\n"
        "#                     zero Claude calls\n"
        "#   scan(...)       — used by --ai path; sends pre-filtered candidates\n"
        "#                     to Claude for final ranking + position-review\n"
        "#\n"
        f"{MERGED_MARKER}\n"
        "# (was: services/stock_scanner.py + services/stock_scanner_v2.py\n"
        "#  inheritance pair)\n"
        "# ================================================================\n"
        "\n"
    )

    # Take V1's preamble starting AFTER the module docstring.  We just
    # replaced that with our new header above.  Find the first import.
    v1_lines = v1_preamble.splitlines(keepends=True)
    # Skip until we hit the first ``import`` or ``from `` line.
    body_start = 0
    for i, ln in enumerate(v1_lines):
        s = ln.lstrip()
        if s.startswith("import ") or s.startswith("from "):
            body_start = i
            break
    out.append("".join(v1_lines[body_start:]))

    # Now append the V2-only preamble bits (everything in v2_preamble that
    # ISN'T already in v1_preamble — most importantly SECTOR_MAP and the
    # MAX_CANDIDATES / MAX_PER_SECTOR constants).  Simple line-set diff.
    v1_lineset = set(ln.strip() for ln in v1_preamble.splitlines() if ln.strip())
    v2_lines = v2_preamble.splitlines(keepends=True)
    extra: list[str] = []
    in_block = False
    for ln in v2_lines:
        s = ln.strip()
        if s and s in v1_lineset:
            # already in V1
            in_block = False
            continue
        # Drop V2's own module docstring and the ``from services.stock_scanner
        # import StockScanner`` line — that import is now circular.
        if "from services.stock_scanner" in s:
            continue
        # Drop V2's module docstring opener / closer / body if present.
        # (The first block is the docstring; we replaced it with the unified
        #  header above.)
        if s.startswith("# services/stock_scanner_v2.py"):
            in_block = True
            continue
        extra.append(ln)
    out.append("\n# ── extra constants/helpers from the former v2 module ──\n")
    out.append("".join(extra))

    out.append(
        "\n\n"
        "class StockScanner:\n"
        '    """Candle-pattern + indicator scanner. See module docstring."""\n'
        "\n"
    )

    # Merge methods: V1 order, V2 wins on overrides, V2-only methods appended.
    v1_by_name = dict(v1_methods)
    v2_by_name = dict(v2_methods)
    seen: set[str] = set()
    for name, _ in v1_methods:
        chunk = v2_by_name[name] if name in v2_by_name else v1_by_name[name]
        out.append(chunk)
        seen.add(name)
    for name, chunk in v2_methods:
        if name in seen:
            continue
        out.append(chunk)
        seen.add(name)

    merged = "".join(out)

    # Validate
    try:
        ast.parse(merged)
    except SyntaxError as e:
        sys.stderr.write(f"Merged scanner has SyntaxError: {e}\n")
        debug_path = os.path.join(ROOT, "services", "stock_scanner.py.merge-debug")
        with open(debug_path, "w", encoding="utf-8") as f:
            f.write(merged)
        sys.stderr.write(f"Wrote debug copy to {debug_path}\n")
        sys.exit(1)

    with open(V1_PATH, "w", encoding="utf-8") as f:
        f.write(merged)
    print(f"Wrote merged: {V1_PATH} ({len(merged.splitlines())} lines)")

    os.remove(V2_PATH)
    print(f"Deleted: {V2_PATH}")


if __name__ == "__main__":
    merge()
