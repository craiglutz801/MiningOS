"""
Run discovery in a subprocess. Writes JSON result to RESULT_PATH.
Never raises: on any failure writes error JSON and exits 0 so parent always gets a result.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback


def _write_error(path: str, message: str, log_line: str) -> None:
    try:
        with open(path, "w") as f:
            json.dump({
                "status": "error",
                "message": message,
                "log": [log_line],
                "areas_added": 0,
                "minerals_checked": [],
                "errors": [],
                "locations_from_ai": [],
                "urls_from_web_search": [],
            }, f)
    except Exception:
        pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replace", type=lambda x: x.lower() == "true", default=False)
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--result-path", default=os.environ.get("RESULT_PATH"))
    args = parser.parse_args()
    result_path = args.result_path or os.environ.get("RESULT_PATH")
    if not result_path:
        return 0

    try:
        from mining_os.services.discovery_agent import run_discovery
    except Exception as e:
        _write_error(result_path, str(e), f"Error: {e}")
        return 0

    try:
        log_lines: list = []
        result = run_discovery(
            replace=args.replace,
            limit_per_mineral=args.limit,
            log_lines=log_lines,
        )
        out = {
            "status": str(result.get("status", "ok")),
            "message": result.get("message"),
            "minerals_checked": list(result.get("minerals_checked") or []),
            "areas_added": int(result.get("areas_added", 0)),
            "replace": bool(result.get("replace", False)),
            "errors": list(result.get("errors") or [])[:20],
            "log": list(result.get("log") or []),
            "locations_from_ai": [
                {str(k): str(v) if v is not None else "" for k, v in loc.items()}
                for loc in (result.get("locations_from_ai") or [])
                if isinstance(loc, dict)
            ],
            "urls_from_web_search": list(result.get("urls_from_web_search") or []),
        }
        with open(result_path, "w") as f:
            json.dump(out, f, indent=0)
        return 0
    except BaseException as e:
        msg = str(e)
        _write_error(result_path, msg, f"Error: {msg}")
        try:
            traceback.print_exc()
        except Exception:
            pass
        return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BaseException:
        sys.exit(0)
