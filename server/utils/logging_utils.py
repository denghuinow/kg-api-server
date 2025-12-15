from __future__ import annotations

import logging
from typing import Any, Dict


def setup_logging(config: Dict[str, Any]) -> None:
    level_name = str((config.get("logging") or {}).get("level", "INFO")).upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(level=level, format="%(asctime)s - %(levelname)s - %(message)s")

