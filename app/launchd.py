from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from app.config import Settings, ensure_runtime_dirs
from app.install_paths import daemon_error_log_path, daemon_log_path, plist_path


class LaunchdManager:
    def __init__(self, settings: Settings):
        self.settings = settings

    @property
    def label(self) -> str:
        return self.settings.launchd_label

    def _daemon_path(self) -> str:
        path_entries = []
        codex_command = self.settings.codex_command
        codex_resolved = shutil.which(codex_command)
        if codex_resolved:
            path_entries.append(str(Path(codex_resolved).resolve().parent))
        path_entries.extend(
            [
                "/opt/homebrew/bin",
                "/usr/local/bin",
                "/usr/bin",
                "/bin",
                "/usr/sbin",
                "/sbin",
            ]
        )
        ordered = []
        for entry in path_entries:
            if entry not in ordered:
                ordered.append(entry)
        return ":".join(ordered)

    def plist_contents(self) -> str:
        ensure_runtime_dirs(self.settings)
        daemon_log_path(self.settings).parent.mkdir(parents=True, exist_ok=True)
        python_executable = Path(sys.executable)
        extra_env = [
            f"    <key>ALVIS_WORKER_BACKEND</key>\n    <string>{self.settings.worker_backend}</string>\n",
            f"    <key>ALVIS_WORKER_MODEL</key>\n    <string>{self.settings.worker_model}</string>\n",
            f"    <key>ALVIS_WORKER_REASONING_EFFORT</key>\n    <string>{self.settings.worker_reasoning_effort}</string>\n",
            f"    <key>ALVIS_WORKER_TIMEOUT_SECONDS</key>\n    <string>{self.settings.worker_timeout_seconds}</string>\n",
            f"    <key>ALVIS_WORKER_MAX_TOOL_ROUNDS</key>\n    <string>{self.settings.worker_max_tool_rounds}</string>\n",
        ]
        if self.settings.codex_api_key:
            extra_env.append(
                f"    <key>ALVIS_CODEX_API_KEY</key>\n    <string>{self.settings.codex_api_key}</string>\n"
            )
            extra_env.append(
                f"    <key>CODEX_API_KEY</key>\n    <string>{self.settings.codex_api_key}</string>\n"
            )
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{self.label}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{python_executable}</string>
    <string>-m</string>
    <string>app.daemon</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>ALVIS_HOME</key>
    <string>{self.settings.app_home}</string>
    <key>PATH</key>
    <string>{self._daemon_path()}</string>
    <key>ALVIS_DAEMON_HOST</key>
    <string>{self.settings.daemon_host}</string>
    <key>ALVIS_DAEMON_PORT</key>
    <string>{self.settings.daemon_port}</string>
    <key>ALVIS_CODEX_COMMAND</key>
    <string>{self.settings.codex_command}</string>
{''.join(extra_env)}  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>{daemon_log_path(self.settings)}</string>
  <key>StandardErrorPath</key>
  <string>{daemon_error_log_path(self.settings)}</string>
</dict>
</plist>
"""

    def ensure_plist(self) -> Path:
        path = plist_path(self.settings)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.plist_contents())
        return path

    def _run(self, *cmd: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(list(cmd), check=check, capture_output=True, text=True)

    def start(self) -> dict:
        path = self.ensure_plist()
        domain = f"gui/{os.getuid()}"
        self._run("launchctl", "bootout", domain, str(path), check=False)
        self._run("launchctl", "bootstrap", domain, str(path))
        self._run("launchctl", "kickstart", "-k", f"{domain}/{self.label}")
        return {"label": self.label, "plist": str(path), "status": "started"}

    def stop(self) -> dict:
        path = plist_path(self.settings)
        domain = f"gui/{os.getuid()}"
        self._run("launchctl", "bootout", domain, str(path), check=False)
        return {"label": self.label, "plist": str(path), "status": "stopped"}

    def restart(self) -> dict:
        self.stop()
        return self.start()

    def status(self) -> dict:
        domain = f"gui/{os.getuid()}"
        result = self._run("launchctl", "print", f"{domain}/{self.label}", check=False)
        running = result.returncode == 0
        return {
            "label": self.label,
            "plist": str(plist_path(self.settings)),
            "running": running,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
        }
