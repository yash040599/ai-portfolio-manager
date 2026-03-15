# ================================================================
# services/analysis_queue.py
# ================================================================
# Per-stock Claude analysis queue with auto-retry and interactive
# failure resolution.
#
# Also owns:
#   - Prompt building (depth scales with claude_plan)
#   - Response parsing into standardised fields
#   - Output format schema (the template Claude must follow)
#
# Status lifecycle per stock:
#   pending → (auto-retry ×3) → done
#                              → failed → (user: r/s/q) → done / skipped
#
# One failure affects exactly one stock — the rest continue.
# ================================================================

import re
import time
import datetime

from config             import Config
from core.logger        import Logger
from core.claude_client import ClaudeClient


# ================================================================
# OUTPUT FORMAT SCHEMA
# ================================================================
# The exact template Claude must return for every stock.
# AnalysisQueue._parse() extracts these fields — because the schema
# is strict, every stock in the report looks identical regardless
# of which API call produced it.
# ================================================================

OUTPUT_FORMAT = """
You MUST use EXACTLY this format. No text before or after.

ACTION: [HOLD | AVERAGE DOWN | PARTIAL EXIT | FULL EXIT | ADD MORE]
CONVICTION: [Low | Medium | High]
REASONING: [2-4 sentences, plain English, no jargon]
HORIZON: [Short (<6 months) | Medium (6-18 months) | Long (2-3 years)]
TARGET_PRICE: [specific ₹ value or range e.g. ₹450-500]
RISKS:
1. [first risk]
2. [second risk]
3. [third risk]
WATCH: [one specific trigger or event to monitor]
NEXT_STEPS:
1. [first concrete actionable step e.g. "Average down if price drops below ₹X"]
2. [second step e.g. "Set stop-loss at ₹Y" or "Book partial profits above ₹Z"]
---END---
"""

EXPECTED_FIELDS = ["ACTION", "CONVICTION", "REASONING", "HORIZON", "TARGET_PRICE", "RISKS", "WATCH", "NEXT_STEPS"]
VALID_ACTIONS   = {"HOLD", "AVERAGE DOWN", "PARTIAL EXIT", "FULL EXIT", "ADD MORE"}


class AnalysisQueue:

    MAX_RETRIES = 3   # auto-retry attempts before asking the user
    RETRY_DELAY = 3   # seconds to wait between auto-retries
    STOCK_PAUSE = 1   # seconds between stocks (rate limit buffer)

    def __init__(self, config: type[Config], claude: ClaudeClient, log: Logger):
        self.cfg    = config
        self.claude = claude
        self.log    = log
        self._queue: list[dict] = []

    # ================================================================
    # LOAD
    # ================================================================

    def load(self, portfolio: list[dict]):
        """
        Loads the portfolio into the queue.
        Every stock starts with status = "pending".
        """
        self._queue = [
            {
                "stock":    stock,
                "status":   "pending",   # pending | done | failed | skipped
                "result":   None,        # raw Claude response text
                "parsed":   None,        # structured dict from _parse()
                "error":    None,        # last plain-English error message
                "attempts": 0,           # total API call attempts made
            }
            for stock in portfolio
        ]

    # ================================================================
    # RUN
    # ================================================================

    def run(self) -> tuple[list[dict], list[str], list[dict]]:
        """
        Runs the full two-pass analysis.

        Pass 1 — Automatic:
          Every stock is sent to Claude with up to MAX_RETRIES attempts.
          Transient errors (rate limits, overloads) are retried automatically.
          Permanent errors (bad key, no credit) skip retries immediately.

        Pass 2 — Interactive (only if Pass 1 left failures):
          Shows a red summary of failed stocks. For each one, asks:
            r → retry once more right now
            s → skip this stock (logged in report)
            q → skip this and all remaining failed stocks

        Returns: (analyses, skipped_symbols, failed_log)
        """
        total = len(self._queue)
        self.log.section(f"CLAUDE ANALYSIS — {total} stocks  [{self.cfg.claude()['model']}]")

        self._run_pass1(total)

        if self.failed():
            self._run_pass2()

        self._print_progress()
        return self._collect_results()

    # ================================================================
    # PASS 1 — AUTOMATIC
    # ================================================================

    def _run_pass1(self, total: int):
        for i, entry in enumerate(self._queue):
            self._analyse_with_retry(entry, i + 1, total)
            time.sleep(self.STOCK_PAUSE)

    def _analyse_with_retry(self, entry: dict, pos: int, total: int):
        """
        Tries one stock up to MAX_RETRIES times.
        Prints inline progress: [1/15] TCS ✓ done
        """
        symbol = entry["stock"]["symbol"]

        for attempt in range(1, self.MAX_RETRIES + 1):
            entry["attempts"] = attempt

            label = (
                f"[{pos}/{total}] {symbol}"
                if attempt == 1
                else f"[{pos}/{total}] {symbol} retry {attempt}/{self.MAX_RETRIES}"
            )
            print(f"  {label} ", end="", flush=True)

            if attempt > 1:
                time.sleep(self.RETRY_DELAY)

            ok, error = self._call_claude(entry)

            if ok:
                print("\033[92m✓ done\033[0m")
                return
            else:
                print("\033[91m✗\033[0m ", end="", flush=True)
                if not ClaudeClient.is_retryable(error):
                    break   # Permanent error — retrying won't help

        print()
        self.log.error(f"FAILED: {symbol} — {entry['error']}")

    def _call_claude(self, entry: dict) -> tuple[bool, str | None]:
        """
        Sends one stock's prompt to Claude and parses the response.
        Updates entry in place on success.
        Returns (success, error_message_or_None).
        """
        prompt = self._build_prompt(entry["stock"])

        try:
            raw    = self.claude.call(prompt)
            parsed = self._parse(raw, entry["stock"]["symbol"])
            entry.update(status="done", result=raw, parsed=parsed)
            return True, None

        except Exception as e:
            error = ClaudeClient.classify_error(e)
            entry.update(status="failed", error=error)
            return False, error

    # ================================================================
    # PASS 2 — INTERACTIVE
    # ================================================================

    def _run_pass2(self):
        """
        Shows failed stocks in red and asks the user what to do
        with each one: retry (r), skip (s), or skip all (q).
        """
        self.log.section("FAILED STOCKS — MANUAL RESOLUTION")
        for e in self.failed():
            self.log.error(f"{e['stock']['symbol']} — {e['error']}")

        self._print_progress()
        print("\nSome stocks failed after all auto-retries. Handle them one by one.\n")

        for entry in list(self.failed()):
            if entry["status"] != "failed":
                continue   # Already resolved by a prior 'q'

            symbol = entry["stock"]["symbol"]
            print(f"  \033[91m✗ \033[1m{symbol}\033[0m")
            print(f"    Reason  : {entry['error']}")
            print(f"    Options : \033[1mr\033[0m retry  \033[1ms\033[0m skip  \033[1mq\033[0m skip all remaining")

            while True:
                try:
                    choice = input("    Choice [r/s/q]: ").strip().lower()
                except (EOFError, KeyboardInterrupt):
                    choice = "q"

                if choice == "r":
                    entry.update(status="pending", error=None)
                    ok, error = self._call_claude(entry)
                    if ok:
                        self.log.success(f"{symbol} succeeded on manual retry")
                        break
                    else:
                        self.log.error(f"{symbol} failed again: {error}")
                        print(f"    Options : \033[1mr\033[0m retry  \033[1ms\033[0m skip  \033[1mq\033[0m skip all")
                        continue

                elif choice == "s":
                    entry["status"] = "skipped"
                    self.log.warning(f"{symbol} skipped — noted in report")
                    break

                elif choice == "q":
                    entry["status"] = "skipped"
                    for remaining in self.failed():
                        remaining["status"] = "skipped"
                    self.log.warning("All remaining failed stocks skipped")
                    break

                else:
                    print("    Please enter r, s, or q")

    # ================================================================
    # STATUS HELPERS
    # ================================================================

    def _by_status(self, status: str) -> list[dict]:
        return [e for e in self._queue if e["status"] == status]

    def pending(self)  -> list[dict]: return self._by_status("pending")
    def done(self)     -> list[dict]: return self._by_status("done")
    def failed(self)   -> list[dict]: return self._by_status("failed")
    def skipped(self)  -> list[dict]: return self._by_status("skipped")

    def _print_progress(self):
        d = len(self.done());    f = len(self.failed())
        s = len(self.skipped()); p = len(self.pending())
        t = len(self._queue)
        print(
            f"\n  Progress: \033[92m{d} done\033[0m · "
            f"\033[91m{f} failed\033[0m · "
            f"\033[93m{s} skipped\033[0m · "
            f"{p} pending  (total: {t})\n"
        )

    def _collect_results(self) -> tuple[list[dict], list[str], list[dict]]:
        """Packages queue results into the three return values."""
        analyses = [
            {
                "symbol":   e["stock"]["symbol"],
                "stock":    e["stock"],    # full data dict — used by ReportWriter
                "raw":      e["result"],   # raw Claude text  — saved to JSON
                "parsed":   e["parsed"],   # structured fields — used in report
                "attempts": e["attempts"],
            }
            for e in self.done()
        ]
        skipped_symbols = [e["stock"]["symbol"] for e in self.skipped()]
        failed_log      = [
            {"symbol": e["stock"]["symbol"], "error": e["error"]}
            for e in self.failed()
        ]
        return analyses, skipped_symbols, failed_log

    # ================================================================
    # PROMPT BUILDER
    # ================================================================

    def _build_prompt(self, stock: dict) -> str:
        """
        Builds the analysis prompt for one stock.

        Three things scale with claude_plan (set in config.py):
          1. Data depth  — basic plan omits PE ratios; pro/max include them
          2. Instruction — basic is concise; full is institutional-grade
          3. Today's date is always injected so Claude doesn't use
             stale training memory for prices or market conditions
        """
        plan  = self.cfg.claude()
        today = datetime.date.today().strftime("%B %d, %Y")

        # Stock data block
        data  = f"Analysis date  : {today} (use this as today — ignore training memory for prices)\n\n"
        data += f"Stock          : {stock['symbol']} ({stock.get('exchange','NSE')})\n"
        data += f"Qty held       : {stock['quantity']} shares\n"
        data += f"Avg buy price  : ₹{stock['avg_buy_price']}\n"
        data += f"Current price  : ₹{stock['current_price']}  [source: {stock.get('price_source','')}]\n"
        data += f"P&L            : ₹{stock['pnl']} ({stock['pnl_percent']}%)\n"
        data += f"52-week range  : ₹{stock.get('52w_low','N/A')} – ₹{stock.get('52w_high','N/A')}\n"
        data += f"1-year trend   : {stock.get('price_trend','Unknown')}\n"
        data += f"30-day momentum: {stock.get('momentum','Unknown')}\n"
        data += f"Sector         : {stock.get('sector','Unknown')}\n"

        if plan["include_pe_ratios"]:
            data += f"P/E ratio      : {stock.get('pe_ratio','N/A')}\n"
            data += f"P/B ratio      : {stock.get('pb_ratio','N/A')}\n"
            data += f"Market cap     : ₹{stock.get('market_cap_cr','N/A')} Cr\n"

        # Action guide — always included so Claude picks the right action
        action_guide = (
            "ACTION GUIDE — pick the single best action:\n"
            "  HOLD         : Keep position as-is. Fundamentals intact, no urgency to act.\n"
            "  AVERAGE DOWN : Stock is beaten down but fundamentals are strong — buy more at current "
            "levels to lower avg cost. Specify buy price levels in NEXT_STEPS.\n"
            "  ADD MORE     : Stock is performing well and has more upside — increase position size. "
            "Specify entry price and allocation in NEXT_STEPS.\n"
            "  PARTIAL EXIT : Take some profits or reduce risk. Specify how much to sell (e.g. 25-50%) "
            "and at what price in NEXT_STEPS.\n"
            "  FULL EXIT    : Sell entire position — broken thesis, better alternatives, or terminal decline.\n\n"
            "CONVICTION — how confident you are in the action:\n"
            "  Low    : Uncertain, could go either way, limited data.\n"
            "  Medium : Reasonable confidence, some risks remain.\n"
            "  High   : Strong conviction based on clear evidence.\n\n"
            "NEXT_STEPS — give 2 concrete, actionable steps the investor should take right now. "
            "Examples: specific price levels to buy/sell at, stop-loss levels, profit booking targets, "
            "SIP amounts, rebalance triggers, or upcoming events to wait for before acting.\n"
        )

        # Instruction depth
        depth = plan["analysis_depth"]
        if depth == "basic":
            instruction = (
                "Analyse this Indian stock for a retail investor. "
                "Give a clear action, plain-language reasoning, time horizon, "
                "price target, key risks, and one event to watch."
            )
        elif depth == "detailed":
            instruction = (
                "Analyse this Indian stock in detail. Cover price trend, sector outlook, "
                "and valuation. State if the P&L changes your recommendation. "
                "Give a realistic price target. Plain English."
            )
        else:  # full
            instruction = (
                "Provide a thorough analysis. Cover momentum, sector macro outlook, "
                "valuation vs peers, and whether the investor should rebalance. "
                "Give a target range with bull and bear scenarios. Plain English."
            )

        return (
            f"You are an experienced Indian stock market analyst (NSE/BSE).\n\n"
            f"{instruction}\n\n"
            f"{action_guide}"
            f"STOCK DATA:\n{data}\n"
            f"REQUIRED OUTPUT FORMAT (follow exactly):\n{OUTPUT_FORMAT}"
        )

    # ================================================================
    # RESPONSE PARSER
    # ================================================================

    @staticmethod
    def _parse(raw_text: str, symbol: str) -> dict:
        """
        Extracts standardised fields from Claude's response.

        Tolerant of minor variations: extra spaces, mixed case,
        Claude adding qualifiers to the ACTION field.

        Any field Claude omits is filled with "[Not provided]"
        so ReportWriter never encounters a KeyError or blank section.
        """
        text   = raw_text.strip()
        parsed = {}

        for field in EXPECTED_FIELDS:
            # Match FIELDNAME: then capture until next field label or ---END---
            pattern = rf"(?i){field}\s*:\s*(.*?)(?=\n[A-Z_]{{3,}}\s*:|---END---|$)"
            match   = re.search(pattern, text, re.DOTALL)

            if match:
                value = match.group(1).strip()
                value = re.sub(r'\s*---END---.*$', '', value, flags=re.DOTALL).strip()
                parsed[field] = value if value else "[Not provided]"
            else:
                parsed[field] = "[Not provided]"

        # Normalise ACTION — Claude sometimes adds e.g. "HOLD (with caution)"
        action = parsed.get("ACTION", "").upper().strip()
        if action not in VALID_ACTIONS:
            for valid in VALID_ACTIONS:
                if valid in action:
                    parsed["ACTION"] = valid
                    break
            else:
                parsed["ACTION"] = f"{parsed['ACTION']} ⚠️"   # flag for review

        return parsed
