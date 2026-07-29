from __future__ import annotations

import sys
from pathlib import Path

import pytest

from douyin_downloader.resources import app_icon_path, static_resource_path

STATIC_NAMES = ("index.html", "styles.css", "app.js", "app-icon.png")


def test_source_resources_resolve_to_existing_files() -> None:
    for name in STATIC_NAMES:
        path = static_resource_path(name)
        assert path.is_file()
        assert path.name == name

    icon = app_icon_path()
    assert icon.is_file()
    assert icon.name == "app-icon.ico"


def test_packaged_resources_resolve_below_meipass(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    bundle_root = tmp_path / "bundle"
    static_dir = bundle_root / "douyin_downloader" / "web" / "static"
    static_dir.mkdir(parents=True)
    for name in STATIC_NAMES:
        (static_dir / name).write_bytes(b"packaged")
    icon = bundle_root / "assets" / "app-icon.ico"
    icon.parent.mkdir()
    icon.write_bytes(b"icon")
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle_root), raising=False)

    for name in STATIC_NAMES:
        assert static_resource_path(name) == static_dir / name
    assert app_icon_path() == icon


def test_packaging_manifest_never_collects_f2_dependency_data() -> None:
    project_root = Path(__file__).parents[2]
    manifest = (project_root / "douyin_downloader.spec").read_text(encoding="utf-8")
    adapter = (
        project_root / "src" / "douyin_downloader" / "f2_adapter.py"
    ).read_text(encoding="utf-8")

    assert "collect_data_files" not in manifest
    assert "F2_DATA" not in manifest
    assert "importlib.resources" not in adapter
    assert "conf.yaml" not in adapter
