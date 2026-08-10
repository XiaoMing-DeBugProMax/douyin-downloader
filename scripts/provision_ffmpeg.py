from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
import urllib.request
import uuid
import zipfile
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from subprocess import CompletedProcess
from typing import Any

CommandRunner = Callable[[tuple[str, ...]], CompletedProcess[str]]
_FORBIDDEN_FLAGS = ("--enable-gpl", "--enable-nonfree")


class ProvisionError(RuntimeError):
    pass


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ProvisionError("FFmpeg manifest is unreadable") from error
    if not isinstance(document, dict):
        raise ProvisionError("FFmpeg manifest must be an object")
    return document


def audit_buildconf(buildconf: str) -> None:
    lowered = buildconf.casefold()
    for flag in _FORBIDDEN_FLAGS:
        if flag in lowered:
            raise ProvisionError(f"forbidden FFmpeg build flag: {flag}")


def provision_from_archive(
    archive_path: Path,
    output_directory: Path,
    manifest: dict[str, Any],
    *,
    runner: CommandRunner | None = None,
) -> Path:
    command_runner = runner or _run_identity_command
    expected_archive_hash = _required_text(manifest, "archive_sha256")
    if _sha256(archive_path) != expected_archive_hash:
        raise ProvisionError("FFmpeg archive SHA-256 mismatch")
    if output_directory.exists():
        return _validate_existing(output_directory, manifest, command_runner)

    staging = output_directory.with_name(
        f".{output_directory.name}-{uuid.uuid4().hex}.staging"
    )
    staging.mkdir(parents=True)
    try:
        _extract_distribution(archive_path, staging)
        audit = _audit_distribution(staging, manifest, command_runner)
        _validate_pinned_files(audit, manifest)
        audit_path = staging / "audit.json"
        _write_audit(audit_path, audit)
        output_directory.parent.mkdir(parents=True, exist_ok=True)
        staging.replace(output_directory)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return output_directory / "audit.json"


def _extract_distribution(archive_path: Path, staging: Path) -> None:
    extracted: set[Path] = set()
    try:
        with zipfile.ZipFile(archive_path) as bundle:
            for info in bundle.infolist():
                if info.is_dir():
                    continue
                source = PurePosixPath(info.filename)
                if source.is_absolute() or ".." in source.parts:
                    raise ProvisionError("unsafe FFmpeg archive path")
                destination = _distribution_destination(source)
                if destination is None:
                    continue
                target = staging / destination
                if target in extracted:
                    raise ProvisionError("duplicate FFmpeg archive path")
                extracted.add(target)
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(info) as input_stream, target.open("wb") as output:
                    shutil.copyfileobj(input_stream, output)
    except (OSError, zipfile.BadZipFile) as error:
        raise ProvisionError("FFmpeg archive is unreadable") from error

    for required in (staging / "bin" / "ffmpeg.exe", staging / "bin" / "ffprobe.exe"):
        if not required.is_file():
            raise ProvisionError(f"required FFmpeg tool is missing: {required.name}")
    licenses = staging / "licenses"
    if not licenses.is_dir() or not any(licenses.iterdir()):
        raise ProvisionError("FFmpeg license material is missing")


def _distribution_destination(source: PurePosixPath) -> Path | None:
    parts = source.parts
    if "bin" in parts:
        index = parts.index("bin")
        relative = parts[index + 1 :]
        if len(relative) == 1:
            name = relative[0]
            lowered = name.casefold()
            if lowered in {"ffmpeg.exe", "ffprobe.exe"} or lowered.endswith(".dll"):
                return Path("bin", name)
    name = source.name
    if "license" in name.casefold() or name.casefold().startswith("copying"):
        return Path("licenses", name)
    return None


def _audit_distribution(
    directory: Path,
    manifest: dict[str, Any],
    runner: CommandRunner,
) -> dict[str, Any]:
    version_marker = _required_text(manifest, "version_marker")
    identities: dict[str, dict[str, str]] = {}
    for tool_name in ("ffmpeg.exe", "ffprobe.exe"):
        executable = directory / "bin" / tool_name
        version = _identity_output(runner, executable, "-version")
        buildconf = _identity_output(runner, executable, "-buildconf")
        if version_marker not in version:
            raise ProvisionError(f"unexpected {tool_name} version")
        audit_buildconf(buildconf)
        identities[tool_name] = {"version": version, "buildconf": buildconf}

    files = {
        path.relative_to(directory).as_posix(): {
            "size_bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
        for root in (directory / "bin", directory / "licenses")
        for path in sorted(root.iterdir())
        if path.is_file() and path.name != "audit.json"
    }
    return {
        "release_tag": _required_text(manifest, "release_tag"),
        "archive_sha256": _required_text(manifest, "archive_sha256"),
        "files": files,
        "tools": identities,
    }


def _identity_output(
    runner: CommandRunner,
    executable: Path,
    argument: str,
) -> str:
    try:
        completed = runner((str(executable), argument))
    except (OSError, subprocess.SubprocessError) as error:
        raise ProvisionError(f"could not inspect {executable.name}") from error
    if completed.returncode != 0 or not completed.stdout.strip():
        raise ProvisionError(f"could not inspect {executable.name}")
    return completed.stdout.strip()


def _validate_existing(
    directory: Path,
    manifest: dict[str, Any],
    runner: CommandRunner,
) -> Path:
    audit_path = directory / "audit.json"
    audit = _audit_distribution(directory, manifest, runner)
    _validate_pinned_files(audit, manifest)
    _write_audit(audit_path, audit)
    return audit_path


def _validate_pinned_files(
    audit: dict[str, Any],
    manifest: dict[str, Any],
) -> None:
    pinned = manifest.get("files")
    if not isinstance(pinned, dict) or not pinned:
        raise ProvisionError("FFmpeg pinned manifest file list is missing")
    if audit.get("files") != pinned:
        raise ProvisionError("FFmpeg files do not match the pinned manifest")


def _write_audit(path: Path, audit: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.part")
    temporary.write_text(
        json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _run_identity_command(argv: tuple[str, ...]) -> CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - audited local executable, fixed argument
        argv,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _required_text(document: dict[str, Any], name: str) -> str:
    value = document.get(name)
    if not isinstance(value, str) or not value:
        raise ProvisionError(f"FFmpeg manifest field is missing: {name}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download(url: str, destination: Path) -> None:
    if not url.startswith("https://github.com/BtbN/FFmpeg-Builds/releases/download/"):
        raise ProvisionError("FFmpeg archive URL is not approved")
    request = urllib.request.Request(  # noqa: S310 - approved HTTPS prefix above
        url,
        headers={"User-Agent": "douyin-local-build"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
            with destination.open("wb") as output:
                shutil.copyfileobj(response, output)
    except OSError as error:
        raise ProvisionError("FFmpeg archive download failed") from error


def main() -> int:
    parser = argparse.ArgumentParser(description="Provision audited FFmpeg tools")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    project_root = Path(__file__).resolve().parent.parent
    manifest = load_manifest(project_root / "third_party" / "ffmpeg" / "manifest.json")
    output_directory = args.output.resolve()
    if output_directory.exists():
        _validate_existing(output_directory, manifest, _run_identity_command)
        print("PASS ffmpeg_provision audited=1")
        return 0
    with tempfile.TemporaryDirectory(prefix="douyin-ffmpeg-") as temporary:
        archive_path = Path(temporary) / "ffmpeg.zip"
        _download(_required_text(manifest, "archive_url"), archive_path)
        provision_from_archive(archive_path, output_directory, manifest)
    print("PASS ffmpeg_provision audited=1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
