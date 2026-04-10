"""Simple structured logging for the target pipeline."""

from __future__ import annotations

import logging
import sys
from typing import Any, Optional

from target_pipeline.config import get_settings


def setup_logging(level: Optional[str] = None) -> None:
    lvl = (level or get_settings().log_level).upper()
    numeric = getattr(logging, lvl, logging.INFO)
    root = logging.getLogger()
    root.setLevel(numeric)
    if not root.handlers:
        h = logging.StreamHandler(sys.stderr)
        h.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s %(levelname)s [%(name)s] %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S",
            )
        )
        root.addHandler(h)


def log_counts(logger: logging.Logger, label: str, **kwargs: Any) -> None:
    parts = [f"{k}={v}" for k, v in sorted(kwargs.items())]
    logger.info("%s: %s", label, ", ".join(parts))
