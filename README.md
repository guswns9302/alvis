# Alvis

`alvis` is a CLI-first orchestrator for teams of AI agents backed by `LangGraph`, `tmux`, and `Codex CLI`.

## Requirements

- Python 3.12+
- `tmux`
- `git`
- `codex` CLI on `PATH`

## Quick Start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
alvis team create demo --workers 2
alvis team start demo
alvis run demo "Investigate flaky tests in billing"
alvis status demo
```

## Core Principles

- `LangGraph` is the control plane.
- `tmux` is execution UI, not source of truth.
- `SQLite` stores teams, tasks, sessions, reviews, and events.
- Each `Codex CLI` session is an independent actor with its own worktree.
