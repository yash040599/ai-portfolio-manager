# Intraday Backtest Data Contract

This contract is part of the 2026-05-15 Chan Research Reset. Stage 1 needs replay data that works the same way on the Windows dev machine and on the Linux trading VM.

## Repo Roles

| Repo or path | Role |
|---|---|
| `ai-portfolio-manager` | Trading, replay, dashboards, reports, and sync scripts. No large historical datasets. |
| Existing private operational data repo | Mutable runtime data: trade ledgers, tax DBs, telemetry, reports, and logs synced by `scripts/shared/backup_data.py`. |
| `https://github.com/yash040599/ai-portfolio-backtest-data` | Private replay-data repo for normalized historical datasets. It is cloned locally into `backtest_data/`. |
| `backtest_data/` | Gitignored local clone/cache read by replay on both the dev machine and Linux VM. |
| `https://github.com/yash040599/market-research` | Standalone ATH-dip research sandbox. Use as reference/seed material, not as an intraday replay runtime dependency. |

## Sync Model

The new backtest-data repo is a normal Git repo cloned inside the main checkout at `backtest_data/`. The main repo ignores that directory, so data commits are made in the data repo only.

Normal pulls:

```powershell
.\.venv\Scripts\python.exe scripts\shared\sync_backtest_data.py
```

Linux VM pulls with SSH:

```bash
python scripts/shared/sync_backtest_data.py --ssh
```

After a data-build script writes or updates files inside `backtest_data/`, push from the dev machine:

```powershell
.\.venv\Scripts\python.exe scripts\shared\sync_backtest_data.py --status
.\.venv\Scripts\python.exe scripts\shared\sync_backtest_data.py --push --commit --message "add initial replay dataset"
```

This script does not merge SQLite rows. If two machines edit the same dataset, fix it in the data repo as a Git conflict. The expected flow is one writer for dataset builds and many readers for replay/trading machines.

## Environment

Set these in `.env`:

```text
BACKTEST_DATA_REPO_URL_HTTPS=https://github.com/yash040599/ai-portfolio-backtest-data.git
BACKTEST_DATA_REPO_URL_SSH=git@github.com:yash040599/ai-portfolio-backtest-data.git
BACKTEST_DATA_PATH=backtest_data
```

Use HTTPS on the Windows dev machine if `gh auth login` is already configured. Use SSH on the Linux VM with a GitHub SSH key.

## First Dataset Layout

Use dependency-light formats first. This repo does not currently depend on pandas, pyarrow, polars, or duckdb, so the first contract is CSV metadata plus SQLite candle stores.

```text
backtest_data/
  README.md
  manifest.json
  symbols/
    instruments_nse.csv
    universe_nifty50.csv
    universe_nifty100.csv
  candles/
    intraday_15m.sqlite
    daily.sqlite
  corporate_actions/
    actions.csv
  derived/
    README.md
```

Required `manifest.json` fields:

| Field | Meaning |
|---|---|
| `dataset_id` | Stable name such as `nse_intraday_replay_v1`. |
| `dataset_version` | Date or semantic version for the data snapshot. |
| `created_at_ist` | Build timestamp. |
| `source` | Source system: Zerodha historical, NSE bhavcopy, yfinance seed, or manual import. |
| `universe` | Symbol universe covered by this snapshot. |
| `intervals` | Candle intervals present, starting with `15minute` and `day`. |
| `date_range` | Inclusive first and last trading dates. |
| `adjustment_policy` | Raw, split-adjusted, dividend-adjusted, or mixed. |
| `market_timezone` | `Asia/Kolkata`. |
| `generated_by_commit` | Commit hash of `ai-portfolio-manager` that built the dataset. |
| `checksums` | File-level sha256 map for reproducibility. |

Required candle columns:

| Column | Notes |
|---|---|
| `symbol` | NSE tradingsymbol used by the strategy code. |
| `instrument_token` | Zerodha token when available; blank is allowed for non-Zerodha sources. |
| `exchange` | Usually `NSE`. |
| `ts_ist` | Candle open timestamp in IST, ISO-8601 string. |
| `interval` | Example: `15minute` or `day`. |
| `open`, `high`, `low`, `close` | Numeric OHLC prices. |
| `volume` | Integer volume when available. |
| `source` | Source tag for auditability. |

SQLite tables should use a unique key on `(symbol, interval, ts_ist, source)` so dataset rebuilds can be checked without silent duplication.

## Market-Research Repo Decision

`market-research` is useful, but it is not the Stage 1 intraday data source. It is a standalone daily ATH-dip backtest using `yfinance`, a hardcoded current NIFTY 50 list, and result matrices for swing-style dip buying. It can seed daily history experiments and remind us how prior research was structured, but intraday replay needs its own normalized dataset with timestamps, intraday candles, costs, and reproducible manifests.

## Linux VM Rule

The VM should never fetch individual candles from GitHub during trading or replay. It should pull the repo once before a run, then read local files from `backtest_data/`. That keeps replay deterministic, avoids network/runtime surprises, and makes the dev machine and VM use the same dataset version.

## Stage 1 Acceptance

T1.0 is complete when:

- `backtest_data/` is a clone of the private data repo on both machines.
- `manifest.json` exists and names the dataset version/source/date range.
- replay code reads only local `backtest_data/` files.
- the VM can run `python scripts/shared/sync_backtest_data.py --ssh` and land on the same manifest as the dev machine.