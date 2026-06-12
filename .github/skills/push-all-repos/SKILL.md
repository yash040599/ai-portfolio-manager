# Skill: Push All Repos

> **When to use:** User says "push all", "push everything", "sync all
> repos", "push to all three repos", or "push code/data/backtest".
> This skill pushes all three repositories that make up the
> ai-portfolio-manager system.

## Repository Layout

```
AiPortfolioManager/
├── ai-portfolio-manager/            # Code repo (git, HTTPS)
├── ai-portfolio-manager-data/       # Operational data backup (git, SSH)
└── ai-portfolio-backtest-data/      # Replay/backtest data (git, HTTPS)
```

## Quick Reference

| Repo | What to run | Notes |
|------|-------------|-------|
| **Code** | `git add -A ; git commit -m "<msg>" ; git push` | Standard git. Only if there are uncommitted code changes. |
| **Data** | `python scripts/shared/backup_data.py --all-local --yes` | Copies data/, reports/, logs/, copilot/ → data repo → commits → pushes. Uses SSH. |
| **Backtest** | `python scripts/shared/sync_backtest_data.py --push --commit --message "<msg>"` | Syncs SQLite candle DBs. Uses HTTPS. Prints "No changes" if clean. |

## Full Push Sequence

Run from the code repo root (`c:\Users\yashagrawal\AiPortfolioManager\ai-portfolio-manager`):

```powershell
# 1. Code repo (only if there are uncommitted changes)
git add -A ; git commit -m "sync: <describe changes>" ; git push

# 2. Data repo (copies local data → data repo → push)
python scripts/shared/backup_data.py --all-local --yes

# 3. Backtest repo (syncs backtest SQLite DBs → push)
python scripts/shared/sync_backtest_data.py --push --commit --message "sync backtest data"
```

## Important Notes

- **Data repo uses SSH** (`git@github.com:...`). If SSH keys aren't configured, the push will fail.
- **Code repo's data/ and reports/ are .gitignored** — they only live in the data repo. That's why `git status` on the code repo won't show them.
- `backup_data.py` skips `access_token.json`, `ZerodhaTaxPL/`, `__pycache__/`, and SQLite WAL/SHM files.
- The `copilot/` folder syncs to BOTH code and data repos.
- `--all-local` means local files overwrite remote (no conflict prompts).
- `--yes` skips interactive confirmation (required for non-interactive use).
- Without `--yes`, `backup_data.py` will prompt and block forever.

## Useful Flags

### backup_data.py
- `--dry-run` — preview what would change without writing
- `--ssh` — use SSH transport (for Linux VMs)
- `--prefer local` — two-way sync, local wins conflicts (gentler than `--all-local`)

### sync_backtest_data.py
- `--push` — push after committing
- `--commit` — create a commit
- `--message "<msg>"` — commit message
