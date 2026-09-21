from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .errors import InvalidVideoURLError

_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def extract_video_id(value: str) -> str:
    """Extract an 11-character YouTube video ID from a URL or raw ID."""
    candidate = value.strip()
    if _VIDEO_ID_RE.fullmatch(candidate):
        return candidate

    parsed = urlparse(candidate)
    host = parsed.netloc.lower().split(":", 1)[0]
    host = host.removeprefix("www.").removeprefix("m.")

    video_id: str | None = None
    if host == "youtu.be":
        video_id = parsed.path.strip("/").split("/")[0]
    elif host in {"youtube.com", "music.youtube.com"}:
        parts = [part for part in parsed.path.split("/") if part]
        if parsed.path == "/watch":
            video_id = parse_qs(parsed.query).get("v", [None])[0]
        elif parts and parts[0] in {"shorts", "embed", "live"} and len(parts) >= 2:
            video_id = parts[1]

    if video_id:
        video_id = video_id.split("?")[0].split("&")[0]
    if video_id and _VIDEO_ID_RE.fullmatch(video_id):
        return video_id

    raise InvalidVideoURLError(f"Unsupported or invalid YouTube URL/video ID: {value}")


def load_video_inputs(urls: list[str] | None = None, input_file: Path | None = None) -> list[str]:
    """Load and de-duplicate video references while preserving order."""
    values: list[str] = []
    if urls:
        values.extend(item.strip() for item in urls if item.strip())
    if input_file:
        if not input_file.exists():
            raise FileNotFoundError(f"Input file not found: {input_file}")
        for line in input_file.read_text(encoding="utf-8-sig").splitlines():
            cleaned = line.strip()
            if cleaned and not cleaned.startswith("#"):
                values.append(cleaned.split(",", 1)[0].strip())

    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        video_id = extract_video_id(value)
        if video_id not in seen:
            seen.add(video_id)
            result.append(value)
    return result
