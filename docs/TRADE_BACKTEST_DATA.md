# Intraday Backtest Data Contract

This contract is part of the 2026-05-15 Chan Research Reset. Stage 1 needs replay data that works the same way on the Windows dev machine and on the Linux trading VM.

## Repo Roles

| Repo or path | Role |
|---|---|
| `ai-portfolio-manager` | Trading, replay, dashboards, reports, and sync scripts. No large historical datasets. |
| Existing private operational data repo | Mutable runtime data: trade ledgers, tax DBs, telemetry, reports, and logs synced by `scripts/shared/backup_data.py`. |
| `https://github.com/yash040599/ai-portfolio-backtest-data` | Private replay-data repo for normalized historical datasets. It is cloned locally beside the main repo at `../ai-portfolio-backtest-data` by default. |
| `../ai-portfolio-backtest-data/` | Local clone/cache read by replay on both the dev machine and Linux VM. `backtest_data/` remains a supported legacy override only. |
| `https://github.com/yash040599/market-research` | Standalone ATH-dip research sandbox. Use as reference/seed material, not as an intraday replay runtime dependency. |

## Sync Model

The new backtest-data repo is a normal Git repo cloned beside the main checkout at `../ai-portfolio-backtest-data`, matching the operational data repo layout. Data commits are made in the data repo only; the main repo stores code and contract docs.

Normal pulls:

```powershell
.\.venv\Scripts\python.exe scripts\shared\sync_backtest_data.py
```

Linux VM pulls with SSH:

```bash
python scripts/shared/sync_backtest_data.py --ssh
```

After a data-build script writes or updates files inside `../ai-portfolio-backtest-data/`, push from the dev machine:

```powershell
.\.venv\Scripts\python.exe scripts\shared\sync_backtest_data.py --status
.\.venv\Scripts\python.exe scripts\shared\sync_backtest_data.py --push --commit --message "add initial replay dataset"
```

This script does not merge SQLite rows. If two machines edit the same dataset, fix it in the data repo as a Git conflict. The expected flow is one writer for dataset builds and many readers for replay/trading machines.

## Git LFS (required)

The candle stores are large — `candles/intraday_15m.sqlite` is ~220 MB, well past GitHub's 100 MB per-file limit. The data repo therefore tracks every `*.sqlite` file through **Git LFS**. The `.gitattributes` in the data repo contains:

```text
*.sqlite filter=lfs diff=lfs merge=lfs -text
```

`git-lfs` must be installed on every machine that clones or pulls the repo, otherwise the working tree only gets small text pointer files instead of the real databases:

```bash
# one-time, per machine
git lfs install
# Windows:        winget install GitHub.GitLFS
# Debian/Ubuntu:  sudo apt-get install git-lfs
# macOS:          brew install git-lfs
```

`scripts/shared/sync_backtest_data.py` is LFS-aware:

- It fails early with install instructions if `git-lfs` is missing.
- It runs `git lfs install --local` + `git lfs pull` after clone, and `git lfs pull` after each `--pull`, so the binaries are always materialized.
- Its 100 MB guard exempts LFS-tracked files; it only blocks a large file that is *not* yet tracked by LFS, telling you to `git lfs track` the pattern first.

If you cloned the repo manually and see tiny `*.sqlite` files, run `git -C ../ai-portfolio-backtest-data lfs pull`. To add a new large file type, track it in the data repo before committing:

```bash
git -C ../ai-portfolio-backtest-data lfs track "*.parquet"
git -C ../ai-portfolio-backtest-data add .gitattributes
git -C ../ai-portfolio-backtest-data commit -m "track parquet via LFS"
```

## Seed Export

The first local seed dataset comes from the existing `data/candle_cache.db`. This is not enough to validate a strategy by itself because the 15-minute cache currently covers only the recent live window, but it gives Stage 1 a real local dataset with the same shape the Linux VM will consume.

Dry-run source summary:

```powershell
.\.venv\Scripts\python.exe scripts\trade\export_backtest_data.py --dry-run
```

Write the normalized dataset into `../ai-portfolio-backtest-data/`:

```powershell
.\.venv\Scripts\python.exe scripts\trade\export_backtest_data.py
.\.venv\Scripts\python.exe scripts\shared\sync_backtest_data.py --push --commit --message "seed replay data from candle cache"
```

The exporter writes:

- `candles/intraday_15m.sqlite`
- `candles/daily.sqlite`
- refreshed `symbols/*.csv`
- refreshed `manifest.json` with source hash, row counts, ranges, checksums, and the generating code commit

The replay harness now reads `../ai-portfolio-backtest-data/candles/intraday_15m.sqlite` by default when that repo is present, and falls back to `data/candle_cache.db` only when the Stage 1 data repo has not been cloned:

```powershell
.\.venv\Scripts\python.exe scripts\trade\backtest.py --from 2026-04-07 --to 2026-04-24 --symbol RELIANCE --score-mode scanner
```

Use `--score-mode scanner` to run replay-safe scanner-style candle scoring with an injected historical clock. Use `--score-mode simple` to compare against the old simplified replay score while parity is still being inspected. Run `scripts/trade/replay_clock_check.py` after scanner/replay clock changes.

## Environment

Set these in `.env`:

```text
BACKTEST_DATA_REPO_URL_HTTPS=https://github.com/yash040599/ai-portfolio-backtest-data.git
BACKTEST_DATA_REPO_URL_SSH=git@github.com:yash040599/ai-portfolio-backtest-data.git
BACKTEST_DATA_PATH=../ai-portfolio-backtest-data
```

Use HTTPS on the Windows dev machine if `gh auth login` is already configured. Use SSH on the Linux VM with a GitHub SSH key.

## First Dataset Layout

Use dependency-light formats first. This repo does not currently depend on pandas, pyarrow, polars, or duckdb, so the first contract is CSV metadata plus SQLite candle stores.

```text
../ai-portfolio-backtest-data/
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

The VM should never fetch individual candles from GitHub during trading or replay. It should pull the repo once before a run, then read local files from `../ai-portfolio-backtest-data/`. That keeps replay deterministic, avoids network/runtime surprises, and makes the dev machine and VM use the same dataset version. The VM must have `git-lfs` installed (see the Git LFS section) or the `*.sqlite` candle stores arrive as empty pointer files.

## Stage 1 Acceptance

T1.0 is complete when:

- `git-lfs` is installed on both machines and the `*.sqlite` candle stores materialize as real databases (not LFS pointer files).
- `../ai-portfolio-backtest-data/` is a clone of the private data repo on both machines.
- `manifest.json` exists and names the dataset version/source/date range.
- `scripts/trade/export_backtest_data.py` can seed the contract from the local candle cache without broker/network calls.
- replay code reads only local `../ai-portfolio-backtest-data/` files.
- the VM can run `python scripts/shared/sync_backtest_data.py --ssh` and land on the same manifest as the dev machine.