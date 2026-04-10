#!/usr/bin/env python3
"""Fast Tesseract check (no pytesseract.get_tesseract_version — avoids agent timeouts).

Usage:
  .venv/bin/python scripts/verify_tesseract.py
  .venv/bin/python scripts/verify_tesseract.py /opt/homebrew/bin/tesseract
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else None
    if not cmd:
        for c in ("/opt/homebrew/bin/tesseract", "/usr/local/bin/tesseract"):
            if Path(c).is_file():
                cmd = c
                break
    if not cmd:
        cmd = "tesseract"
    try:
        p = subprocess.run(
            [cmd, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        print("TIMEOUT after 5s — binary may be wrong or blocked:", cmd, file=sys.stderr)
        return 2
    except FileNotFoundError:
        print("Not found:", cmd, file=sys.stderr)
        return 1
    out = (p.stdout or "") + (p.stderr or "")
    print(out.strip().splitlines()[0] if out.strip() else f"exit {p.returncode}")
    return 0 if p.returncode in (0, 1) else p.returncode


if __name__ == "__main__":
    raise SystemExit(main())
