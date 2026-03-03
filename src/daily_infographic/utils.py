from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None

    text = value.strip()
    if not text:
        return None

    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        pass

    try:
        dt = parsedate_to_datetime(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def in_window(dt: datetime, since: datetime, until: datetime) -> bool:
    return since <= dt <= until


def normalize_title(value: str) -> str:
    lowered = value.lower().strip()
    lowered = re.sub(r"https?://\S+", "", lowered)
    lowered = re.sub(r"[^\w\u4e00-\u9fff]+", "", lowered)
    return lowered


def stable_id(*parts: str) -> str:
    h = hashlib.sha1()
    for p in parts:
        h.update(p.encode("utf-8", errors="ignore"))
        h.update(b"|")
    return h.hexdigest()[:16]


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def truncate_text(text: str, max_len: int) -> str:
    raw = " ".join(text.split())
    if len(raw) <= max_len:
        return raw
    return raw[: max_len - 1].rstrip() + "…"


def to_local_date_label(dt: datetime, tz_name: str) -> str:
    local_dt = dt.astimezone(ZoneInfo(tz_name))
    return f"{local_dt.year}年{local_dt.month}月{local_dt.day}日"


def floor_sentence(text: str) -> str:
    clean = " ".join(text.split()).strip()
    if not clean:
        return clean

    # Prefer Chinese sentence punctuation; avoid cutting at the first English dot
    # in patterns like "The Information." that can lead to overly short summaries.
    delimiters = ["。", "！", "？", ";", "；"]
    indices = [clean.find(d) for d in delimiters if clean.find(d) > 0]
    if not indices:
        return clean
    end = min(indices)
    return clean[: end + 1]


def load_dotenv(path: str = ".env") -> None:
    if not os.path.exists(path):
        return

    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return

    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value
