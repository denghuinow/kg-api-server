from __future__ import annotations

import logging
from typing import Any, Dict


def setup_logging(config: Dict[str, Any]) -> None:
    level_name = str((config.get("logging") or {}).get("level", "INFO")).upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(level=level, format="%(asctime)s - %(levelname)s - %(message)s")

    logging_cfg = config.get("logging") or {}
    silence_http = bool(logging_cfg.get("silence_http_requests", True))
    if silence_http:
        # Suppress noisy per-request logs from OpenAI-compatible clients (httpx/httpcore).
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        logging.getLogger("openai").setLevel(logging.WARNING)
