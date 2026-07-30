# ================================================================
# modes/swing/types.py
# ================================================================
# Typed records for swing-mode candidates, actions, positions, and
# run snapshots. Mirrors the analyse-mode Field pattern from
# modes/analyze/types.py so every value carries provenance.
#
# These dataclasses are the in-memory contract between the scanner,
# risk engine, persistence layer, report writer, and dashboard.
# ================================================================

from __future__ import annotations

import dataclasses
import datetime
import json
from dataclasses import dataclass, field
from typing import Any


# ── Setup types ─────────────────────────────────────────────────

SETUP_BREAKOUT            = "BREAKOUT"
SETUP_PULLBACK_UPTREND    = "PULLBACK_UPTREND"
SETUP_TREND_CONTINUATION  = "TREND_CONTINUATION"
SETUP_SUPPORT_REVERSAL    = "SUPPORT_REVERSAL"
SETUP_NEAR_52W_HIGH       = "NEAR_52W_HIGH"
# Dip-buy strategy (rolling 52-week-high reference). The legacy
# SETUP_ATH_DIP token is retained ONLY so existing rows in
# `data/swing.db` (entered before the 2026-05-14 ATH→52w switch)
# load without migration. New candidates are tagged SETUP_52W_DIP.
SETUP_52W_DIP             = "52W_DIP"
SETUP_ATH_DIP             = "ATH_DIP"   # legacy alias — DO NOT use for new candidates

# Persistence helpers consume this set when they need to "find any
# dip-buy candidate" (legacy or current).
DIP_SETUP_TYPES = {SETUP_52W_DIP, SETUP_ATH_DIP}

VALID_SETUPS = {
    SETUP_BREAKOUT,
    SETUP_PULLBACK_UPTREND,
    SETUP_TREND_CONTINUATION,
    SETUP_SUPPORT_REVERSAL,
    SETUP_NEAR_52W_HIGH,
    SETUP_52W_DIP,
    SETUP_ATH_DIP,
}


# ── Action types ────────────────────────────────────────────────

ACTION_ENTRY         = "ENTRY"
ACTION_TIGHTEN_STOP  = "TIGHTEN_STOP"
ACTION_PARTIAL_EXIT  = "PARTIAL_EXIT"
ACTION_FULL_EXIT     = "FULL_EXIT"
ACTION_WATCH         = "WATCH"
ACTION_HOLD          = "HOLD"

VALID_ACTIONS = {
    ACTION_ENTRY, ACTION_TIGHTEN_STOP, ACTION_PARTIAL_EXIT,
    ACTION_FULL_EXIT, ACTION_WATCH, ACTION_HOLD,
}


# ── Action status ───────────────────────────────────────────────

STATUS_PENDING        = "PENDING"
STATUS_CONFIRMED      = "CONFIRMED"
STATUS_SKIPPED        = "SKIPPED"
STATUS_EXPIRED        = "EXPIRED"
STATUS_MANUAL_REVIEW  = "MANUAL_REVIEW"

VALID_ACTION_STATUSES = {
    STATUS_PENDING, STATUS_CONFIRMED, STATUS_SKIPPED,
    STATUS_EXPIRED, STATUS_MANUAL_REVIEW,
}


# ── Position status ─────────────────────────────────────────────

POS_OPEN           = "OPEN"
POS_CLOSED         = "CLOSED"
POS_MANUAL_REVIEW  = "MANUAL_REVIEW"


# ── Candidate record ───────────────────────────────────────────

@dataclass
class SwingCandidate:
    """One scanned candidate from a swing run.

    Every candidate is persisted (accepted or rejected) so we can
    audit hit-rates and missed opportunities later.
    """
    symbol: str
    exchange: str = "NSE"
    setup_type: str = ""
    score: float = 0.0
    priority_rank: int = 0
    priority_score: float = 0.0
    close_price: float = 0.0
    entry_price: float = 0.0
    stop_price: float = 0.0
    target_price: float = 0.0
    risk_rupees: float = 0.0
    reward_rupees: float = 0.0
    rr_ratio: float = 0.0
    suggested_qty: int = 0
    status: str = "SCORED"          # SCORED | ACCEPTED | REJECTED | PLANNED
    rejected_reason: str = ""
    sector: str = ""

    # Indicator snapshot (for persistence + audit)
    sma_50: float = 0.0
    sma_200: float = 0.0
    ema_20: float = 0.0
    rsi_daily: float = 0.0
    atr_14: float = 0.0
    relative_strength: float = 0.0
    volume_ratio: float = 0.0       # today_vol / 20d_avg_vol
    high_20d: float = 0.0
    high_50d: float = 0.0
    low_52w: float = 0.0
    high_52w: float = 0.0
    weekly_trend_up: bool = False

    # All-time / reference high (running max of close prices over
    # the full available history) and the current close's drawdown
    # from it. Despite the legacy "ath_*" field names (kept so old
    # `data/swing.db` rows load without migration), the value
    # populated by both scanners since 2026-05-14 is the
    # *rolling 52-week high* (`Config.SWING_DIP_LOOKBACK_DAYS`
    # trading bars, default 252). The displayed column on the
    # dashboard reads "% Below 52w High".
    ath_price: float = 0.0          # rolling 52w-high reference (legacy field name)
    dip_from_ath_pct: float = 0.0   # 0 = at 52w high; +25 = 25% below 52w high

    # Signal reasons (human-readable)
    reasons: list[str] = field(default_factory=list)

    # ── Conviction + risk grading (2026-07-31) ──
    # `score` above is the raw setup score, which is not comparable
    # across setup families and says nothing about downside. These are
    # the two numbers a reader acts on. See modes/swing/conviction.py.
    conviction: float = 0.0           # 0-100
    conviction_grade: str = ""        # A | B | C | D
    risk_score: float = 0.0           # 0-100, higher = riskier
    risk_grade: str = ""              # LOW | MODERATE | HIGH | VERY HIGH
    conviction_json: str = ""         # component breakdown + notes
    quant_json: str = ""              # shared/quant_metrics.profile() snapshot

    # AI overlay (None when NoAI)
    ai_overlay_json: str = ""
    broker_instruction_json: str = ""

    # Internal
    _id: int = 0                    # DB id after persist
    _run_id: int = 0

    def snapshot_dict(self) -> dict:
        """Full snapshot for persistence as JSON."""
        return dataclasses.asdict(self)

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> SwingCandidate:
        known = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})


# ── Action record ──────────────────────────────────────────────

@dataclass
class SwingAction:
    """A recommended action from a swing run — entry, exit, stop move."""

    action_id: int = 0
    run_id: int = 0
    candidate_id: int = 0
    position_id: int = 0
    symbol: str = ""
    exchange: str = "NSE"
    action_type: str = ACTION_ENTRY
    status: str = STATUS_PENDING
    suggested_qty: int = 0
    suggested_price: float = 0.0
    suggested_stop: float = 0.0
    suggested_target: float = 0.0
    priority_rank: int = 0
    live_price: float = 0.0
    broker_instruction_json: str = ""
    created_at: str = ""
    expires_at: str = ""
    confirmed_at: str = ""
    executed_qty: int = 0
    executed_price: float = 0.0
    confirmed_stop: float = 0.0
    confirmation_source: str = ""   # DASHBOARD | CLI | RECONCILED
    notes: str = ""

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> SwingAction:
        known = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})


# ── Position record ────────────────────────────────────────────

@dataclass
class SwingPosition:
    """An open or closed swing-managed position."""

    position_id: int = 0
    symbol: str = ""
    exchange: str = "NSE"
    side: str = "BUY"
    managed_qty: int = 0
    entry_price: float = 0.0
    entry_date: str = ""
    stop_price: float = 0.0
    target_price: float = 0.0
    trailing_stop: float = 0.0
    status: str = POS_OPEN
    source: str = ""                # DASHBOARD_DONE | CLI_CONFIRM | ...
    linked_run_id: int = 0
    linked_action_id: int = 0
    exit_date: str = ""
    exit_price: float = 0.0
    exit_qty: int = 0
    gross_pnl: float = 0.0
    charges: float = 0.0
    net_pnl: float = 0.0
    charge_breakdown_json: str = ""
    closed_action_id: int = 0
    notes: str = ""

    # Live overlay (not persisted — filled at render time)
    live_price: float = 0.0
    unrealised_pnl: float = 0.0
    r_multiple: float = 0.0
    age_days: int = 0
    daily_action: str = ACTION_HOLD

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> SwingPosition:
        known = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})


# ── Run snapshot ───────────────────────────────────────────────

@dataclass
class SwingRunResult:
    """Output of one swing scan — everything needed for report + dashboard."""
    run_id: int = 0
    started_at: str = ""
    finished_at: str = ""
    mode: str = "NOAI"              # NOAI | AI
    universe: str = ""
    market_regime: str = ""
    run_for_date: str = ""
    trigger_source: str = ""
    candidates: list[SwingCandidate] = field(default_factory=list)
    actions: list[SwingAction] = field(default_factory=list)
    positions: list[SwingPosition] = field(default_factory=list)
    blocked_reason: str = ""
    notes: str = ""
