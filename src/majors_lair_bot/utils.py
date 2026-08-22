from __future__ import annotations

import html
import re
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")
STATUS_URL_RE = re.compile(
    r"^https?://(?:www\.)?(?:x\.com|twitter\.com)/[A-Za-z0-9_]+/status/(\d+)(?:[/?#].*)?$",
    re.IGNORECASE,
)
URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
MENTION_RE = re.compile(r"@[A-Za-z0-9_]+")
WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_'’-]*")


def utc_now() -> datetime:
    return datetime.now(UTC)


def isoformat(value: datetime | None = None) -> str:
    current = value or utc_now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    return current.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None or value == "":
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def normalize_handle(value: str) -> str:
    handle = value.strip().removeprefix("@").lower()
    if not HANDLE_RE.fullmatch(handle):
        raise ValueError("X handle must be 1–15 letters, numbers, or underscores")
    return handle


def parse_status_url(value: str) -> str:
    match = STATUS_URL_RE.fullmatch(value.strip())
    if not match:
        raise ValueError("Use a full X status URL such as https://x.com/user/status/123")
    return match.group(1)


def parse_period(value: str) -> tuple[timedelta, str]:
    raw = value.strip().lower()
    aliases = {"today": "24h", "day": "24h", "week": "7d", "month": "30d"}
    raw = aliases.get(raw, raw)
    match = re.fullmatch(r"(\d{1,3})([hd])", raw)
    if not match:
        raise ValueError("Period must look like 24h, 7d, or 30d")
    amount = int(match.group(1))
    duration = timedelta(hours=amount) if match.group(2) == "h" else timedelta(days=amount)
    if duration < timedelta(hours=1) or duration > timedelta(days=31):
        raise ValueError("Period must be between 1 hour and 31 days")
    return duration, raw


def parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, (int, float)):
        dt = datetime.fromtimestamp(float(value), tz=UTC)
    else:
        raw = str(value or "").strip()
        if not raw:
            return datetime.fromtimestamp(0, tz=UTC)
        if raw.isdigit():
            return datetime.fromtimestamp(int(raw), tz=UTC)
        formats = (
            "%a %b %d %H:%M:%S %z %Y",
            "%Y-%m-%dT%H:%M:%S.%fZ",
            "%Y-%m-%dT%H:%M:%SZ",
        )
        dt = None
        for fmt in formats:
            try:
                dt = datetime.strptime(raw, fmt)
                break
            except ValueError:
                pass
        if dt is None:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def normalize_text(text: str) -> str:
    cleaned = html.unescape(text or "").lower()
    cleaned = URL_RE.sub(" ", cleaned)
    cleaned = MENTION_RE.sub(" ", cleaned)
    cleaned = re.sub(r"[^\w\s'’-]", " ", cleaned, flags=re.UNICODE)
    return " ".join(cleaned.split())


def words(text: str) -> list[str]:
    return WORD_RE.findall(text)


def deep_get(mapping: Any, paths: Iterable[tuple[str, ...]], default: Any = "") -> Any:
    for path in paths:
        current = mapping
        for key in path:
            if not isinstance(current, dict) or key not in current:
                break
            current = current[key]
        else:
            if current is not None and current != "":
                return current
    return default
