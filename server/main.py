from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn

from .api import create_app
from .utils import AppConfig, load_yaml, parse_config, setup_logging


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="kg-api-server")
    p.add_argument("--config", type=str, default=str(Path(__file__).resolve().parent.parent / "config.yaml"))
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    config_path = Path(args.config).resolve()
    raw = load_yaml(config_path)
    cfg = parse_config(raw)
    setup_logging(raw)

    app = create_app(cfg)
    uvicorn.run(app, host=cfg.server.host, port=cfg.server.port, log_level="info")
