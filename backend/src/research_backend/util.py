from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import PurePosixPath


class Conflict(ValueError):
    pass


class Missing(LookupError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def stamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Timestamp requires a timezone")
    return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def json_text(value, max_bytes=131072):
    result = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True)
    if len(result.encode()) > max_bytes:
        raise ValueError("Record too large; split records or store an artifact")
    return result


def digest(body):
    return hashlib.sha256(body).hexdigest()


def identifier(value, length=150):
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.:/-]*", value) or len(value) > length:
        raise ValueError("Invalid identifier")
    return value


def sha_value(value):
    if not re.fullmatch(r"[a-f0-9]{64}", value):
        raise ValueError("Invalid SHA-256")
    return value


def safe_relative(value):
    p = PurePosixPath(value)
    if (not value or len(value.encode()) > 900 or p.is_absolute() or "\\" in value
            or ":" in value or str(p) != value or any(part in {".", ".."} for part in p.parts)
            or any(part.lower() == ".git" or part.lower().startswith(".env") for part in p.parts)
            or any(ord(c) < 32 for c in value)):
        raise ValueError("Unsafe state file path")
    return p
