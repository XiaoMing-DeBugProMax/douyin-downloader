from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path
from subprocess import CompletedProcess

import pytest

from scripts.provision_ffmpeg import (
    ProvisionError,
    audit_buildconf,
    load_manifest,
    provision_from_archive,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _file_identity(content: bytes) -> dict[str, int | str]:
    return {
        "size_bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def test_distribution_manifest_pins_an_immutable_lgpl_build() -> None:
    manifest = load_manifest(PROJECT_ROOT / "third_party" / "ffmpeg" / "manifest.json")

    assert manifest["release_tag"] == "autobuild-2026-08-01-13-21"
    assert manifest["archive_sha256"] == (
        "3bba81dcfd017a6ea1627905549769913948831ef10f3e7df7541f736067bff8"
    )
    assert "/autobuild-2026-08-01-13-21/" in manifest["archive_url"]
    assert "/latest/" not in manifest["archive_url"]
    assert manifest["ffmpeg_commit"] == (
        "9b6c8969e05b4f0b29f0f85cd501be6b3e582e6b"
    )


@pytest.mark.parametrize("flag", ["--enable-gpl", "--enable-nonfree"])
def test_build_configuration_rejects_forbidden_license_flags(flag: str) -> None:
    with pytest.raises(ProvisionError, match="forbidden FFmpeg build flag"):
        audit_buildconf(f"configuration: --disable-network {flag}")


def test_pinned_archive_is_safely_extracted_and_audited(tmp_path: Path) -> None:
    archive = tmp_path / "ffmpeg.zip"
    entries = {
        "ffmpeg-build/bin/ffmpeg.exe": b"ffmpeg-binary",
        "ffmpeg-build/bin/ffprobe.exe": b"ffprobe-binary",
        "ffmpeg-build/bin/ffplay.exe": b"unneeded-player",
        "ffmpeg-build/bin/avcodec.dll": b"shared-library",
        "ffmpeg-build/LICENSE.txt": b"GNU Lesser General Public License",
    }
    with zipfile.ZipFile(archive, "w") as bundle:
        for name, content in entries.items():
            bundle.writestr(name, content)
    manifest = {
        "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "version_marker": "n8.1.2-34-g9b6c8969e0",
        "release_tag": "fixture",
        "files": {
            "bin/avcodec.dll": _file_identity(b"shared-library"),
            "bin/ffmpeg.exe": _file_identity(b"ffmpeg-binary"),
            "bin/ffprobe.exe": _file_identity(b"ffprobe-binary"),
            "licenses/LICENSE.txt": _file_identity(
                b"GNU Lesser General Public License"
            ),
        },
    }
    calls: list[tuple[str, ...]] = []

    def identify(argv: tuple[str, ...]) -> CompletedProcess[str]:
        calls.append(argv)
        if argv[-1] == "-version":
            return CompletedProcess(
                argv,
                0,
                "ffmpeg version n8.1.2-34-g9b6c8969e0\n",
                "",
            )
        return CompletedProcess(
            argv,
            0,
            "configuration: --disable-network --disable-autodetect\n",
            "",
        )

    output = tmp_path / "ffmpeg"
    audit_path = provision_from_archive(archive, output, manifest, runner=identify)

    assert (output / "bin" / "ffmpeg.exe").read_bytes() == b"ffmpeg-binary"
    assert (output / "bin" / "ffprobe.exe").read_bytes() == b"ffprobe-binary"
    assert not (output / "bin" / "ffplay.exe").exists()
    assert (output / "bin" / "avcodec.dll").read_bytes() == b"shared-library"
    assert (output / "licenses" / "LICENSE.txt").is_file()
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    assert audit["release_tag"] == "fixture"
    assert audit["files"]["bin/ffmpeg.exe"]["sha256"] == hashlib.sha256(
        b"ffmpeg-binary"
    ).hexdigest()
    assert audit["files"]["bin/ffprobe.exe"]["sha256"] == hashlib.sha256(
        b"ffprobe-binary"
    ).hexdigest()
    assert "licenses/LICENSE.txt" in audit["files"]
    assert len(calls) == 4


def test_cached_distribution_rejects_files_outside_the_pinned_manifest(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "ffmpeg.zip"
    entries = {
        "ffmpeg-build/bin/ffmpeg.exe": b"ffmpeg-binary",
        "ffmpeg-build/bin/ffprobe.exe": b"ffprobe-binary",
        "ffmpeg-build/bin/avcodec.dll": b"shared-library",
        "ffmpeg-build/LICENSE.txt": b"GNU Lesser General Public License",
    }
    with zipfile.ZipFile(archive, "w") as bundle:
        for name, content in entries.items():
            bundle.writestr(name, content)
    manifest = {
        "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "version_marker": "n8.1.2-34-g9b6c8969e0",
        "release_tag": "fixture",
        "files": {
            "bin/avcodec.dll": _file_identity(b"shared-library"),
            "bin/ffmpeg.exe": _file_identity(b"ffmpeg-binary"),
            "bin/ffprobe.exe": _file_identity(b"ffprobe-binary"),
            "licenses/LICENSE.txt": _file_identity(
                b"GNU Lesser General Public License"
            ),
        },
    }

    def identify(argv: tuple[str, ...]) -> CompletedProcess[str]:
        output = (
            "ffmpeg version n8.1.2-34-g9b6c8969e0\n"
            if argv[-1] == "-version"
            else "configuration: --disable-network\n"
        )
        return CompletedProcess(argv, 0, output, "")

    output = tmp_path / "ffmpeg"
    audit_path = provision_from_archive(archive, output, manifest, runner=identify)
    unexpected = output / "bin" / "injected.dll"
    unexpected.write_bytes(b"not-from-the-pinned-archive")
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    audit["files"]["bin/injected.dll"] = _file_identity(unexpected.read_bytes())
    audit_path.write_text(json.dumps(audit), encoding="utf-8")

    with pytest.raises(ProvisionError, match="pinned manifest"):
        provision_from_archive(archive, output, manifest, runner=identify)
