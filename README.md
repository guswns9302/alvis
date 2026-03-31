# Alvis

`alvis` is a CLI-first orchestrator for teams of AI agents backed by `LangGraph`, `tmux`, and `Codex CLI`.

## Requirements

- Python 3.12+
- `tmux`
- `git`
- `codex` CLI on `PATH`

## Quick Start

```bash
./scripts/bootstrap.sh
source .venv/bin/activate
alvis team create demo --workers 2
alvis team start demo
alvis run demo "Investigate flaky tests in billing"
alvis status demo
alvis review list
alvis recover --team-id demo --retry
alvis cleanup --team-id demo
```

## Supported Use

- Single user
- Single machine
- Single repository
- Experimental local operations

## Data And Reset Policy

- Runtime state lives under `.alvis/`
- Main SQLite DB path: `.alvis/alvis.db`
- Worktrees live under `.worktrees/`
- Before upgrading local schema, back up `.alvis/` and `.worktrees/`
- Schema compatibility is not guaranteed yet; local DB reset is allowed for now

## Manual Verification

```bash
alvis team create demo --workers 2
alvis team start demo
alvis run demo "Investigate flaky tests in billing"
alvis status demo
alvis review list
alvis review approve <review_id>
alvis review reject <review_id> --reason "Need a narrower fix"
alvis logs demo
alvis recover --team-id demo
alvis recover --team-id demo --retry
alvis cleanup --team-id demo
```

## Core Principles

- `LangGraph` is the control plane.
- `tmux` is execution UI, not source of truth.
- `SQLite` stores teams, tasks, sessions, reviews, and events.
- Each `Codex CLI` session is an independent actor with its own worktree.
