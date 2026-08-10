# ================================================================
# modes/mf/persistence.py
# ================================================================
# SQLite store for the mutual-fund book. Lives at data/mf.db.
# Pattern mirrors modes/swing/persistence.py.
#
# Two things are persisted:
#   mf_external_holdings — funds held at a broker other than Coin.
#       Coin holdings are never written here; they are broker truth
#       and are re-fetched every run. Only what the broker cannot
#       tell us gets stored.
#   mf_nav_cache — last known NAV per scheme, so the dashboard can
#       render a valued book before (or without) a Zerodha session.
# ================================================================

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from typing import Iterator

from config import now_ist
from modes.mf.types import MFHolding, SRC_COIN, SRC_EXTERNAL


DB_PATH = os.path.join("data", "mf.db")


# ── Connection helpers ───────────────────────────────────────────

def _ensure_dir() -> None:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)


@contextmanager
def _connect(path: str = DB_PATH) -> Iterator[sqlite3.Connection]:
    _ensure_dir()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# ── Schema ──────────────────────────────────────────────────────

def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS mf_external_holdings (
            holding_id   INTEGER PRIMARY KEY AUTOINCREMENT,
            scheme_code  TEXT NOT NULL,
            fund         TEXT NOT NULL,
            broker       TEXT NOT NULL DEFAULT 'Other',
            folio        TEXT NOT NULL DEFAULT '',
            units        REAL NOT NULL,
            avg_nav      REAL NOT NULL,
            notes        TEXT NOT NULL DEFAULT '',
            created_at   TEXT NOT NULL,
            updated_at   TEXT NOT NULL
        );

        -- One row per broker+folio per scheme: the same fund can
        -- legitimately be held at two platforms, and each leg keeps
        -- its own average NAV.
        CREATE UNIQUE INDEX IF NOT EXISTS idx_mf_ext_unique
            ON mf_external_holdings (scheme_code, broker, folio);

        CREATE TABLE IF NOT EXISTS mf_nav_cache (
            scheme_code  TEXT PRIMARY KEY,
            fund         TEXT NOT NULL DEFAULT '',
            amc          TEXT NOT NULL DEFAULT '',
            scheme_type  TEXT NOT NULL DEFAULT '',
            plan         TEXT NOT NULL DEFAULT '',
            nav          REAL NOT NULL DEFAULT 0,
            nav_date     TEXT NOT NULL DEFAULT '',
            updated_at   TEXT NOT NULL
        );

        -- Last Coin fetch, kept verbatim so the page opens on real
        -- numbers instead of a blank book. Replaced wholesale on every
        -- live sync: the broker is the only source of truth, so a row
        -- that has left Coin must leave here too.
        CREATE TABLE IF NOT EXISTS mf_coin_holdings (
            scheme_code  TEXT NOT NULL,
            fund         TEXT NOT NULL DEFAULT '',
            folio        TEXT NOT NULL DEFAULT '',
            units        REAL NOT NULL DEFAULT 0,
            avg_nav      REAL NOT NULL DEFAULT 0,
            nav          REAL NOT NULL DEFAULT 0,
            nav_date     TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (scheme_code, folio)
        );

        CREATE TABLE IF NOT EXISTS mf_coin_sips (
            sip_id                TEXT PRIMARY KEY,
            scheme_code           TEXT NOT NULL DEFAULT '',
            fund                  TEXT NOT NULL DEFAULT '',
            status                TEXT NOT NULL DEFAULT '',
            frequency             TEXT NOT NULL DEFAULT '',
            instalment_amount     REAL NOT NULL DEFAULT 0,
            instalment_day        INTEGER NOT NULL DEFAULT 0,
            completed_instalments INTEGER NOT NULL DEFAULT 0,
            pending_instalments   INTEGER NOT NULL DEFAULT 0,
            next_instalment       TEXT NOT NULL DEFAULT '',
            last_instalment       TEXT NOT NULL DEFAULT '',
            created               TEXT NOT NULL DEFAULT '',
            tag                   TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS mf_coin_orders (
            order_id         TEXT PRIMARY KEY,
            scheme_code      TEXT NOT NULL DEFAULT '',
            fund             TEXT NOT NULL DEFAULT '',
            folio            TEXT NOT NULL DEFAULT '',
            transaction_type TEXT NOT NULL DEFAULT '',
            status           TEXT NOT NULL DEFAULT '',
            amount           REAL NOT NULL DEFAULT 0,
            units            REAL NOT NULL DEFAULT 0,
            avg_nav          REAL NOT NULL DEFAULT 0,
            placed_at        TEXT NOT NULL DEFAULT '',
            purchase_type    TEXT NOT NULL DEFAULT '',
            status_message   TEXT NOT NULL DEFAULT '',
            tag              TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS mf_sync_state (
            id          INTEGER PRIMARY KEY CHECK (id = 1),
            synced_at   TEXT NOT NULL DEFAULT '',
            holdings    INTEGER NOT NULL DEFAULT 0
        );

        -- Daily NAV series per scheme, used for the risk/correlation
        -- analytics. Cached because the source is a public API and the
        -- analysis re-reads the full series on every render.
        CREATE TABLE IF NOT EXISTS mf_nav_history (
            scheme_code  TEXT NOT NULL,
            nav_date     TEXT NOT NULL,
            nav          REAL NOT NULL,
            PRIMARY KEY (scheme_code, nav_date)
        );

        CREATE TABLE IF NOT EXISTS mf_nav_history_meta (
            scheme_code  TEXT PRIMARY KEY,
            fetched_at   TEXT NOT NULL DEFAULT '',
            points       INTEGER NOT NULL DEFAULT 0,
            ok           INTEGER NOT NULL DEFAULT 0
        );
    """)

    # Added after the first release; existing books need the column.
    cols = {r["name"] for r in conn.execute(
        "PRAGMA table_info(mf_external_holdings)").fetchall()}
    if "sip_amount" not in cols:
        conn.execute("ALTER TABLE mf_external_holdings "
                     "ADD COLUMN sip_amount REAL NOT NULL DEFAULT 0")


def init_db(path: str = DB_PATH) -> None:
    with _connect(path) as conn:
        _ensure_schema(conn)


# ── External holdings CRUD ──────────────────────────────────────

def add_external_holding(
    *,
    scheme_code: str,
    fund: str,
    units: float,
    avg_nav: float,
    broker: str = "Other",
    folio: str = "",
    notes: str = "",
    sip_amount: float = 0.0,
    path: str = DB_PATH,
) -> int:
    """Insert (or merge into) an externally-held fund leg.

    Re-adding the same scheme+broker+folio merges the two lots into a
    unit-weighted average NAV rather than raising, which is what a
    top-up at the same broker actually is.
    """
    scheme_code = (scheme_code or "").strip().upper()
    fund = (fund or "").strip()
    broker = (broker or "Other").strip() or "Other"
    folio = (folio or "").strip()
    if not scheme_code or units <= 0 or avg_nav <= 0:
        raise ValueError("scheme_code, positive units and positive avg_nav are required")

    stamp = now_ist().isoformat(timespec="seconds")
    with _connect(path) as conn:
        _ensure_schema(conn)
        row = conn.execute(
            "SELECT holding_id, units, avg_nav FROM mf_external_holdings "
            "WHERE scheme_code = ? AND broker = ? AND folio = ?",
            (scheme_code, broker, folio),
        ).fetchone()

        if row:
            total_units = float(row["units"]) + units
            blended = ((float(row["units"]) * float(row["avg_nav"]) + units * avg_nav)
                       / total_units) if total_units > 0 else avg_nav
            conn.execute(
                "UPDATE mf_external_holdings "
                "SET units = ?, avg_nav = ?, fund = ?, notes = ?, "
                "    sip_amount = ?, updated_at = ? "
                "WHERE holding_id = ?",
                (round(total_units, 4), round(blended, 4), fund or row["fund"],
                 notes, max(0.0, float(sip_amount)), stamp, int(row["holding_id"])),
            )
            return int(row["holding_id"])

        cur = conn.execute(
            "INSERT INTO mf_external_holdings "
            "(scheme_code, fund, broker, folio, units, avg_nav, notes, "
            " sip_amount, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (scheme_code, fund, broker, folio, round(units, 4),
             round(avg_nav, 4), notes, max(0.0, float(sip_amount)), stamp, stamp),
        )
        return int(cur.lastrowid or 0)


def edit_external_holding(
    *,
    holding_id: int,
    units: float,
    avg_nav: float,
    broker: str | None = None,
    folio: str | None = None,
    notes: str | None = None,
    sip_amount: float | None = None,
    path: str = DB_PATH,
) -> bool:
    if units <= 0 or avg_nav <= 0:
        raise ValueError("units and avg_nav must be positive")

    stamp = now_ist().isoformat(timespec="seconds")
    with _connect(path) as conn:
        _ensure_schema(conn)
        row = conn.execute(
            "SELECT broker, folio, notes, sip_amount FROM mf_external_holdings "
            "WHERE holding_id = ?",
            (int(holding_id),),
        ).fetchone()
        if not row:
            return False
        cur = conn.execute(
            "UPDATE mf_external_holdings "
            "SET units = ?, avg_nav = ?, broker = ?, folio = ?, notes = ?, "
            "    sip_amount = ?, updated_at = ? "
            "WHERE holding_id = ?",
            (round(units, 4), round(avg_nav, 4),
             (broker.strip() if broker is not None else row["broker"]) or "Other",
             folio.strip() if folio is not None else row["folio"],
             notes if notes is not None else row["notes"],
             max(0.0, float(sip_amount)) if sip_amount is not None
             else float(row["sip_amount"] or 0),
             stamp, int(holding_id)),
        )
        return cur.rowcount > 0


def remove_external_holding(holding_id: int, path: str = DB_PATH) -> bool:
    with _connect(path) as conn:
        _ensure_schema(conn)
        cur = conn.execute(
            "DELETE FROM mf_external_holdings WHERE holding_id = ?",
            (int(holding_id),),
        )
        return cur.rowcount > 0


def external_holdings(path: str = DB_PATH) -> list[MFHolding]:
    with _connect(path) as conn:
        _ensure_schema(conn)
        rows = conn.execute(
            "SELECT * FROM mf_external_holdings ORDER BY fund, broker, folio"
        ).fetchall()
    return [
        MFHolding(
            scheme_code=str(r["scheme_code"]),
            fund=str(r["fund"]),
            units=float(r["units"]),
            avg_nav=float(r["avg_nav"]),
            folio=str(r["folio"]),
            source=SRC_EXTERNAL,
            broker=str(r["broker"]),
            holding_id=int(r["holding_id"]),
            notes=str(r["notes"]),
            sip_amount=float(r["sip_amount"] or 0),
        )
        for r in rows
    ]


# ── Coin snapshot ───────────────────────────────────────────────

def save_coin_snapshot(holdings: list[dict], sips: list[dict],
                       orders: list[dict], path: str = DB_PATH) -> None:
    """Replace the stored Coin fetch with a fresh one.

    Written in one transaction so a crash mid-sync can never leave a
    half-book on screen. SIPs and orders are only cleared when the
    fetch returned something, since those two calls can fail
    independently of holdings.
    """
    stamp = now_ist().isoformat(timespec="seconds")
    with _connect(path) as conn:
        _ensure_schema(conn)

        conn.execute("DELETE FROM mf_coin_holdings")
        conn.executemany(
            "INSERT OR REPLACE INTO mf_coin_holdings "
            "(scheme_code, fund, folio, units, avg_nav, nav, nav_date) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(str(h.get("scheme_code") or ""), str(h.get("fund") or ""),
              str(h.get("folio") or ""), float(h.get("units") or 0),
              float(h.get("avg_nav") or 0), float(h.get("nav") or 0),
              str(h.get("nav_date") or ""))
             for h in holdings if h.get("scheme_code")],
        )

        if sips:
            conn.execute("DELETE FROM mf_coin_sips")
            conn.executemany(
                "INSERT OR REPLACE INTO mf_coin_sips "
                "(sip_id, scheme_code, fund, status, frequency, "
                " instalment_amount, instalment_day, completed_instalments, "
                " pending_instalments, next_instalment, last_instalment, "
                " created, tag) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(str(s.get("sip_id") or ""), str(s.get("scheme_code") or ""),
                  str(s.get("fund") or ""), str(s.get("status") or ""),
                  str(s.get("frequency") or ""),
                  float(s.get("instalment_amount") or 0),
                  int(s.get("instalment_day") or 0),
                  int(s.get("completed_instalments") or 0),
                  int(s.get("pending_instalments") or 0),
                  str(s.get("next_instalment") or ""),
                  str(s.get("last_instalment") or ""),
                  str(s.get("created") or ""), str(s.get("tag") or ""))
                 for s in sips if s.get("sip_id")],
            )

        if orders:
            conn.execute("DELETE FROM mf_coin_orders")
            conn.executemany(
                "INSERT OR REPLACE INTO mf_coin_orders "
                "(order_id, scheme_code, fund, folio, transaction_type, "
                " status, amount, units, avg_nav, placed_at, purchase_type, "
                " status_message, tag) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(str(o.get("order_id") or ""), str(o.get("scheme_code") or ""),
                  str(o.get("fund") or ""), str(o.get("folio") or ""),
                  str(o.get("transaction_type") or ""), str(o.get("status") or ""),
                  float(o.get("amount") or 0), float(o.get("units") or 0),
                  float(o.get("avg_nav") or 0), str(o.get("placed_at") or ""),
                  str(o.get("purchase_type") or ""),
                  str(o.get("status_message") or ""), str(o.get("tag") or ""))
                 for o in orders if o.get("order_id")],
            )

        conn.execute(
            "INSERT INTO mf_sync_state (id, synced_at, holdings) VALUES (1, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET synced_at = excluded.synced_at, "
            "  holdings = excluded.holdings",
            (stamp, len(holdings)),
        )


def coin_holdings(path: str = DB_PATH) -> list[MFHolding]:
    """The last Coin fetch. Never contacts the broker."""
    with _connect(path) as conn:
        _ensure_schema(conn)
        rows = conn.execute(
            "SELECT * FROM mf_coin_holdings ORDER BY fund"
        ).fetchall()
    return [
        MFHolding(
            scheme_code=str(r["scheme_code"]),
            fund=str(r["fund"]),
            units=float(r["units"]),
            avg_nav=float(r["avg_nav"]),
            nav=float(r["nav"]),
            nav_date=str(r["nav_date"]),
            folio=str(r["folio"]),
            source=SRC_COIN,
            broker="Zerodha Coin",
        )
        for r in rows
    ]


def coin_sips(path: str = DB_PATH) -> list[dict]:
    with _connect(path) as conn:
        _ensure_schema(conn)
        rows = conn.execute("SELECT * FROM mf_coin_sips").fetchall()
    return [dict(r) for r in rows]


def coin_orders(path: str = DB_PATH) -> list[dict]:
    with _connect(path) as conn:
        _ensure_schema(conn)
        rows = conn.execute(
            "SELECT * FROM mf_coin_orders ORDER BY placed_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def last_synced_at(path: str = DB_PATH) -> str:
    with _connect(path) as conn:
        _ensure_schema(conn)
        row = conn.execute(
            "SELECT synced_at FROM mf_sync_state WHERE id = 1").fetchone()
    return str(row["synced_at"]) if row else ""


# ── NAV cache ───────────────────────────────────────────────────

def cache_navs(rows: list[dict], path: str = DB_PATH) -> int:
    """Upsert scheme NAVs. Rows come from the Coin catalogue or from a
    holdings fetch; both carry the same shape."""
    if not rows:
        return 0
    stamp = now_ist().isoformat(timespec="seconds")
    payload = []
    for r in rows:
        code = str(r.get("scheme_code") or "").strip().upper()
        nav = float(r.get("nav") or 0)
        if not code or nav <= 0:
            continue
        payload.append((
            code, str(r.get("fund") or r.get("name") or ""),
            str(r.get("amc") or ""), str(r.get("scheme_type") or ""),
            str(r.get("plan") or ""), nav, str(r.get("nav_date") or ""), stamp,
        ))
    if not payload:
        return 0

    with _connect(path) as conn:
        _ensure_schema(conn)
        conn.executemany(
            "INSERT INTO mf_nav_cache "
            "(scheme_code, fund, amc, scheme_type, plan, nav, nav_date, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(scheme_code) DO UPDATE SET "
            "  fund = excluded.fund, amc = excluded.amc, "
            "  scheme_type = excluded.scheme_type, plan = excluded.plan, "
            "  nav = excluded.nav, nav_date = excluded.nav_date, "
            "  updated_at = excluded.updated_at",
            payload,
        )
    return len(payload)


def cached_navs(scheme_codes: list[str] | None = None,
                path: str = DB_PATH) -> dict[str, dict]:
    """Last known NAV per scheme. Never contacts a broker."""
    with _connect(path) as conn:
        _ensure_schema(conn)
        if scheme_codes:
            codes = [c.strip().upper() for c in scheme_codes if c and c.strip()]
            if not codes:
                return {}
            out: dict[str, dict] = {}
            # Chunked so a large book stays under SQLite's variable cap.
            for i in range(0, len(codes), 400):
                chunk = codes[i:i + 400]
                marks = ",".join("?" for _ in chunk)
                rows = conn.execute(
                    f"SELECT * FROM mf_nav_cache WHERE scheme_code IN ({marks})",
                    chunk,
                ).fetchall()
                out.update({str(r["scheme_code"]): dict(r) for r in rows})
            return out
        rows = conn.execute("SELECT * FROM mf_nav_cache").fetchall()
        return {str(r["scheme_code"]): dict(r) for r in rows}


# ── NAV history ─────────────────────────────────────────────────

def save_nav_history(scheme_code: str, points: list[dict], *, ok: bool = True,
                     path: str = DB_PATH) -> int:
    """Upsert a daily NAV series for one scheme.

    `ok=False` records a failed lookup so the analytics do not retry a
    scheme with no published history on every single render.
    """
    code = (scheme_code or "").strip().upper()
    if not code:
        return 0
    stamp = now_ist().isoformat(timespec="seconds")
    rows = [(code, str(p.get("date") or ""), float(p.get("nav") or 0))
            for p in (points or [])
            if p.get("date") and float(p.get("nav") or 0) > 0]

    with _connect(path) as conn:
        _ensure_schema(conn)
        if rows:
            conn.executemany(
                "INSERT INTO mf_nav_history (scheme_code, nav_date, nav) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(scheme_code, nav_date) DO UPDATE SET "
                "  nav = excluded.nav",
                rows,
            )
        conn.execute(
            "INSERT INTO mf_nav_history_meta (scheme_code, fetched_at, points, ok) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(scheme_code) DO UPDATE SET "
            "  fetched_at = excluded.fetched_at, points = excluded.points, "
            "  ok = excluded.ok",
            (code, stamp, len(rows), 1 if (ok and rows) else 0),
        )
    return len(rows)


def nav_series(scheme_code: str, path: str = DB_PATH) -> list[tuple[str, float]]:
    """Stored NAV series, oldest first. Never contacts the network."""
    code = (scheme_code or "").strip().upper()
    if not code:
        return []
    with _connect(path) as conn:
        _ensure_schema(conn)
        rows = conn.execute(
            "SELECT nav_date, nav FROM mf_nav_history WHERE scheme_code = ? "
            "ORDER BY nav_date",
            (code,),
        ).fetchall()
    return [(str(r["nav_date"]), float(r["nav"])) for r in rows]


def nav_history_meta(path: str = DB_PATH) -> dict[str, dict]:
    with _connect(path) as conn:
        _ensure_schema(conn)
        rows = conn.execute("SELECT * FROM mf_nav_history_meta").fetchall()
    return {str(r["scheme_code"]): dict(r) for r in rows}


__all__ = [
    "DB_PATH", "init_db",
    "add_external_holding", "edit_external_holding", "remove_external_holding",
    "external_holdings", "cache_navs", "cached_navs",
    "save_coin_snapshot", "coin_holdings", "coin_sips", "coin_orders",
    "last_synced_at", "save_nav_history", "nav_series", "nav_history_meta",
]
