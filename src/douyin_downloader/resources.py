from __future__ import annotations

import sys
from pathlib import Path

_STATIC_NAMES = frozenset({"index.html", "styles.css", "app.js", "app-icon.png"})


def _package_dir() -> Path:
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root is not None:
        return Path(bundle_root) / "douyin_downloader"
    return Path(__file__).resolve().parent


def static_resource_path(name: str) -> Path:
    if name not in _STATIC_NAMES:
        raise ValueError("unknown static resource")
    return _package_dir() / "web" / "static" / name


def static_directory() -> Path:
    return static_resource_path("index.html").parent


def app_icon_path() -> Path:
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root is not None:
        return Path(bundle_root) / "assets" / "app-icon.ico"
    return Path(__file__).resolve().parents[2] / "assets" / "app-icon.ico"
