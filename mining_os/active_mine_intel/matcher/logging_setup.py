"""Central logging configuration: rich console output plus a rotating log file."""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

_CONFIGURED = False


def setup_logging(log_dir: Path | None = None, level: str | None = None) -> logging.Logger:
    global _CONFIGURED
    logger = logging.getLogger("mcm")
    if _CONFIGURED:
        return logger

    level_name = (level or os.environ.get("MCM_LOG_LEVEL") or "INFO").upper()
    logger.setLevel(getattr(logging, level_name, logging.INFO))

    try:
        from rich.logging import RichHandler

        console = RichHandler(rich_tracebacks=False, show_path=False)
        console.setFormatter(logging.Formatter("%(message)s", datefmt="%H:%M:%S"))
    except ImportError:  # pragma: no cover
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(console)

    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_dir / "mine_claim_matcher.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        )
        logger.addHandler(file_handler)

    logger.propagate = False
    _CONFIGURED = True
    return logger


def get_logger(name: str = "mcm") -> logging.Logger:
    return logging.getLogger(name)
