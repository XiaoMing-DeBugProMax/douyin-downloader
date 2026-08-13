from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from PyInstaller.archive.readers import CArchiveReader

from douyin_downloader.resources import (
    app_icon_path,
    ffmpeg_executable_path,
    ffprobe_executable_path,
    static_resource_path,
)

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
    assert ffmpeg_executable_path() == bundle_root / "ffmpeg" / "ffmpeg.exe"
    assert ffprobe_executable_path() == bundle_root / "ffmpeg" / "ffprobe.exe"


def test_source_media_tools_resolve_below_the_managed_dependency_cache() -> None:
    project_root = Path(__file__).parents[2]

    assert ffmpeg_executable_path() == (
        project_root / ".deps" / "ffmpeg" / "bin" / "ffmpeg.exe"
    )
    assert ffprobe_executable_path() == (
        project_root / ".deps" / "ffmpeg" / "bin" / "ffprobe.exe"
    )


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


def test_packaging_manifest_requires_audited_ffmpeg_and_compliance_material() -> None:
    project_root = Path(__file__).parents[2]
    spec = (project_root / "douyin_downloader.spec").read_text(encoding="utf-8")
    build = (project_root / "scripts" / "build.ps1").read_text(encoding="utf-8")

    assert "FFMPEG_BINARIES" in spec
    assert '"ffmpeg"' in spec
    assert "audit.json" in spec
    assert "third_party" in spec
    assert "manifest.json" in spec
    assert "NOTICE.md" in spec
    assert "provision_ffmpeg.py" in build
    assert "--output" in build


def test_system_tray_runtime_and_windows_backend_are_packaged() -> None:
    project_root = Path(__file__).parents[2]
    project = (project_root / "pyproject.toml").read_text(encoding="utf-8")
    manifest = (project_root / "douyin_downloader.spec").read_text(encoding="utf-8")

    assert '"pystray==0.19.5"' in project
    assert '"pystray._win32"' in manifest


def test_current_package_contains_required_resources_and_no_forbidden_config() -> None:
    artifact_value = os.environ.get("DOUYIN_BUILT_EXE")
    if artifact_value is None:
        pytest.skip("built executable was not explicitly selected")
    artifact = Path(artifact_value)
    assert artifact.is_file()
    archive = CArchiveReader(str(artifact))
    names = {name.replace("\\", "/").casefold() for name in archive.toc}

    for static_name in STATIC_NAMES:
        assert f"douyin_downloader/web/static/{static_name}" in names
    assert "assets/app-icon.ico" in names
    assert "assets/app-icon.svg" in names
    assert "ffmpeg/ffmpeg.exe" in names
    assert "ffmpeg/ffprobe.exe" in names
    assert "ffmpeg/ffplay.exe" not in names
    assert "third-party/ffmpeg/audit.json" in names
    assert "third-party/ffmpeg/manifest.json" in names
    assert "third-party/ffmpeg/notice.md" in names
    forbidden_names = {
        "test.yaml",
        "conf.yaml",
        "cookies.txt",
        "credentials.json",
        ".env",
    }
    assert not {
        name
        for name in names
        if name.rsplit("/", maxsplit=1)[-1] in forbidden_names
    }
    assert "third-party/ffmpeg/licenses/license.txt" in names
