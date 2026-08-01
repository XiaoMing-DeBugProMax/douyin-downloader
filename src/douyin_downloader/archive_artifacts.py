from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Literal

from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

from douyin_downloader.archive_validation import archive_failed
from douyin_downloader.domain import ResolvedWork

ArtifactKind = Literal["video", "cover", "audio", "description", "metadata"]
_IMAGE_FORMATS = {
    "JPEG": ("image/jpeg", ".jpg"),
    "PNG": ("image/png", ".png"),
    "WEBP": ("image/webp", ".webp"),
}


@dataclass(frozen=True, slots=True)
class ArtifactRecord:
    kind: ArtifactKind
    relative_path: Path
    size_bytes: int
    mime_type: str
    sha256: str
    part_relative_path: Path | None = None
    status: str = "archived"


class AuthorMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stable_id: str
    nickname: str


class MusicMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stable_id: str
    title: str
    author: str
    duration_seconds: int | None


class PublicMetricsMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    captured_at: datetime
    likes: int | None
    comments: int | None
    shares: int | None
    collects: int | None


class WorkMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    aweme_id: str
    content_type: str
    public_url: str
    description: str
    tags: tuple[str, ...]
    published_at: int | None
    duration_ms: int
    author: AuthorMetadata
    public_metrics: PublicMetricsMetadata
    music: MusicMetadata | None


class DiscoveryMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_type: Literal["single_work"]
    source_id: str
    operation_id: str


class ArtifactMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["video", "cover", "audio", "description"]
    path: str
    size_bytes: int = Field(ge=0)
    mime_type: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("path")
    @classmethod
    def path_must_be_relative(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or "\\" in value:
            raise ValueError("artifact paths must be relative")
        return value

    @field_validator("size_bytes")
    @classmethod
    def only_description_may_be_empty(
        cls,
        value: int,
        info: ValidationInfo,
    ) -> int:
        if value == 0 and info.data.get("kind") != "description":
            raise ValueError("only a description artifact may be empty")
        return value


class ArchiveMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    generated_at: datetime
    work: WorkMetadata
    discovery: DiscoveryMetadata
    artifacts: tuple[ArtifactMetadata, ...]


def inspect_cover(path: Path) -> tuple[str, str]:
    try:
        with Image.open(path) as image:
            image_format = image.format
            image.verify()
        with Image.open(path) as image:
            image.load()
    except (OSError, UnidentifiedImageError) as error:
        raise archive_failed() from error
    if image_format not in _IMAGE_FORMATS:
        raise archive_failed()
    return _IMAGE_FORMATS[image_format]


def artifact_digest(
    kind: ArtifactKind,
    path: Path,
    relative_path: Path,
    mime_type: str,
    *,
    allow_empty: bool = False,
) -> ArtifactRecord:
    digest = hashlib.sha256()
    size_bytes = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            size_bytes += len(chunk)
            digest.update(chunk)
    if size_bytes <= 0 and not allow_empty:
        raise archive_failed()
    return ArtifactRecord(
        kind=kind,
        relative_path=relative_path,
        size_bytes=size_bytes,
        mime_type=mime_type,
        sha256=digest.hexdigest(),
    )


def write_description(
    path: Path,
    description: str,
    relative_path: Path,
) -> ArtifactRecord:
    path.write_text(description, encoding="utf-8", newline="")
    return artifact_digest(
        "description",
        path,
        relative_path,
        "text/plain; charset=utf-8",
        allow_empty=True,
    )


def write_metadata(
    path: Path,
    resolved: ResolvedWork,
    operation_id: str,
    artifacts: tuple[ArtifactRecord, ...],
    relative_path: Path,
) -> ArtifactRecord:
    generated_at = datetime.now(UTC)
    snapshot = resolved.snapshot
    music = snapshot.music
    document = ArchiveMetadata(
        schema_version=1,
        generated_at=generated_at,
        work=WorkMetadata(
            aweme_id=snapshot.aweme_id,
            content_type=snapshot.content_type,
            public_url=snapshot.public_url,
            description=snapshot.description,
            tags=snapshot.tags,
            published_at=snapshot.published_at,
            duration_ms=snapshot.duration_ms,
            author=AuthorMetadata(
                stable_id=snapshot.author.stable_id,
                nickname=snapshot.author.nickname,
            ),
            public_metrics=PublicMetricsMetadata(
                captured_at=generated_at,
                likes=snapshot.public_metrics.likes,
                comments=snapshot.public_metrics.comments,
                shares=snapshot.public_metrics.shares,
                collects=snapshot.public_metrics.collects,
            ),
            music=(
                MusicMetadata(
                    stable_id=music.stable_id,
                    title=music.title,
                    author=music.author,
                    duration_seconds=music.duration_seconds,
                )
                if music is not None
                else None
            ),
        ),
        discovery=DiscoveryMetadata(
            source_type="single_work",
            source_id=snapshot.aweme_id,
            operation_id=operation_id,
        ),
        artifacts=tuple(
            ArtifactMetadata(
                kind=_metadata_artifact_kind(artifact.kind),
                path=artifact.relative_path.as_posix(),
                size_bytes=artifact.size_bytes,
                mime_type=artifact.mime_type,
                sha256=artifact.sha256,
            )
            for artifact in artifacts
            if artifact.kind in {"video", "cover", "audio", "description"}
            and artifact.status not in {
                "no_audio",
                "probe_failed",
                "extract_failed",
                "validation_failed",
            }
        ),
    )
    path.write_text(
        json.dumps(
            document.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    validate_metadata(path, snapshot.aweme_id)
    return artifact_digest(
        "metadata",
        path,
        relative_path,
        "application/json",
    )


def validate_metadata(path: Path, aweme_id: str) -> ArchiveMetadata:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        document = ArchiveMetadata.model_validate(raw)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise archive_failed() from error
    if document.work.aweme_id != aweme_id:
        raise archive_failed()
    kinds = {artifact.kind for artifact in document.artifacts}
    if not {"video", "cover"}.issubset(kinds):
        raise archive_failed()
    return document


def _metadata_artifact_kind(
    kind: ArtifactKind,
) -> Literal["video", "cover", "audio", "description"]:
    if (
        kind == "video"
        or kind == "cover"
        or kind == "audio"
        or kind == "description"
    ):
        return kind
    raise ValueError("metadata is not a file artifact")
