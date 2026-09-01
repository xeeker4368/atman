#!/usr/bin/env python3
"""Start the Project Anam backend.

    python run_server.py [--debug] [--port N]

Host and port come from the configuration layers (defaults.toml -> local.toml
-> ANAM_API_HOST / ANAM_API_PORT). ``--port`` overrides for one run without
editing anything; start.sh sets the host through the environment.
"""

from __future__ import annotations

import argparse
import logging

import uvicorn

from program import config


def main() -> None:
    parser = argparse.ArgumentParser(description="Project Anam backend")
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Debug logging and auto-reload",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Override the configured port for this run",
    )
    args = parser.parse_args()

    log_dir = config.PROJECT_ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_dir / "anam.log"),
        ],
    )

    uvicorn.run(
        "program.api.app:app",
        host=config.api_host(),
        port=args.port if args.port is not None else config.api_port(),
        reload=args.debug,
        log_level="debug" if args.debug else "info",
    )


if __name__ == "__main__":
    main()
