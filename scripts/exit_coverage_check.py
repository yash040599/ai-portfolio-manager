"""
Exit-gate coverage check.

WHY THIS EXISTS
---------------
On 2026-04-28 a real live loss was traced to a *cross-gate dead zone*
between two exit gates that each looked correct in isolation:

  * `_signal_reversal_exit` (#174) requires |fresh|>=SIGNAL_REVERSAL_SCORE
    AND a confirming reversal candle pattern.
  * `_signal_decay_exit` (#188) had a same-sign requirement that
    deferred any flipped-sign case to #174.

Together they left soft sign flips (e.g. entry +10 -> fresh -3 with no
candle pattern) caught by NEITHER gate. Each gate's docstring asserted
the other handled the case. Nobody enumerated the actual cross-product
to verify. That class of bug is the target of this script.

WHAT IT DOES
------------
For a representative entry conviction and a grid of `fresh_score`
values, it asks: "with current Config thresholds, does at least one
exit gate fire when the thesis is broken?" — for each combination of
`has_confirming_pattern` and `in_loss`.

For every cell where the thesis is broken (sign flip OR sign-held but
collapsed below SIGNAL_DECAY_FRACTION) the script prints which gate(s)
would fire, and FAILS THE BUILD if no gate fires.

WHEN TO RUN
-----------
* As part of `copilot/review-cycle.md` smoke after any change to:
  - portfolio/manager_v2.py exit gates
  - config.py exit-related thresholds
  - services/candle_patterns.py reversal-pattern sets
* Before closing an exit-pipeline stability window.

KEEP IN SYNC
------------
The `would_reversal_fire` and `would_decay_fire` predicates below
must mirror the guards inside `_signal_reversal_exit` and
`_signal_decay_exit`. If you change one, change the other in the
same commit. The script does not import the methods directly to
avoid pulling in the full manager dependency tree (Zerodha client,
analysis queue, etc.) for a pure-logic check.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import List

# Ensure we can import the project Config when run from repo root.
sys.path.insert(0, ".")

from config import Config  # noqa: E402


# ---------------------------------------------------------------------
# Gate predicates — MUST mirror portfolio/manager_v2.py guards.
# ---------------------------------------------------------------------

def would_reversal_fire(
    entry_score: float,
    fresh_score: float,
    has_confirming_pattern: bool,
    in_loss: bool,
    cfg=Config,
) -> bool:
    """Mirror of `_signal_reversal_exit` core gate (#174).

    Skips the live-price guard and the SELL mirror (the truth table
    enumerates BUY-side cases; SELL is symmetric).
    """
    if not getattr(cfg, "SIGNAL_REVERSAL_EXIT_ENABLED", False):
        return False
    # BUY position: fresh must be <= -SIGNAL_REVERSAL_SCORE.
    if fresh_score > -cfg.SIGNAL_REVERSAL_SCORE:
        return False
    if cfg.SIGNAL_REVERSAL_REQUIRE_PATTERN and not has_confirming_pattern:
        return False
    # 1R winner-skip — gate only acts on losers/flat. The truth table
    # explores both `in_loss` states so we can flag winner-skip cases.
    if not in_loss:
        return False
    return True


def would_decay_fire(
    entry_score: float,
    fresh_score: float,
    in_loss: bool,
    elapsed_min: int = 60,
    cfg=Config,
) -> bool:
    """Mirror of `_signal_decay_exit` core gate (#188)."""
    if not getattr(cfg, "SIGNAL_DECAY_EXIT_ENABLED", False):
        return False
    if abs(entry_score) < cfg.SIGNAL_DECAY_MIN_ENTRY_SCORE:
        return False
    if elapsed_min < cfg.SIGNAL_DECAY_MIN_HOLD_MINUTES:
        return False
    if not in_loss:
        # 1R winner-skip — same as reversal gate.
        return False
    sign_flipped = (entry_score > 0) != (fresh_score > 0)
    if sign_flipped:
        return True
    # Same-sign: magnitude must have collapsed.
    return abs(fresh_score) < abs(entry_score) * cfg.SIGNAL_DECAY_FRACTION


# ---------------------------------------------------------------------
# Truth table.
# ---------------------------------------------------------------------

@dataclass
class Cell:
    entry: float
    fresh: float
    has_pattern: bool
    in_loss: bool

    @property
    def thesis_broken(self) -> bool:
        """A thesis is 'broken' (and SHOULD be exited) when:
          * sign flipped — the signal now points the OTHER way, OR
          * sign held but magnitude collapsed below the configured
            SIGNAL_DECAY_FRACTION of the entry magnitude.
        Only meaningful for entries above the conviction floor.
        """
        if abs(self.entry) < Config.SIGNAL_DECAY_MIN_ENTRY_SCORE:
            return False  # low-conviction entry — not this gate's job
        sign_flipped = (self.entry > 0) != (self.fresh > 0)
        if sign_flipped:
            return True
        return abs(self.fresh) < abs(self.entry) * Config.SIGNAL_DECAY_FRACTION


def build_table() -> List[Cell]:
    """Representative grid focused on the dead-zone region.

    Entry side: BUY at +10 (typical high-conviction entry that the
    decay gate is parameterised for). SELL is symmetric and shares
    the same code path, so we don't need to enumerate both.
    """
    entries = [+10.0, +8.0]
    # Cover same-sign decay (+10 -> +5/+3/+1), zero, and the full
    # negative spectrum that includes the dead zone (-3/-5/-6/-7/-10).
    fresh_values = [+10, +7, +5, +4, +3, +1, 0, -1, -3, -5, -6, -7, -10]
    cells: List[Cell] = []
    for entry in entries:
        for fresh in fresh_values:
            for has_pattern in (True, False):
                for in_loss in (True, False):
                    cells.append(Cell(entry, fresh, has_pattern, in_loss))
    return cells


def main() -> int:
    cells = build_table()
    failures: List[str] = []
    rows: List[str] = []

    rows.append(
        f"{'entry':>6}  {'fresh':>6}  {'pat':>3}  {'loss':>4}  "
        f"{'broken':>6}  {'#174':>5}  {'#188':>5}  verdict"
    )
    rows.append("-" * 70)

    for c in cells:
        r174 = would_reversal_fire(c.entry, c.fresh, c.has_pattern, c.in_loss)
        r188 = would_decay_fire(c.entry, c.fresh, c.in_loss)
        broken = c.thesis_broken

        if broken and c.in_loss and not (r174 or r188):
            verdict = "DEAD ZONE"
            failures.append(
                f"entry {c.entry:+.0f} fresh {c.fresh:+.0f} "
                f"pattern={c.has_pattern} in_loss={c.in_loss} -> "
                "neither gate fires"
            )
        elif broken and c.in_loss:
            verdict = "covered"
        elif broken and not c.in_loss:
            # Winner-skip is intentional — both gates defer to the
            # trailing stop above 1R. Mark explicitly so the table
            # makes the decision visible rather than silent.
            verdict = "winner-skip (by design)"
        else:
            verdict = "thesis intact"

        rows.append(
            f"{c.entry:+6.0f}  {c.fresh:+6.0f}  "
            f"{'Y' if c.has_pattern else 'n':>3}  "
            f"{'Y' if c.in_loss else 'n':>4}  "
            f"{'Y' if broken else 'n':>6}  "
            f"{'fire' if r174 else '-':>5}  "
            f"{'fire' if r188 else '-':>5}  "
            f"{verdict}"
        )

    print("Exit-gate coverage matrix")
    print(f"  SIGNAL_REVERSAL_SCORE             = {Config.SIGNAL_REVERSAL_SCORE}")
    print(f"  SIGNAL_REVERSAL_REQUIRE_PATTERN   = {Config.SIGNAL_REVERSAL_REQUIRE_PATTERN}")
    print(f"  SIGNAL_DECAY_FRACTION             = {Config.SIGNAL_DECAY_FRACTION}")
    print(f"  SIGNAL_DECAY_MIN_ENTRY_SCORE      = {Config.SIGNAL_DECAY_MIN_ENTRY_SCORE}")
    print(f"  SIGNAL_DECAY_MIN_HOLD_MINUTES     = {Config.SIGNAL_DECAY_MIN_HOLD_MINUTES}")
    print()
    print("\n".join(rows))
    print()

    if failures:
        print(f"FAIL: {len(failures)} dead-zone cell(s) detected.")
        for f in failures:
            print(f"  - {f}")
        print()
        print(
            "Action: either tighten one of the gates so it covers the "
            "uncovered cells, or document explicitly why those cells "
            "are intentionally out-of-scope."
        )
        return 1

    print("PASS: every thesis-broken in-loss cell is covered by at "
          "least one exit gate.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
