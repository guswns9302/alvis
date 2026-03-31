# Alvis

`alvis` is a CLI-first orchestrator for teams of AI agents backed by `LangGraph`, `tmux`, and `Codex CLI`.

## Requirements

- Python 3.12+
- `tmux`
- `codex` CLI on `PATH`

## Global Install

Install Alvis into `~/.alvis`, create a global `alvis` wrapper, and start the background daemon:

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/guswns9302/alvis/main/install.sh)"
```

After install:

```bash
alvis version
alvis doctor
alvis daemon status
```

## Quick Start

Global install is the default user path. The repo-local bootstrap flow still exists for development:

```bash
./scripts/bootstrap.sh
source .venv/bin/activate
alvis team create
# tmux attach 후 leader pane에서 요청 입력
# 예: Investigate flaky tests in billing
# worker 완료 후 LangGraph가 reviewer handoff, redo, leader 최종 출력 여부를 자동 판단
alvis recover --team-id demo --retry
alvis cleanup --team-id demo
alvis team remove demo
```

## Installed Runtime Model

- `~/.alvis/app`: installed application source
- `~/.alvis/venv`: execution virtualenv
- `~/.alvis/data/workspaces/<workspace-id>`: per-workspace DB/log/runtime state
- `~/.local/bin/alvis`: global wrapper entrypoint
- `launchd`: keeps the local Alvis daemon running in the background

## Upgrade

```bash
alvis upgrade
alvis upgrade --version v0.1.0
```

The upgrade path uses GitHub releases/tags and restarts the background daemon after reinstalling the package into `~/.alvis/venv`.

Leader pane supports:

- entering a new request directly
- `/refresh`
- `/status`
- `/quit`

## Supported Use

- Single user
- Single machine
- Single repository
- Experimental local operations

## Data And Reset Policy

- Runtime state lives under `~/.alvis/data/workspaces/<workspace-id>/`
- Main SQLite DB path is workspace-scoped under that directory
- Before upgrading local schema, back up the relevant workspace data directory
- Schema compatibility is not guaranteed yet; local DB reset is allowed for now
- There is no formal migration system yet; this project currently favors explicit backup and reset over in-place schema upgrades

## Reset Playbook

When local schema or runtime state becomes inconsistent:

```bash
cp -R ~/.alvis ~/.alvis.backup
alvis status demo
alvis recover --team-id demo
alvis cleanup --team-id demo
rm -f ~/.alvis/data/workspaces/<workspace-id>/alvis.db
```

After reset, recreate the team and start a fresh session:

```bash
alvis team create
```

Use reset only for local recovery. If there are active tasks or automatic handoffs in progress, inspect them with `status`, `recover`, and `cleanup` before deleting anything.

## Manual Verification

```bash
alvis team create
# tmux attach 후 leader pane에서 요청 입력
alvis status demo
alvis logs demo
alvis recover --team-id demo
alvis recover --team-id demo --retry
alvis cleanup --team-id demo
alvis team remove demo
```

## Core Principles

- `LangGraph` is the control plane.
- A background daemon is the default control-plane entrypoint for user CLI calls.
- `tmux` is execution UI, not source of truth.
- Leader input happens in the tmux leader console.
- Leader is not a manual approver in the default flow.
- `SQLite` stores teams, tasks, sessions, interactions, handoffs, and events.
- Each team is fixed to `leader 1 + worker 2`.
- Workers have fixed role aliases chosen at team creation time.
- The install location and the active workspace are different concepts; Alvis is globally installed but works against the current project directory.
- All agents share the same active project root, and file ownership is controlled by task `owned_paths`.
- Worker completion is routed automatically by `LangGraph`; there is no manual review approval step in the default flow.
- If a worker output is invalid or off-target, `LangGraph` prefers automatic redo over finalizing a bad result.
