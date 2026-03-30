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
alvis collect-outputs demo
alvis run demo "Investigate flaky tests in billing"
alvis status demo
```

## Core Principles

- `LangGraph` is the control plane.
- `tmux` is execution UI, not source of truth.
- `SQLite` stores teams, tasks, sessions, reviews, and events.
- Each `Codex CLI` session is an independent actor with its own worktree.
