# Skill: Push All Repos

> **When to use:** User says "push all", "push everything", "sync all
> repos", "push to all three repos", or "push code/data/backtest".
> This skill pushes all three repositories that make up the
> ai-portfolio-manager system.

Only execute commits or pushes after an explicit user request. A request to
clean, audit, organise or locally sync repositories does not authorise publication.
The operational-data repository may contain personal records and must remain
private; the application and backtest repositories must contain no personal data.

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
| **Data** | `python scripts/shared/backup_data.py --snapshot --push` | Explicitly publishes a reviewed snapshot; preserves destination-only files and replaced versions. |
| **Backtest** | `python scripts/shared/sync_backtest_data.py --push --commit --message "<msg>"` | Syncs SQLite candle DBs. Uses HTTPS. Prints "No changes" if clean. |

## Full Push Sequence

Run from the code repository root:

```powershell
# 1. Code repo (only if there are uncommitted changes)
git add -A ; git commit -m "sync: <describe changes>" ; git push

# 2. Data repo (copies local data → data repo → push)
python scripts/shared/backup_data.py --snapshot --push

# 3. Backtest repo (syncs backtest SQLite DBs → push)
python scripts/shared/sync_backtest_data.py --push --commit --message "sync backtest data"
```

## Important Notes

- **Data repo uses SSH** (`git@github.com:...`). If SSH keys aren't configured, the push will fail.
- **Code repo's data/ and reports/ are .gitignored** — they only live in the data repo. That's why `git status` on the code repo won't show them.
- `backup_data.py` skips environment secrets, every access-token variant, private keys, raw tax statements and SQLite transient files.
- The `copilot/` folder is private and ignored in the main code repository.
- `--all-local` means local files overwrite remote (no conflict prompts).
- `--yes` skips interactive confirmation (required for non-interactive use).
- Without `--yes`, `backup_data.py` will prompt and block forever.

## Useful Flags

### backup_data.py
- `--dry-run` — preview what would change without writing
- `--local-only` — no network, commit or push
- `--push` — explicitly commit and publish reviewed private-data changes
- `--ssh` — use SSH transport (for Linux VMs)
- `--prefer local` — two-way sync, local wins conflicts (gentler than `--all-local`)

### sync_backtest_data.py
- `--push` — push after committing
- `--commit` — create a commit
- `--message "<msg>"` — commit message
