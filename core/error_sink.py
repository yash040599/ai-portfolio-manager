"""
core/error_sink.py
==================

Tiny in-process error sink for **external-API failures** (Zerodha, Claude,
yfinance fallback). Two responsibilities:

1. Keep the last N (default 20) errors in a thread-safe ring buffer so
   the dashboard can render them as toast notifications top-right.
2. Detect auth-shaped Zerodha errors ("Incorrect `api_key` or
   `access_token`", 401, "TokenException") and **invalidate the
   saved Zerodha token file** so the auth pill correctly flips from
   "OK" to "Re-login" on the next render.

The pre-2026-05-14 behaviour was:
  * `data/access_token.json` carries today's date stamp -> auth pill
    says OK regardless of whether the token actually works
  * a 401 from Zerodha gets caught somewhere upstream, swallowed into
    an inline error message, and the user has no way to know they
    need to re-login until they notice the inline note in one corner
    of the page

Fix: the sink centralises the recording AND the side-effect (token
invalidation), so any future caller that wraps a Zerodha call with
`record_external_error("zerodha", exc)` gets both behaviours for free.

Storage is in-process only (no database, no file). Restarting the
dashboard server clears the buffer — that's intentional, the toast is
a "what just happened" surface, not an audit log. Permanent log lines
still go to `logs/portfolio.log` via the per-module `Logger`.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field, asdict


# --- ring buffer ------------------------------------------------------

_MAX_ERRORS = 20
_TOKEN_FILE = os.path.join("data", "access_token.json")
_TOKEN_BAK  = os.path.join("data", "access_token.json.invalid")

_lock = threading.Lock()
_errors: list["ExternalError"] = []
# monotonically increasing id so the dashboard JS can fetch only
# new errors since its last poll.
_next_id = 1


@dataclass
class ExternalError:
    """One recorded external-API failure, JSON-serialisable."""
    id: int
    source: str          # "zerodha" | "claude" | "yfinance" | ...
    kind: str            # "auth" | "rate_limit" | "network" | "other"
    message: str
    ts: float = field(default_factory=time.time)
    # When True the dashboard JS surfaces a "Re-login" CTA in the
    # toast and the auth pill on the next render is "Re-login".
    auth_invalid: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


# --- classification ---------------------------------------------------

# Substrings that mark a Zerodha auth failure. Matched case-insensitively.
# Kept conservative so a non-auth error doesn't accidentally invalidate
# the user's token.
_ZERODHA_AUTH_NEEDLES = (
    "incorrect `api_key` or `access_token`",
    "incorrect api_key or access_token",
    "tokenexception",
    "invalid `api_key` or `access_token`",
    "session expired",
    "401",
)

_RATE_LIMIT_NEEDLES = ("rate limit", "429", "too many requests")
_NETWORK_NEEDLES    = ("connection", "timeout", "timed out",
                       "network", "name resolution")


def classify(source: str, message: str) -> tuple[str, bool]:
    """Return (kind, auth_invalid). kind is the toast colour; auth_invalid
    triggers the token-file rename below."""
    msg_l = (message or "").lower()
    if source == "zerodha":
        for needle in _ZERODHA_AUTH_NEEDLES:
            if needle in msg_l:
                return "auth", True
    if any(n in msg_l for n in _RATE_LIMIT_NEEDLES):
        return "rate_limit", False
    if any(n in msg_l for n in _NETWORK_NEEDLES):
        return "network", False
    return "other", False


def _invalidate_token_file() -> None:
    """Move the saved Zerodha token aside so the auth pill flips and
    the next call to `ZerodhaClient.login()` triggers a fresh OAuth
    flow.

    Renaming (rather than deleting) keeps the bad token around for
    one debug cycle in case the user needs to compare it against a
    fresh one — `data/access_token.json.invalid`.
    """
    if not os.path.exists(_TOKEN_FILE):
        return
    try:
        # If a previous .invalid exists, overwrite it so we don't
        # accumulate forever.
        if os.path.exists(_TOKEN_BAK):
            os.remove(_TOKEN_BAK)
        os.rename(_TOKEN_FILE, _TOKEN_BAK)
    except OSError:
        # Best-effort. The auth pill check below also handles the
        # case where the file is still present but the token is
        # known-bad in the sink.
        pass


# --- public API -------------------------------------------------------

def record_external_error(source: str, exc_or_message,
                          *, log=None) -> ExternalError:
    """Append an external-API failure to the sink.

    `source` is the integration ("zerodha" / "claude" / "yfinance").
    Auth-shaped Zerodha errors automatically invalidate the saved
    token file as a side-effect. Returns the recorded entry so the
    caller can attach extra context if needed.
    """
    global _next_id
    msg = str(exc_or_message)
    kind, auth_invalid = classify(source, msg)

    with _lock:
        entry = ExternalError(
            id=_next_id,
            source=source,
            kind=kind,
            message=msg[:500],   # cap so a giant traceback doesn't bloat
            auth_invalid=auth_invalid,
        )
        _next_id += 1
        _errors.append(entry)
        if len(_errors) > _MAX_ERRORS:
            del _errors[: len(_errors) - _MAX_ERRORS]

    if auth_invalid:
        _invalidate_token_file()

    # Mirror to the standard log so it's still in `logs/portfolio.log`.
    if log is not None:
        try:
            log.warning(f"[{source}] {kind}: {msg[:200]}")
        except Exception:
            pass

    return entry


def get_errors_since(after_id: int = 0,
                     max_age_secs: float | None = None) -> list[dict]:
    """Return all errors with id > after_id, oldest first. Used by
    the dashboard JS poller to fetch only new errors since its last
    sighting (so reloading the page doesn't replay every prior toast).

    `max_age_secs` (added 2026-05-14): when set, also filters out
    entries older than this many seconds. Belt-and-braces against
    the case where the JS poller's localStorage was wiped (e.g.
    user cleared site data, switched browsers, opened in incognito)
    and a brand-new client would otherwise see ancient errors from
    a laptop-resume-after-sleep network blip from yesterday.
    """
    cutoff_ts = (time.time() - max_age_secs) if max_age_secs else None
    with _lock:
        return [e.to_dict() for e in _errors
                if e.id > after_id
                and (cutoff_ts is None or e.ts >= cutoff_ts)]


def current_max_id() -> int:
    """Highest id currently in the sink (0 when empty). Used by the
    JS poller's first-load init path: a brand-new browser fetches
    this once and stores it as `lastSeenId` so it never surfaces
    pre-existing errors as toasts. Origin: 2026-05-14 user reported
    laptop-sleep-resume errors re-spawning on every page navigation.
    """
    with _lock:
        return _errors[-1].id if _errors else 0


def has_auth_invalid() -> bool:
    """True when any recent error was an auth failure. Lets the
    server-side `_auth_pill()` flip to "Re-login" even when the
    file rename hasn't completed (e.g. permission denied)."""
    with _lock:
        return any(e.auth_invalid for e in _errors)


def clear() -> None:
    """Wipe the sink. Useful from `/login` after a successful
    re-auth so stale toasts don't reappear."""
    with _lock:
        _errors.clear()
        # NB: don't reset _next_id — the JS poller relies on monotonic
        # ids surviving across clears so a clear-then-error sequence
        # still produces an id higher than the JS's last-seen.
