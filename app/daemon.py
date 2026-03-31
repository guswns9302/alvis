from __future__ import annotations

import argparse

import uvicorn

from app.api.server import create_app
from app.config import get_settings
from app.logging import configure_logging


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    args = parser.parse_args()
    configure_logging()
    settings = get_settings()
    uvicorn.run(
        create_app(),
        host=args.host or settings.daemon_host,
        port=args.port or settings.daemon_port,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
