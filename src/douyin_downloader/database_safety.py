from __future__ import annotations

import sqlite3
from pathlib import Path

SENSITIVE_MARKERS = (
    b"authorization:",
    b"cookie:",
    b"set-cookie:",
    b"ttwid=",
    b"s_v_web_id=",
    b"launch_token",
    b"douyinvod.com",
    b"douyinpic.com",
    b"?signature=",
    b"&signature=",
    b"?x-bogus=",
    b"&x-bogus=",
    b"?x-gorgon=",
    b"&x-gorgon=",
)


def database_is_safe_and_valid(path: Path) -> bool:
    if not path.is_file() or file_contains_sensitive_marker(path):
        return False
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            result = connection.execute("PRAGMA integrity_check").fetchone()
            return bool(result == ("ok",))
        finally:
            connection.close()
    except (OSError, sqlite3.Error):
        return False


def file_contains_sensitive_marker(path: Path) -> bool:
    overlap_size = max(map(len, SENSITIVE_MARKERS)) - 1
    previous = b""
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                lowered = (previous + chunk).lower()
                if any(marker in lowered for marker in SENSITIVE_MARKERS):
                    return True
                previous = lowered[-overlap_size:]
    except OSError:
        return True
    return False
