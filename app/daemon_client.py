from __future__ import annotations

import json
from pathlib import Path
from urllib import error, parse, request

from app.config import Settings


class DaemonUnavailableError(RuntimeError):
    pass


class DaemonHttpError(RuntimeError):
    def __init__(self, status_code: int, detail):
        self.status_code = status_code
        self.detail = detail
        if isinstance(detail, dict):
            message = detail.get("detail") or detail.get("error_code") or f"daemon http {status_code}"
        else:
            message = str(detail)
        super().__init__(message)


class DaemonClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.base_url = f"http://{settings.daemon_host}:{settings.daemon_port}"

    def _url(self, path: str, query: dict[str, str | None] | None = None) -> str:
        url = f"{self.base_url}{path}"
        if query:
            filtered = {key: value for key, value in query.items() if value is not None}
            if filtered:
                url += "?" + parse.urlencode(filtered)
        return url

    def request_json(
        self,
        method: str,
        path: str,
        *,
        payload: dict | None = None,
        query: dict[str, str | None] | None = None,
    ):
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = request.Request(self._url(path, query), data=data, method=method.upper(), headers=headers)
        try:
            with request.urlopen(req, timeout=5) as response:
                body = response.read().decode("utf-8")
                return json.loads(body) if body else None
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8")
            try:
                detail = json.loads(body) if body else {"detail": str(exc)}
            except json.JSONDecodeError:
                detail = body or str(exc)
            raise DaemonHttpError(exc.code, detail) from exc
        except error.URLError as exc:
            raise DaemonUnavailableError(str(exc)) from exc

    def health(self, workspace_root: Path | None = None) -> dict:
        return self.request_json("GET", "/health", query=self.with_workspace(workspace_root))

    def with_workspace(self, workspace_root: Path | None = None) -> dict[str, str]:
        return {"workspace_root": str((workspace_root or Path.cwd()).resolve())}
