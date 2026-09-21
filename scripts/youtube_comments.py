#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
SRC = SKILL_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from youtube_comment_research.cli import app  # noqa: E402

if __name__ == "__main__":
    app()
