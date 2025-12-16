from __future__ import annotations

import argparse
import logging
from pathlib import Path
import sys
from typing import Any, Dict

import uvicorn

from .utils.config import load_yaml, parse_config
from .utils.logging_utils import setup_logging


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="kg-api-server")
    p.add_argument("--config", type=str, default=str(Path(__file__).resolve().parent.parent / "config.yaml"))
    return p.parse_args()

def _maybe_prepend_local_deps(raw: Dict[str, Any], config_path: Path) -> None:
    deps = raw.get("deps") or {}
    if not isinstance(deps, dict):
        return
    local_itext2kg_path = deps.get("local_itext2kg_path")
    if not local_itext2kg_path:
        return

    candidate = Path(str(local_itext2kg_path))
    if not candidate.is_absolute():
        candidate = (config_path.parent / candidate).resolve()
    if not candidate.exists():
        return

    sys.path.insert(0, str(candidate))
    # Some environments may preload installed packages. If we want to force using local
    # sources, we must evict already-imported modules so subsequent imports re-resolve.
    for mod_name in list(sys.modules.keys()):
        if mod_name == "itext2kg" or mod_name.startswith("itext2kg."):
            del sys.modules[mod_name]
    try:
        import importlib

        importlib.invalidate_caches()
    except Exception:
        pass
    logging.getLogger(__name__).info("已启用本地依赖路径: %s", candidate)


def main() -> None:
    args = _parse_args()
    config_path = Path(args.config).resolve()
    raw = load_yaml(config_path)
    _maybe_prepend_local_deps(raw, config_path=config_path)

    # Import after potential sys.path modification, so local deps can take effect.
    from .api import create_app

    cfg = parse_config(raw)
    setup_logging(raw)

    app = create_app(cfg)
    uvicorn.run(app, host=cfg.server.host, port=cfg.server.port, log_level="info")
