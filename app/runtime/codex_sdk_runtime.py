from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import Path

from app.config import Settings
from app.install_paths import (
    install_node_package_path,
    install_node_runtime_dir,
    install_node_worker_path,
)
NODE_PACKAGE_JSON = """{
  "name": "alvis-codex-sdk-runtime",
  "private": true,
  "type": "module",
  "dependencies": {
    "@openai/codex-sdk": "latest"
  }
}
"""

NODE_WORKER_SCRIPT = r"""#!/usr/bin/env node
import fs from "node:fs/promises";
import process from "node:process";
import { Codex } from "@openai/codex-sdk";

function parseArgs(argv) {
  const args = {};
  for (let index = 2; index < argv.length; index += 2) {
    const key = argv[index];
    const value = argv[index + 1];
    args[key.replace(/^--/, "")] = value;
  }
  return args;
}

async function main() {
  const args = parseArgs(process.argv);
  const promptText = await fs.readFile(args["prompt-file"], "utf8");
  const contract = JSON.parse(await fs.readFile(args["contract-file"], "utf8"));
  const outputSchema = JSON.parse(await fs.readFile(args["schema-file"], "utf8"));
  const apiKey = process.env.CODEX_API_KEY || process.env.ALVIS_CODEX_API_KEY;
  if (!apiKey) {
    throw new Error("CODEX_API_KEY is not configured.");
  }

  const codex = new Codex({
    env: {
      ...process.env,
      CODEX_API_KEY: apiKey,
    },
    config: {
      model: args["worker-model"],
    },
  });
  const thread = codex.startThread({
    workingDirectory: args.cwd,
    skipGitRepoCheck: true,
  });

  const turn = await thread.run(promptText, {
    outputSchema,
  });

  let finalResponse = turn.finalResponse;
  if (finalResponse == null && Array.isArray(turn.items)) {
    const message = [...turn.items].reverse().find((item) => item && item.type === "message");
    if (message && Array.isArray(message.content)) {
      finalResponse = message.content.map((entry) => entry.text || "").join("");
    }
  }
  if (finalResponse == null || finalResponse === "") {
    throw new Error("Codex SDK did not return a final response.");
  }

  const finalText = typeof finalResponse === "string" ? finalResponse : JSON.stringify(finalResponse);
  const parsed = typeof finalResponse === "string" ? JSON.parse(finalResponse) : finalResponse;
  const payload = {
    task_id: contract.task_id,
    agent_id: args["agent-id"],
    kind: "final",
    ...parsed,
  };
  await fs.writeFile(args["schema-output-file"], JSON.stringify(payload), "utf8");
  await fs.writeFile(args["last-message-file"], finalText, "utf8");
}

await main();
"""


def ensure_node_runtime_assets(settings: Settings) -> dict[str, Path]:
    runtime_dir = install_node_runtime_dir(settings)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    package_path = install_node_package_path(settings)
    worker_path = install_node_worker_path(settings)
    package_path.write_text(NODE_PACKAGE_JSON, encoding="utf-8")
    worker_path.write_text(NODE_WORKER_SCRIPT, encoding="utf-8")
    worker_path.chmod(0o755)
    return {
        "dir": runtime_dir,
        "package": package_path,
        "worker": worker_path,
    }


def install_codex_sdk_runtime(settings: Settings) -> dict:
    assets = ensure_node_runtime_assets(settings)
    node = subprocess.run(["node", "--version"], capture_output=True, text=True, check=False)
    if node.returncode != 0:
        return {
            "sdk_installed": False,
            "sdk_import_error": (node.stderr or node.stdout or "node is not installed").strip(),
        }
    npm = subprocess.run(["npm", "--version"], capture_output=True, text=True, check=False)
    if npm.returncode != 0:
        return {
            "sdk_installed": False,
            "sdk_import_error": (npm.stderr or npm.stdout or "npm is not installed").strip(),
        }
    result = subprocess.run(
        ["npm", "install", "--no-fund", "--no-audit", "--omit", "dev"],
        cwd=str(assets["dir"]),
        capture_output=True,
        text=True,
        check=False,
    )
    error = (result.stderr or result.stdout or "").strip() or None
    return {
        "sdk_installed": result.returncode == 0,
        "sdk_import_error": None if result.returncode == 0 else error,
    }


def verify_codex_sdk_runtime(settings: Settings) -> dict:
    assets = ensure_node_runtime_assets(settings)
    node = subprocess.run(["node", "--version"], capture_output=True, text=True, check=False)
    npm = subprocess.run(["npm", "--version"], capture_output=True, text=True, check=False)
    if node.returncode != 0:
        return {
            "node_available": False,
            "npm_available": npm.returncode == 0,
            "sdk_installed": False,
            "sdk_import_error": (node.stderr or node.stdout or "node is not installed").strip(),
        }
    if npm.returncode != 0:
        return {
            "node_available": True,
            "npm_available": False,
            "sdk_installed": False,
            "sdk_import_error": (npm.stderr or npm.stdout or "npm is not installed").strip(),
        }
    result = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            "import('@openai/codex-sdk').then(() => console.log('ok')).catch((error) => { console.error(String(error?.message || error)); process.exit(1); })",
        ],
        cwd=str(assets["dir"]),
        capture_output=True,
        text=True,
        check=False,
    )
    error = (result.stderr or result.stdout or "").strip() or None
    return {
        "node_available": True,
        "npm_available": True,
        "sdk_installed": result.returncode == 0,
        "sdk_import_error": None if result.returncode == 0 else error,
    }


def run_codex_sdk_worker(
    *,
    settings: Settings,
    prompt_file: Path,
    contract_file: Path,
    schema_file: Path,
    schema_output_file: Path,
    last_message_file: Path,
    agent_id: str,
    cwd: Path,
) -> subprocess.CompletedProcess[str]:
    assets = ensure_node_runtime_assets(settings)
    command = [
        "node",
        str(assets["worker"]),
        "--cwd",
        str(cwd),
        "--prompt-file",
        str(prompt_file),
        "--contract-file",
        str(contract_file),
        "--schema-file",
        str(schema_file),
        "--schema-output-file",
        str(schema_output_file),
        "--last-message-file",
        str(last_message_file),
        "--agent-id",
        agent_id,
        "--worker-model",
        settings.worker_model,
    ]
    env = dict(os.environ)
    if settings.codex_api_key:
        env["CODEX_API_KEY"] = settings.codex_api_key
        env["ALVIS_CODEX_API_KEY"] = settings.codex_api_key
    return subprocess.run(
        command,
        cwd=str(assets["dir"]),
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def normalize_command_backend(command_text: str) -> list[str]:
    command = shlex.split(command_text)
    if not command:
        return ["codex", "exec", "--color", "never"]
    executable = Path(command[0]).name
    if executable == "codex" and "exec" not in command[1:]:
        command = [*command, "exec", "--color", "never"]
    if executable != "codex" or "exec" not in command[1:]:
        return command
    invocation = list(command)
    if invocation and invocation[-1] == "-":
        invocation = invocation[:-1]
    if "--skip-git-repo-check" not in invocation:
        invocation.append("--skip-git-repo-check")
    return invocation
