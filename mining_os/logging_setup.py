import logging
import sys
from pathlib import Path


def setup_logging(level: str = "INFO") -> None:
    """Configure logging to stdout and to logs/mining_os.log (relative to repo root)."""
    level_val = getattr(logging, level.upper(), logging.INFO)
    fmt = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    try:
        repo_root = Path(__file__).resolve().parents[1]
        log_dir = repo_root / "logs"
        log_dir.mkdir(exist_ok=True)
        log_file = log_dir / "mining_os.log"
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(logging.Formatter(fmt))
        handlers.append(file_handler)
    except Exception:
        pass
    logging.basicConfig(level=level_val, format=fmt, handlers=handlers, force=True)
