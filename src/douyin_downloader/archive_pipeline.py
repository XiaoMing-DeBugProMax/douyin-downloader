from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path

from douyin_downloader.archive_adapters import MediaAccess, RemoteArtifact
from douyin_downloader.archive_artifacts import (
    ArtifactKind,
    ArtifactRecord,
    artifact_digest,
    inspect_cover,
    validate_metadata,
    write_description,
    write_metadata,
)
from douyin_downloader.archive_paths import is_reparse_point
from douyin_downloader.archive_validation import archive_failed, inspect_mp4
from douyin_downloader.async_tools import run_in_thread_cancellation_safe
from douyin_downloader.audio_artifacts import AudioArtifactError, AudioArtifactTool
from douyin_downloader.domain import AppError, ResolvedWork
from douyin_downloader.settings import ArchiveProfile

_AUDIO_FAILURE_STATUSES = frozenset(
    {"probe_failed", "extract_failed", "validation_failed"}
)


@dataclass(frozen=True, slots=True)
class PreparedArchive:
    artifacts: tuple[ArtifactRecord, ...]
    promotions: tuple[tuple[Path, Path], ...]
    result: str = "success"

    def discard_parts(self) -> None:
        _discard_paths(part_path for part_path, _ in self.promotions)


class ArchiveArtifactPipeline:
    """Owns archive artifact creation, integrity inspection, and crash recovery."""

    def __init__(
        self,
        media_access: MediaAccess,
        audio_tool: AudioArtifactTool,
    ) -> None:
        self._media_access = media_access
        self._audio_tool = audio_tool

    async def prepare(
        self,
        output_directory: Path,
        aweme_id: str,
        operation_id: str,
        resolved: ResolvedWork,
        valid_artifacts: dict[str, ArtifactRecord],
        registered_artifacts: dict[str, ArtifactRecord],
        base_name: str,
        profile: ArchiveProfile,
    ) -> PreparedArchive:
        part_paths: list[Path] = []
        promotions: list[tuple[Path, Path]] = []
        try:
            video = valid_artifacts.get("video")
            if video is None:
                registered_video = registered_artifacts.get("video")
                video_relative = (
                    registered_video.relative_path
                    if registered_video is not None
                    else Path(f"{base_name}.mp4")
                )
                video_final = registered_path(output_directory, video_relative)
                video_part = output_directory / f"{aweme_id}.{operation_id}.mp4.part"
                part_paths.append(video_part)
                playback = resolved.preferred_playback_source()
                remote = await self._media_access.open_video(
                    playback.cdn_mirror_urls
                )
                if remote.content_type.split(";", 1)[0].lower() != "video/mp4":
                    raise archive_failed()
                await _write_remote(remote, video_part)
                await run_in_thread_cancellation_safe(
                    _validate_video_duration,
                    video_part,
                    resolved.snapshot.duration_ms,
                )
                video = await run_in_thread_cancellation_safe(
                    artifact_digest,
                    "video",
                    video_part,
                    video_relative,
                    "video/mp4",
                )
                video = _pending(video, video_part)
                promotions.append((video_part, video_final))
                video_input_path = video_part
            else:
                video_input_path = registered_path(
                    output_directory,
                    video.relative_path,
                )

            cover = valid_artifacts.get("cover")
            if cover is None:
                if not resolved.cover_urls:
                    raise archive_failed()
                remote = await self._media_access.open_cover(resolved.cover_urls)
                if not remote.content_type.lower().startswith("image/"):
                    raise archive_failed()
                cover_part = output_directory / f"{aweme_id}.{operation_id}.cover.part"
                part_paths.append(cover_part)
                await _write_remote(remote, cover_part)
                cover_mime, cover_suffix = await run_in_thread_cancellation_safe(
                    inspect_cover,
                    cover_part,
                )
                registered_cover = registered_artifacts.get("cover")
                cover_relative = (
                    registered_cover.relative_path
                    if registered_cover is not None
                    and registered_cover.relative_path.suffix.lower() == cover_suffix
                    else Path(f"{base_name}.cover{cover_suffix}")
                )
                cover_final = registered_path(output_directory, cover_relative)
                cover = await run_in_thread_cancellation_safe(
                    artifact_digest,
                    "cover",
                    cover_part,
                    cover_relative,
                    cover_mime,
                )
                cover = _pending(cover, cover_part)
                promotions.append((cover_part, cover_final))

            file_artifacts = [video, cover]
            if profile.include_audio:
                audio = valid_artifacts.get("audio")
                if audio is None:
                    registered_audio = registered_artifacts.get("audio")
                    audio_relative = (
                        registered_audio.relative_path
                        if registered_audio is not None
                        else Path(f"{base_name}.audio.m4a")
                    )
                    audio_final = registered_path(output_directory, audio_relative)
                    audio_part = output_directory / (
                        f"{aweme_id}.{operation_id}.audio.m4a.part"
                    )
                    part_paths.append(audio_part)
                    try:
                        outcome = await run_in_thread_cancellation_safe(
                            self._audio_tool.extract,
                            video_input_path,
                            audio_part,
                        )
                    except AudioArtifactError as error:
                        audio_part.unlink(missing_ok=True)
                        audio = ArtifactRecord(
                            kind="audio",
                            relative_path=audio_relative,
                            size_bytes=0,
                            mime_type="audio/mp4",
                            sha256="",
                            status=error.reason,
                        )
                    else:
                        if outcome == "no_audio":
                            audio = ArtifactRecord(
                                kind="audio",
                                relative_path=audio_relative,
                                size_bytes=0,
                                mime_type="audio/mp4",
                                sha256="",
                                status="no_audio",
                            )
                        else:
                            audio = await run_in_thread_cancellation_safe(
                                artifact_digest,
                                "audio",
                                audio_part,
                                audio_relative,
                                "audio/mp4",
                            )
                            audio = _pending(audio, audio_part)
                            promotions.append((audio_part, audio_final))
                file_artifacts.append(audio)
            if profile.include_description:
                description = valid_artifacts.get("description")
                if description is None:
                    registered_description = registered_artifacts.get("description")
                    description_relative = (
                        registered_description.relative_path
                        if registered_description is not None
                        else Path(f"{base_name}.description.txt")
                    )
                    description_final = registered_path(
                        output_directory,
                        description_relative,
                    )
                    description_part = output_directory / (
                        f"{aweme_id}.{operation_id}.description.txt.part"
                    )
                    part_paths.append(description_part)
                    description = await run_in_thread_cancellation_safe(
                        write_description,
                        description_part,
                        resolved.snapshot.description,
                        description_relative,
                    )
                    description = _pending(description, description_part)
                    promotions.append((description_part, description_final))
                file_artifacts.append(description)

            registered_metadata = registered_artifacts.get("metadata")
            metadata_relative = (
                registered_metadata.relative_path
                if registered_metadata is not None
                else Path(f"{base_name}.metadata.json")
            )
            metadata_final = registered_path(output_directory, metadata_relative)
            metadata_part = (
                output_directory / f"{aweme_id}.{operation_id}.metadata.json.part"
            )
            part_paths.append(metadata_part)
            metadata = await run_in_thread_cancellation_safe(
                write_metadata,
                metadata_part,
                resolved,
                operation_id,
                tuple(file_artifacts),
                metadata_relative,
            )
            metadata = _pending(metadata, metadata_part)
            promotions.append(
                (metadata_part, metadata_final)
            )
            return PreparedArchive(
                artifacts=(*file_artifacts, metadata),
                promotions=tuple(promotions),
                result=(
                    "partial_success"
                    if any(
                        artifact.status in _AUDIO_FAILURE_STATUSES
                        for artifact in file_artifacts
                    )
                    else "success"
                ),
            )
        except Exception:
            _discard_paths(part_paths)
            raise

    def audit(
        self,
        output_directory: Path,
        aweme_id: str,
        artifacts: tuple[ArtifactRecord, ...],
    ) -> dict[str, ArtifactRecord]:
        registrations = {artifact.kind: artifact for artifact in artifacts}
        valid: dict[str, ArtifactRecord] = {}
        for kind in ("video", "cover", "audio", "description"):
            registration = registrations.get(kind)
            if kind in {"audio", "description"} and registration is None:
                continue
            if kind == "audio" and registration is not None:
                video = valid.get("video")
                if video is None:
                    continue
                try:
                    video_path = registered_path(
                        output_directory,
                        video.relative_path,
                    )
                    if registration.status == "no_audio":
                        self._audio_tool.validate(video_path, None, "no_audio")
                        valid["audio"] = registration
                        continue
                    if registration.status in _AUDIO_FAILURE_STATUSES:
                        continue
                except (AudioArtifactError, AppError, OSError):
                    continue
            relative_path = (
                registration.relative_path
                if registration is not None
                else _legacy_artifact_path(output_directory, aweme_id, kind)
            )
            if relative_path is None:
                continue
            try:
                path = registered_path(output_directory, relative_path)
                if not path.is_file():
                    continue
                if kind == "audio":
                    video = valid.get("video")
                    if video is None:
                        continue
                    self._audio_tool.validate(
                        registered_path(output_directory, video.relative_path),
                        path,
                        "ready",
                    )
                valid[kind] = _inspect_local_artifact(
                    kind,
                    path,
                    relative_path,
                    aweme_id,
                    registration,
                )
            except (AudioArtifactError, AppError, OSError):
                continue

        metadata_registration = registrations.get("metadata")
        metadata_relative = (
            metadata_registration.relative_path
            if metadata_registration is not None
            else Path("metadata.json")
        )
        try:
            metadata_path = registered_path(output_directory, metadata_relative)
            if metadata_path.is_file():
                metadata_digest = _inspect_local_artifact(
                    "metadata",
                    metadata_path,
                    metadata_relative,
                    aweme_id,
                    metadata_registration,
                )
                document = validate_metadata(metadata_path, aweme_id)
                declared = {
                    artifact.kind: artifact for artifact in document.artifacts
                }
                file_kinds = {
                    kind
                    for kind in valid
                    if kind in {"video", "cover", "audio", "description"}
                    and valid[kind].status != "no_audio"
                }
                if set(declared) == file_kinds and all(
                    kind in valid
                    and kind in declared
                    and declared[kind].path == valid[kind].relative_path.as_posix()
                    and declared[kind].size_bytes == valid[kind].size_bytes
                    and declared[kind].mime_type == valid[kind].mime_type
                    and declared[kind].sha256 == valid[kind].sha256
                    for kind in file_kinds
                ):
                    valid["metadata"] = metadata_digest
        except (AppError, OSError):
            pass
        return valid

    def recover(
        self,
        output_directory: Path,
        aweme_id: str,
        artifacts: tuple[ArtifactRecord, ...],
    ) -> str:
        kinds = {artifact.kind for artifact in artifacts}
        if not {"video", "cover", "metadata"}.issubset(kinds) or not kinds.issubset(
            {"video", "cover", "audio", "description", "metadata"}
        ):
            raise archive_failed()
        part_paths: list[Path] = []
        try:
            for artifact in artifacts:
                if artifact.kind == "audio" and artifact.status in {
                    "no_audio",
                    *_AUDIO_FAILURE_STATUSES,
                }:
                    continue
                final_path = registered_path(
                    output_directory,
                    artifact.relative_path,
                )
                part_path = (
                    registered_path(output_directory, artifact.part_relative_path)
                    if artifact.part_relative_path is not None
                    else None
                )
                if part_path is not None:
                    part_paths.append(part_path)
                if not final_path.is_file():
                    if part_path is None or not part_path.is_file():
                        raise archive_failed()
                    _validate_registered_artifact(part_path, artifact, aweme_id)
                    part_path.replace(final_path)
                _validate_registered_artifact(final_path, artifact, aweme_id)

            registrations = {artifact.kind: artifact for artifact in artifacts}
            audio = registrations.get("audio")
            if audio is not None and audio.status not in _AUDIO_FAILURE_STATUSES:
                video = registrations["video"]
                video_path = registered_path(output_directory, video.relative_path)
                if audio.status == "no_audio":
                    self._audio_tool.validate(video_path, None, "no_audio")
                else:
                    self._audio_tool.validate(
                        video_path,
                        registered_path(output_directory, audio.relative_path),
                        "ready",
                    )
        except Exception:
            _discard_paths(part_paths)
            raise
        return (
            "partial_success"
            if any(
                artifact.kind == "audio"
                and artifact.status in _AUDIO_FAILURE_STATUSES
                for artifact in artifacts
            )
            else "success"
        )


async def _write_remote(remote: RemoteArtifact, part_path: Path) -> None:
    written = 0
    with part_path.open("wb") as output:
        async for chunk in remote.chunks:
            if chunk:
                written += len(chunk)
                output.write(chunk)
        output.flush()
    if remote.expected_size is not None and written != remote.expected_size:
        raise archive_failed()


def _pending(artifact: ArtifactRecord, part_path: Path) -> ArtifactRecord:
    return replace(
        artifact,
        part_relative_path=Path(part_path.name),
        status="promoting",
    )


def _discard_paths(paths: Iterable[Path]) -> None:
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            continue


def _validate_video_duration(path: Path, expected_duration: int) -> None:
    duration_ms = inspect_mp4(path)
    tolerance = max(2_000, int(expected_duration * 0.15))
    if expected_duration > 0 and abs(duration_ms - expected_duration) > tolerance:
        raise archive_failed()


def _legacy_artifact_path(
    output_directory: Path,
    aweme_id: str,
    kind: str,
) -> Path | None:
    if kind == "video":
        return Path(f"{aweme_id}.mp4")
    if kind == "cover":
        return next(
            (
                candidate
                for candidate in (
                    Path("cover.jpg"),
                    Path("cover.jpeg"),
                    Path("cover.png"),
                    Path("cover.webp"),
                )
                if (output_directory / candidate).is_file()
            ),
            None,
        )
    return None


def _inspect_local_artifact(
    kind: ArtifactKind,
    path: Path,
    relative_path: Path,
    aweme_id: str,
    registration: ArtifactRecord | None,
) -> ArtifactRecord:
    if kind == "video":
        inspect_mp4(path)
        mime_type = "video/mp4"
    elif kind == "cover":
        mime_type, _ = inspect_cover(path)
    elif kind == "metadata":
        validate_metadata(path, aweme_id)
        mime_type = "application/json"
    elif kind == "audio":
        mime_type = "audio/mp4"
    elif kind == "description":
        try:
            path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            raise archive_failed() from error
        mime_type = "text/plain; charset=utf-8"
    else:
        raise archive_failed()
    digest = artifact_digest(
        kind,
        path,
        relative_path,
        mime_type,
        allow_empty=kind == "description",
    )
    if registration is not None and (
        digest.size_bytes != registration.size_bytes
        or digest.mime_type != registration.mime_type
        or digest.sha256 != registration.sha256
    ):
        raise archive_failed()
    return digest


def registered_path(output_directory: Path, relative_path: Path) -> Path:
    if (
        relative_path.is_absolute()
        or ".." in relative_path.parts
        or relative_path.parent != Path(".")
    ):
        raise archive_failed()
    path = output_directory / relative_path
    if is_reparse_point(path):
        raise archive_failed()
    return path


def _validate_registered_artifact(
    path: Path,
    registration: ArtifactRecord,
    aweme_id: str,
) -> None:
    if registration.kind == "video":
        inspect_mp4(path)
    elif registration.kind == "cover":
        mime_type, _ = inspect_cover(path)
        if mime_type != registration.mime_type:
            raise archive_failed()
    elif registration.kind == "metadata":
        validate_metadata(path, aweme_id)
    elif registration.kind == "audio":
        if registration.status == "no_audio":
            return
    elif registration.kind == "description":
        try:
            path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            raise archive_failed() from error
    else:
        raise archive_failed()
    digest = artifact_digest(
        registration.kind,
        path,
        registration.relative_path,
        registration.mime_type,
        allow_empty=registration.kind == "description",
    )
    if (
        digest.size_bytes != registration.size_bytes
        or digest.sha256 != registration.sha256
    ):
        raise archive_failed()
