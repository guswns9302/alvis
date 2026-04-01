# Alvis

`alvis` is a CLI-first multi-worker orchestrator backed by `LangGraph`, `Codex CLI`, and a local workspace-scoped SQLite state store.

## Requirements

- Python 3.12+
- `codex` CLI on `PATH`

## Install

Install Alvis into `~/.alvis`, create the global `alvis` wrapper, and start the background daemon:

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/guswns9302/alvis/main/install.sh)"
```

After install:

```bash
alvis version
alvis doctor
alvis start
```

## Quick Start

Use the Rich REPL:

```bash
alvis start
```

Inside the REPL:

- enter plain text to start a new request
- `/status` to inspect the latest run
- `/logs` to inspect recent important events
- `/clean` to remove workspace teams and exit
- `/quit` to leave the REPL
- `/shutdown` to remove the current team and exit

If Alvis asks a follow-up question during a run, answer directly in the same prompt. The run will resume automatically.

Non-interactive commands are also available:

```bash
alvis run <team_id> "Compare Python and Java"
alvis status <team_id>
alvis logs <team_id>
alvis clean
```

## Runtime Model

- `LangGraph` is the orchestration control plane
- worker tasks run through non-interactive `codex exec`
- worker outputs are schema-first structured results
- `SQLite` stores teams, runs, tasks, interactions, checkpoints, and events
- runtime files under the workspace data directory track heartbeat, process state, stdout, stderr, prompt, and structured output artifacts
- each team is fixed to `leader 1 + worker 2`
- the default worker aliases are `executor` and `reviewer`

## Installed Layout

- `~/.alvis/app`: installed application source
- `~/.alvis/venv`: execution virtualenv
- `~/.alvis/data/workspaces/<workspace-id>`: per-workspace DB, logs, and runtime files
- `~/.local/bin/alvis`: global wrapper entrypoint
- `launchd`: keeps the Alvis daemon available for daemon-backed commands

## Upgrade

```bash
alvis upgrade
alvis upgrade --version v0.2.1
```

The upgrade path reinstalls the package, restarts the daemon, and verifies daemon version alignment with the CLI version.

Recommended verification after upgrade:

```bash
alvis doctor
alvis start
```

## Recovery and Reset

Runtime state is workspace-scoped. If a workspace becomes inconsistent:

```bash
cp -R ~/.alvis ~/.alvis.backup
alvis doctor
alvis recover
alvis clean
```

If you need a full local reset, remove the workspace DB under `~/.alvis/data/workspaces/<workspace-id>/` after taking a backup.

Use `alvis recover` when a run looks stuck, a worker exited without a final answer, or a pending task was interrupted mid-run. Use `alvis clean` when you want to remove the current workspace team state entirely and start fresh.

## Manual Verification

```bash
alvis start
alvis status <team_id>
alvis logs <team_id>
alvis clean
```

## Core Principles

- `LangGraph` makes task routing, redo, reviewer handoff, interaction, and finalization decisions
- worker completion is automatic; there is no manual approval step in the default path
- if worker output is invalid or off-target, Alvis prefers automatic redo over finalizing a bad result
- the install location and active workspace are different concepts; Alvis is globally installed but operates on the current project directory
- all agents share the same workspace root, and file ownership is constrained by task `owned_paths`
