import asyncio
import hashlib
import io
import json
import os
import sqlite3
import struct
import threading
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from PIL import Image

from douyin_downloader import archive_paths
from douyin_downloader.archive import (
    ManagedArchive,
    RemoteArtifact,
    SingleArchiveRequest,
)
from douyin_downloader.domain import (
    AppError,
    AuthorSnapshot,
    MusicSnapshot,
    PlaybackSource,
    PublicMetrics,
    ResolvedWork,
    WorkSnapshot,
)


class UnexpectedWorkAccess:
    async def fetch_work(self, _: str) -> object:
        raise AssertionError("invalid roots must fail before remote work access")


class UnexpectedMediaAccess:
    async def open_video(self, _: tuple[str, ...]) -> object:
        raise AssertionError("invalid roots must fail before media access")

    async def open_cover(self, _: tuple[str, ...]) -> object:
        raise AssertionError("invalid roots must fail before cover access")


class FailingWorkAccess:
    async def fetch_work(self, _: str) -> ResolvedWork:
        raise AppError("UPSTREAM_BLOCKED", "解析服务暂时不可用。", 502)


def mp4_box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I4s", len(payload) + 8, kind) + payload


def valid_mp4(duration_ms: int = 15_000) -> bytes:
    movie_header = (
        b"\x00\x00\x00\x00"
        + struct.pack(">IIII", 0, 0, 1_000, duration_ms)
        + bytes(80)
    )
    handler = b"\x00\x00\x00\x00" + bytes(4) + b"vide" + bytes(12)
    track = mp4_box(b"mdia", mp4_box(b"hdlr", handler))
    return (
        mp4_box(b"ftyp", b"isom\x00\x00\x02\x00isomiso2")
        + mp4_box(b"moov", mp4_box(b"mvhd", movie_header) + mp4_box(b"trak", track))
        + mp4_box(b"mdat", b"\x00\x00\x01\xb3" + bytes(32))
    )


class StaticWorkAccess:
    async def fetch_work(self, aweme_id: str) -> ResolvedWork:
        return ResolvedWork(
            snapshot=WorkSnapshot(
                aweme_id=aweme_id,
                content_type="video",
                public_url=f"https://www.douyin.com/video/{aweme_id}",
                description="归档测试作品",
                tags=("归档",),
                published_at=1_720_000_000,
                duration_ms=15_000,
                author=AuthorSnapshot(
                    stable_id="MS4wLjABAAAAstable",
                    nickname="测试作者",
                ),
                music=MusicSnapshot(
                    stable_id="music-123",
                    title="测试音乐",
                    author="音乐作者",
                    duration_seconds=15,
                ),
                public_metrics=PublicMetrics(12, 3, 4, 5),
            ),
            cover_urls=("https://p3.douyinpic.com/cover.png",),
            playback_sources=(
                PlaybackSource(
                    bitrate=900_000,
                    gear_name="normal_720",
                    quality_type=20,
                    codec="h264",
                    width=720,
                    height=1280,
                    size_bytes=1_000,
                    cdn_mirror_urls=(
                        "https://v95-web.douyinvod.com/720-a.mp4",
                    ),
                ),
                PlaybackSource(
                    bitrate=1_800_000,
                    gear_name="normal_1080",
                    quality_type=40,
                    codec="h265",
                    width=1080,
                    height=1920,
                    size_bytes=2_000,
                    cdn_mirror_urls=(
                        "https://v95-web.douyinvod.com/1080-a.mp4",
                        "https://v11-web.douyinvod.com/1080-b.mp4",
                    ),
                ),
            ),
        )


class BlockingWorkAccess(StaticWorkAccess):
    def __init__(self) -> None:
        self.calls = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def fetch_work(self, aweme_id: str) -> ResolvedWork:
        self.calls += 1
        self.started.set()
        await self.release.wait()
        return await super().fetch_work(aweme_id)


class StaticMediaAccess:
    def __init__(self, payload: bytes, cover_payload: bytes | None = None) -> None:
        self.payload = payload
        self.cover_payload = cover_payload or valid_png()
        self.requests: list[tuple[str, ...]] = []
        self.cover_requests: list[tuple[str, ...]] = []

    async def open_video(self, cdn_mirror_urls: tuple[str, ...]) -> RemoteArtifact:
        self.requests.append(cdn_mirror_urls)

        async def chunks() -> AsyncIterator[bytes]:
            midpoint = len(self.payload) // 2
            yield self.payload[:midpoint]
            yield self.payload[midpoint:]

        return RemoteArtifact(
            content_type="video/mp4",
            expected_size=len(self.payload),
            chunks=chunks(),
        )

    async def open_cover(self, cdn_mirror_urls: tuple[str, ...]) -> RemoteArtifact:
        self.cover_requests.append(cdn_mirror_urls)

        async def chunks() -> AsyncIterator[bytes]:
            yield self.cover_payload

        return RemoteArtifact(
            content_type="image/png",
            expected_size=len(self.cover_payload),
            chunks=chunks(),
        )


def valid_png() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (2, 2), (32, 96, 160)).save(output, format="PNG")
    return output.getvalue()


def truncated_jpeg() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (32, 32), (255, 0, 0)).save(
        output,
        format="JPEG",
        quality=90,
    )
    return output.getvalue()[:-1]


class DirectoryReplacementAttempt(StaticMediaAccess):
    def __init__(self, payload: bytes, work_directory: Path) -> None:
        super().__init__(payload)
        self.work_directory = work_directory
        self.replacement_was_blocked = False

    async def open_video(self, cdn_mirror_urls: tuple[str, ...]) -> RemoteArtifact:
        moved_directory = self.work_directory.with_name("moved-work")
        try:
            self.work_directory.rename(moved_directory)
        except OSError:
            self.replacement_was_blocked = True
        else:
            self.work_directory.mkdir()
        return await super().open_video(cdn_mirror_urls)


class CoverOnlyMediaAccess(StaticMediaAccess):
    async def open_video(self, _: tuple[str, ...]) -> RemoteArtifact:
        raise AssertionError("a healthy archived MP4 must not be downloaded again")


class VideoOnlyMediaAccess(StaticMediaAccess):
    async def open_cover(self, _: tuple[str, ...]) -> RemoteArtifact:
        raise AssertionError("a healthy archived cover must not be downloaded again")


class RecordingFolderOpener:
    def __init__(self) -> None:
        self.opened: list[Path] = []

    def open_folder(self, path: Path) -> None:
        self.opened.append(path)


class SimulatedProcessCrash(BaseException):
    pass


class CrashAfterPromotingFile:
    def promote(self, part_path: Path, final_path: Path) -> None:
        part_path.replace(final_path)
        raise SimulatedProcessCrash


class BlockingFirstPromotion:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self._calls = 0

    def promote(self, part_path: Path, final_path: Path) -> None:
        self._calls += 1
        if self._calls == 1:
            self.started.set()
            if not self.release.wait(timeout=5):
                raise TimeoutError("test did not release promotion")
        part_path.replace(final_path)


class BlockingVideoMediaAccess(StaticMediaAccess):
    def __init__(self, payload: bytes) -> None:
        super().__init__(payload)
        self.started = threading.Event()
        self.release = threading.Event()

    async def open_video(
        self,
        cdn_mirror_urls: tuple[str, ...],
    ) -> RemoteArtifact:
        self.started.set()
        if not await asyncio.to_thread(self.release.wait, 5):
            raise TimeoutError("test did not release video preparation")
        return await super().open_video(cdn_mirror_urls)


@pytest.mark.asyncio
async def test_single_archive_rejects_a_relative_archive_root(tmp_path: Path) -> None:
    archive = ManagedArchive(
        database_path=tmp_path / "archive.db",
        work_access=UnexpectedWorkAccess(),
        media_access=UnexpectedMediaAccess(),
    )

    with pytest.raises(AppError) as error:
        await archive.archive_single(
            SingleArchiveRequest(
                aweme_id="7429378937383308594",
                archive_root=Path("relative-root"),
            )
        )

    assert error.value.code == "ARCHIVE_ROOT_INVALID"
    assert error.value.status_code == 400
    assert not (tmp_path / "archive.db").exists()


@pytest.mark.asyncio
async def test_single_archive_persists_three_levels_and_promotes_valid_mp4(
    tmp_path: Path,
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    payload = valid_mp4()
    media = StaticMediaAccess(payload)
    archive = ManagedArchive(
        database_path=tmp_path / "archive.db",
        work_access=StaticWorkAccess(),
        media_access=media,
    )

    result = await archive.archive_single(
        SingleArchiveRequest(
            aweme_id="7429378937383308594",
            archive_root=root,
        )
    )

    assert result.operation.lifecycle == "finished"
    assert result.operation.result == "success"
    assert result.source_task.lifecycle == "finished"
    assert result.work_task.lifecycle == "finished"
    assert result.archive_item.status == "archived"
    assert result.archive_item.aweme_id == "7429378937383308594"
    assert media.requests == [
        (
            "https://v95-web.douyinvod.com/1080-a.mp4",
            "https://v11-web.douyinvod.com/1080-b.mp4",
        )
    ]

    work_directory = root / result.archive_item.relative_directory
    video_path = work_directory / "7429378937383308594.mp4"
    assert video_path.read_bytes() == payload
    assert not video_path.with_suffix(".mp4.part").exists()
    assert (tmp_path / "archive.db").is_file()
    database_bytes = (tmp_path / "archive.db").read_bytes()
    assert b"douyinvod.com" not in database_bytes


@pytest.mark.asyncio
async def test_default_archive_contains_valid_cover_and_filtered_metadata(
    tmp_path: Path,
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    media = StaticMediaAccess(valid_mp4())
    archive = ManagedArchive(
        database_path=tmp_path / "archive.db",
        work_access=StaticWorkAccess(),
        media_access=media,
    )

    result = await archive.archive_single(
        SingleArchiveRequest("7429378937383308594", root)
    )

    work_directory = root / result.archive_item.relative_directory
    cover_path = work_directory / "cover.png"
    metadata_path = work_directory / "metadata.json"
    with Image.open(cover_path) as cover:
        cover.verify()
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["schema_version"] == 1
    assert metadata["work"]["aweme_id"] == "7429378937383308594"
    assert metadata["work"]["author"] == {
        "stable_id": "MS4wLjABAAAAstable",
        "nickname": "测试作者",
    }
    assert metadata["work"]["public_metrics"]["likes"] == 12
    assert metadata["work"]["music"]["stable_id"] == "music-123"
    assert metadata["work"]["tags"] == ["归档"]
    assert metadata["discovery"]["source_type"] == "single_work"
    assert {artifact["kind"] for artifact in metadata["artifacts"]} == {
        "video",
        "cover",
    }
    assert all(not Path(artifact["path"]).is_absolute() for artifact in metadata["artifacts"])
    assert all(artifact["size_bytes"] > 0 for artifact in metadata["artifacts"])
    assert all(len(artifact["sha256"]) == 64 for artifact in metadata["artifacts"])
    for artifact in metadata["artifacts"]:
        artifact_bytes = (work_directory / artifact["path"]).read_bytes()
        assert artifact["size_bytes"] == len(artifact_bytes)
        assert artifact["sha256"] == hashlib.sha256(artifact_bytes).hexdigest()
    serialized = metadata_path.read_bytes()
    assert b"douyinvod.com" not in serialized
    assert b"douyinpic.com" not in serialized
    assert b"cookie" not in serialized.lower()
    assert media.cover_requests == [
        ("https://p3.douyinpic.com/cover.png",)
    ]


@pytest.mark.asyncio
async def test_missing_cover_is_reported_and_repaired_without_redownloading_video(
    tmp_path: Path,
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    database = tmp_path / "archive.db"
    original = ManagedArchive(
        database_path=database,
        work_access=StaticWorkAccess(),
        media_access=StaticMediaAccess(valid_mp4()),
    )
    completed = await original.archive_single(
        SingleArchiveRequest("7429378937383308594", root)
    )
    work_directory = root / completed.archive_item.relative_directory
    video_path = work_directory / "7429378937383308594.mp4"
    original_video_hash = hashlib.sha256(video_path.read_bytes()).hexdigest()
    (work_directory / "cover.png").unlink()

    damaged = original.get_work_archive("7429378937383308594")

    assert damaged is not None
    assert damaged.status == "needs_repair"

    repair_media = CoverOnlyMediaAccess(valid_mp4())
    restarted = ManagedArchive(
        database_path=database,
        work_access=StaticWorkAccess(),
        media_access=repair_media,
    )
    repaired = await restarted.archive_single(
        SingleArchiveRequest("7429378937383308594", root)
    )

    assert repaired.archive_item.status == "archived"
    assert repair_media.requests == []
    assert repair_media.cover_requests == [
        ("https://p3.douyinpic.com/cover.png",)
    ]
    assert hashlib.sha256(video_path.read_bytes()).hexdigest() == original_video_hash
    assert (work_directory / "cover.png").is_file()
    assert (work_directory / "metadata.json").is_file()


@pytest.mark.asyncio
async def test_corrupt_metadata_is_rebuilt_without_downloading_healthy_media(
    tmp_path: Path,
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    database = tmp_path / "archive.db"
    original = ManagedArchive(
        database_path=database,
        work_access=StaticWorkAccess(),
        media_access=StaticMediaAccess(valid_mp4()),
    )
    completed = await original.archive_single(
        SingleArchiveRequest("7429378937383308594", root)
    )
    work_directory = root / completed.archive_item.relative_directory
    metadata_path = work_directory / "metadata.json"
    metadata_path.write_text("{}", encoding="utf-8")

    damaged = original.get_work_archive("7429378937383308594")
    assert damaged is not None
    assert damaged.status == "needs_repair"

    restarted = ManagedArchive(
        database_path=database,
        work_access=StaticWorkAccess(),
        media_access=UnexpectedMediaAccess(),
    )
    repaired = await restarted.archive_single(
        SingleArchiveRequest("7429378937383308594", root)
    )

    assert repaired.archive_item.status == "archived"
    rebuilt = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert rebuilt["schema_version"] == 1
    assert rebuilt["work"]["aweme_id"] == "7429378937383308594"


@pytest.mark.asyncio
async def test_corrupt_video_is_repaired_without_redownloading_healthy_cover(
    tmp_path: Path,
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    database = tmp_path / "archive.db"
    original = ManagedArchive(
        database_path=database,
        work_access=StaticWorkAccess(),
        media_access=StaticMediaAccess(valid_mp4()),
    )
    completed = await original.archive_single(
        SingleArchiveRequest("7429378937383308594", root)
    )
    work_directory = root / completed.archive_item.relative_directory
    video_path = work_directory / "7429378937383308594.mp4"
    cover_path = work_directory / "cover.png"
    original_cover_hash = hashlib.sha256(cover_path.read_bytes()).hexdigest()
    video_path.write_bytes(b"damaged")

    damaged = original.get_work_archive("7429378937383308594")
    assert damaged is not None
    assert damaged.status == "needs_repair"

    repair_media = VideoOnlyMediaAccess(valid_mp4())
    restarted = ManagedArchive(
        database_path=database,
        work_access=StaticWorkAccess(),
        media_access=repair_media,
    )
    repaired = await restarted.archive_single(
        SingleArchiveRequest("7429378937383308594", root)
    )

    assert repaired.archive_item.status == "archived"
    assert len(repair_media.requests) == 1
    assert repair_media.cover_requests == []
    assert hashlib.sha256(cover_path.read_bytes()).hexdigest() == original_cover_hash
    assert video_path.read_bytes() == valid_mp4()


@pytest.mark.asyncio
@pytest.mark.parametrize("cover_payload", [b"not-an-image", truncated_jpeg()])
async def test_undecodable_cover_never_creates_a_complete_archive(
    tmp_path: Path,
    cover_payload: bytes,
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    archive = ManagedArchive(
        database_path=tmp_path / "archive.db",
        work_access=StaticWorkAccess(),
        media_access=StaticMediaAccess(valid_mp4(), cover_payload),
    )

    with pytest.raises(AppError) as error:
        await archive.archive_single(
            SingleArchiveRequest("7429378937383308594", root)
        )

    assert error.value.code == "ARCHIVE_FAILED"
    assert list(root.rglob("*.part")) == []
    assert list(root.rglob("*.mp4")) == []
    assert list(root.rglob("cover.*")) == []
    assert list(root.rglob("metadata.json")) == []
    assert archive.get_work_archive("7429378937383308594") is None


@pytest.mark.asyncio
async def test_prepare_cleanup_failure_does_not_mask_archive_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    archive = ManagedArchive(
        database_path=tmp_path / "archive.db",
        work_access=StaticWorkAccess(),
        media_access=StaticMediaAccess(valid_mp4(), b"not-an-image"),
    )
    original_unlink = Path.unlink

    def locked_part_unlink(path: Path, missing_ok: bool = False) -> None:
        if path.name.endswith(".part"):
            raise PermissionError("simulated file lock")
        original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", locked_part_unlink)

    with pytest.raises(AppError) as error:
        await archive.archive_single(
            SingleArchiveRequest("7429378937383308594", root)
        )

    assert error.value.code == "ARCHIVE_FAILED"
    with sqlite3.connect(tmp_path / "archive.db") as connection:
        result = connection.execute(
            "SELECT result FROM archive_operations"
        ).fetchone()
    assert result == ("failed",)


@pytest.mark.asyncio
async def test_promotion_transaction_failure_removes_unregistered_parts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    archive = ManagedArchive(
        database_path=tmp_path / "archive.db",
        work_access=StaticWorkAccess(),
        media_access=StaticMediaAccess(valid_mp4()),
    )

    def fail_promotion(*_: object, **__: object) -> None:
        raise sqlite3.OperationalError("simulated transaction failure")

    monkeypatch.setattr(archive._store, "prepare_promotion", fail_promotion)

    with pytest.raises(sqlite3.OperationalError):
        await archive.archive_single(
            SingleArchiveRequest("7429378937383308594", root)
        )

    assert list(root.rglob("*.part")) == []
    assert list(root.rglob("*.mp4")) == []
    assert list(root.rglob("cover.*")) == []
    assert list(root.rglob("metadata.json")) == []
    assert archive.get_work_archive("7429378937383308594") is None


@pytest.mark.asyncio
async def test_part_cleanup_failure_still_persists_failed_task_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    database = tmp_path / "archive.db"
    archive = ManagedArchive(
        database_path=database,
        work_access=StaticWorkAccess(),
        media_access=StaticMediaAccess(valid_mp4()),
    )

    def fail_promotion(*_: object, **__: object) -> None:
        raise sqlite3.OperationalError("primary promotion failure")

    original_unlink = Path.unlink

    def locked_part_unlink(path: Path, missing_ok: bool = False) -> None:
        if path.name.endswith(".part"):
            raise PermissionError("simulated file lock")
        original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(archive._store, "prepare_promotion", fail_promotion)
    monkeypatch.setattr(Path, "unlink", locked_part_unlink)

    with pytest.raises(sqlite3.OperationalError, match="primary promotion failure"):
        await archive.archive_single(
            SingleArchiveRequest("7429378937383308594", root)
        )

    with sqlite3.connect(database) as connection:
        result = connection.execute(
            "SELECT result FROM archive_operations"
        ).fetchone()
    assert result == ("failed",)
    assert list(root.rglob("*.part"))


@pytest.mark.asyncio
async def test_single_archive_persists_failed_tasks_when_work_resolution_fails(
    tmp_path: Path,
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    database = tmp_path / "archive.db"
    archive = ManagedArchive(
        database_path=database,
        work_access=FailingWorkAccess(),
        media_access=UnexpectedMediaAccess(),
    )

    with pytest.raises(AppError) as error:
        await archive.archive_single(
            SingleArchiveRequest("7429378937383308594", root)
        )

    assert error.value.code == "UPSTREAM_BLOCKED"
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT lifecycle, phase, result FROM archive_operations"
        ).fetchall() == [("finished", "idle", "failed")]
        assert connection.execute(
            "SELECT lifecycle, phase, result FROM source_tasks"
        ).fetchall() == [("finished", "idle", "failed")]
        assert connection.execute(
            "SELECT lifecycle, phase, result FROM work_tasks"
        ).fetchall() == [("finished", "idle", "failed")]


@pytest.mark.asyncio
async def test_completed_archive_survives_restart_and_skips_duplicate_download(
    tmp_path: Path,
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    database = tmp_path / "archive.db"
    first_media = StaticMediaAccess(valid_mp4())
    first = ManagedArchive(
        database_path=database,
        work_access=StaticWorkAccess(),
        media_access=first_media,
    )
    await first.archive_single(
        SingleArchiveRequest("7429378937383308594", root)
    )
    restarted = ManagedArchive(
        database_path=database,
        work_access=UnexpectedWorkAccess(),
        media_access=UnexpectedMediaAccess(),
    )

    archived = restarted.get_work_archive("7429378937383308594")
    duplicate = await restarted.archive_single(
        SingleArchiveRequest("7429378937383308594", root)
    )

    assert archived is not None
    assert archived.status == "archived"
    assert duplicate.archive_item == archived
    assert duplicate.operation.result == "success"
    assert first_media.requests != []


@pytest.mark.asyncio
async def test_unavailable_archive_root_is_not_misreported_as_needs_repair(
    tmp_path: Path,
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    archive = ManagedArchive(
        database_path=tmp_path / "archive.db",
        work_access=StaticWorkAccess(),
        media_access=StaticMediaAccess(valid_mp4()),
    )
    await archive.archive_single(
        SingleArchiveRequest("7429378937383308594", root)
    )
    disconnected = tmp_path / "disconnected-library"
    root.rename(disconnected)

    item = archive.get_work_archive("7429378937383308594")

    assert item is not None
    assert item.status == "location_unavailable"
    with pytest.raises(AppError) as error:
        archive.open_work_folder("7429378937383308594")
    assert error.value.code == "ARCHIVE_LOCATION_UNAVAILABLE"


@pytest.mark.asyncio
async def test_missing_work_directory_is_repairable_when_root_is_available(
    tmp_path: Path,
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    database = tmp_path / "archive.db"
    archive = ManagedArchive(
        database_path=database,
        work_access=StaticWorkAccess(),
        media_access=StaticMediaAccess(valid_mp4()),
    )
    completed = await archive.archive_single(
        SingleArchiveRequest("7429378937383308594", root)
    )
    work_directory = root / completed.archive_item.relative_directory
    work_directory.rename(tmp_path / "removed-work-directory")

    item = archive.get_work_archive("7429378937383308594")

    assert item is not None
    assert item.status == "needs_repair"
    repaired = await archive.archive_single(
        SingleArchiveRequest("7429378937383308594", root)
    )
    assert repaired.archive_item.status == "archived"
    assert (work_directory / "7429378937383308594.mp4").is_file()
    assert (work_directory / "cover.png").is_file()
    assert (work_directory / "metadata.json").is_file()


@pytest.mark.asyncio
async def test_restart_recovers_a_file_promoted_just_before_process_crash(
    tmp_path: Path,
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    database = tmp_path / "archive.db"
    interrupted = ManagedArchive(
        database_path=database,
        work_access=StaticWorkAccess(),
        media_access=StaticMediaAccess(valid_mp4()),
        file_promoter=CrashAfterPromotingFile(),
    )

    with pytest.raises(SimulatedProcessCrash):
        await interrupted.archive_single(
            SingleArchiveRequest("7429378937383308594", root)
        )

    restarted = ManagedArchive(
        database_path=database,
        work_access=UnexpectedWorkAccess(),
        media_access=UnexpectedMediaAccess(),
    )
    recovered = restarted.get_work_archive("7429378937383308594")

    assert recovered is not None
    assert recovered.status == "archived"
    assert (
        root / recovered.relative_directory / "7429378937383308594.mp4"
    ).is_file()
    assert list(root.rglob("*.part")) == []


@pytest.mark.asyncio
async def test_status_audit_waits_for_active_promotion(tmp_path: Path) -> None:
    root = tmp_path / "library"
    root.mkdir()
    promoter = BlockingFirstPromotion()
    archive = ManagedArchive(
        database_path=tmp_path / "archive.db",
        work_access=StaticWorkAccess(),
        media_access=StaticMediaAccess(valid_mp4()),
        file_promoter=promoter,
    )
    request = SingleArchiveRequest("7429378937383308594", root)
    archive_task = asyncio.create_task(
        asyncio.to_thread(asyncio.run, archive.archive_single(request))
    )
    try:
        assert await asyncio.to_thread(promoter.started.wait, 5)
        status_task = asyncio.create_task(
            asyncio.to_thread(archive.get_work_archive, request.aweme_id)
        )
        await asyncio.sleep(0.05)
        assert not status_task.done()
    finally:
        promoter.release.set()

    result = await archive_task
    status = await status_task
    assert result.archive_item.status == "archived"
    assert status is not None
    assert status.status == "archived"


@pytest.mark.asyncio
async def test_status_audit_waits_for_active_artifact_preparation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    media = BlockingVideoMediaAccess(valid_mp4())
    archive = ManagedArchive(
        database_path=tmp_path / "archive.db",
        work_access=StaticWorkAccess(),
        media_access=media,
    )
    request = SingleArchiveRequest("7429378937383308594", root)
    archive_task = asyncio.create_task(archive.archive_single(request))
    try:
        assert await asyncio.to_thread(media.started.wait, 5)
        status_task = asyncio.create_task(
            asyncio.to_thread(archive.get_work_archive, request.aweme_id)
        )
        await asyncio.sleep(0.05)
        assert not status_task.done()
    finally:
        media.release.set()

    await archive_task
    status = await status_task
    assert status is not None
    assert status.status == "archived"


@pytest.mark.asyncio
async def test_cancelled_archive_does_not_orphan_its_integrity_lock(
    tmp_path: Path,
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    archive = ManagedArchive(
        database_path=tmp_path / "archive.db",
        work_access=StaticWorkAccess(),
        media_access=StaticMediaAccess(valid_mp4()),
    )
    aweme_id = "7429378937383308594"
    integrity_lock = archive._integrity_lock(aweme_id)
    integrity_lock.acquire()
    task = asyncio.create_task(
        archive.archive_single(SingleArchiveRequest(aweme_id, root))
    )
    await asyncio.sleep(0.05)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    integrity_lock.release()

    assert await asyncio.to_thread(integrity_lock.acquire, True, 1)
    integrity_lock.release()


@pytest.mark.asyncio
async def test_archive_integrity_audit_runs_off_event_loop(tmp_path: Path) -> None:
    root = tmp_path / "library"
    root.mkdir()
    archive = ManagedArchive(
        database_path=tmp_path / "archive.db",
        work_access=StaticWorkAccess(),
        media_access=StaticMediaAccess(valid_mp4()),
    )
    request = SingleArchiveRequest("7429378937383308594", root)
    await archive.archive_single(request)
    event_loop_thread = threading.get_ident()
    audit_threads: list[int] = []
    original_audit = archive._artifact_pipeline.audit

    def recording_audit(*args: object, **kwargs: object) -> object:
        audit_threads.append(threading.get_ident())
        return original_audit(*args, **kwargs)  # type: ignore[arg-type]

    archive._artifact_pipeline.audit = recording_audit  # type: ignore[method-assign]

    await archive.archive_single(request)

    assert audit_threads
    assert event_loop_thread not in audit_threads


@pytest.mark.asyncio
async def test_cancelled_archive_keeps_lock_until_thread_audit_finishes(
    tmp_path: Path,
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    archive = ManagedArchive(
        database_path=tmp_path / "archive.db",
        work_access=StaticWorkAccess(),
        media_access=StaticMediaAccess(valid_mp4()),
    )
    request = SingleArchiveRequest("7429378937383308594", root)
    await archive.archive_single(request)
    started = threading.Event()
    release = threading.Event()
    original_audit = archive._artifact_pipeline.audit

    def blocking_audit(*args: object, **kwargs: object) -> object:
        started.set()
        if not release.wait(timeout=5):
            raise TimeoutError("test did not release integrity audit")
        return original_audit(*args, **kwargs)  # type: ignore[arg-type]

    archive._artifact_pipeline.audit = blocking_audit  # type: ignore[method-assign]
    archive_task = asyncio.create_task(archive.archive_single(request))
    assert await asyncio.to_thread(started.wait, 5)
    archive_task.cancel()
    status_task = asyncio.create_task(
        asyncio.to_thread(archive.get_work_archive, request.aweme_id)
    )
    try:
        await asyncio.sleep(0.05)
        assert not archive_task.done()
        assert not status_task.done()
    finally:
        release.set()

    with pytest.raises(asyncio.CancelledError):
        await archive_task
    status = await status_task
    assert status is not None
    assert status.status == "archived"


@pytest.mark.asyncio
async def test_concurrent_requests_for_one_work_share_one_archive_operation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    work_access = BlockingWorkAccess()
    media = StaticMediaAccess(valid_mp4())
    archive = ManagedArchive(
        database_path=tmp_path / "archive.db",
        work_access=work_access,
        media_access=media,
    )
    request = SingleArchiveRequest("7429378937383308594", root)

    first = asyncio.create_task(archive.archive_single(request))
    await work_access.started.wait()
    second = asyncio.create_task(archive.archive_single(request))
    await asyncio.sleep(0)
    work_access.release.set()
    first_result, second_result = await asyncio.gather(first, second)

    assert work_access.calls == 1
    assert len(media.requests) == 1
    assert first_result == second_result
    assert list(root.rglob("*.mp4")) != []
    assert list(root.rglob("*.part")) == []


@pytest.mark.asyncio
async def test_completed_archive_opens_only_its_registered_work_directory(
    tmp_path: Path,
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    opener = RecordingFolderOpener()
    archive = ManagedArchive(
        database_path=tmp_path / "archive.db",
        work_access=StaticWorkAccess(),
        media_access=StaticMediaAccess(valid_mp4()),
        folder_opener=opener,
    )
    completed = await archive.archive_single(
        SingleArchiveRequest("7429378937383308594", root)
    )

    archive.open_work_folder("7429378937383308594")

    assert opener.opened == [
        (root / completed.archive_item.relative_directory).resolve(strict=True)
    ]


@pytest.mark.asyncio
async def test_invalid_mp4_removes_partial_and_never_registers_archive(
    tmp_path: Path,
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    archive = ManagedArchive(
        database_path=tmp_path / "archive.db",
        work_access=StaticWorkAccess(),
        media_access=StaticMediaAccess(b"not-an-mp4"),
    )

    with pytest.raises(AppError) as error:
        await archive.archive_single(
            SingleArchiveRequest("7429378937383308594", root)
        )

    assert error.value.code == "ARCHIVE_FAILED"
    assert list(root.rglob("*.part")) == []
    assert list(root.rglob("*.mp4")) == []
    assert archive.get_work_archive("7429378937383308594") is None


@pytest.mark.asyncio
async def test_archive_refuses_a_preexisting_reparse_point_in_its_write_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    author_digest = hashlib.sha256(b"MS4wLjABAAAAstable").hexdigest()[:16]
    author_directory = root / f"author-{author_digest}"
    author_directory.mkdir()
    monkeypatch.setattr(
        archive_paths,
        "is_reparse_point",
        lambda path: path == author_directory,
    )
    archive = ManagedArchive(
        database_path=tmp_path / "archive.db",
        work_access=StaticWorkAccess(),
        media_access=StaticMediaAccess(valid_mp4()),
    )

    with pytest.raises(AppError) as error:
        await archive.archive_single(
            SingleArchiveRequest("7429378937383308594", root)
        )

    assert error.value.code == "ARCHIVE_PATH_INVALID"
    assert list(outside.rglob("*.mp4")) == []
    assert list(outside.rglob("*.part")) == []


@pytest.mark.skipif(os.name != "nt", reason="Windows directory-handle semantics")
@pytest.mark.asyncio
async def test_archive_pins_its_validated_write_directory_until_promotion(
    tmp_path: Path,
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    author_digest = hashlib.sha256(b"MS4wLjABAAAAstable").hexdigest()[:16]
    work_directory = (
        root
        / f"author-{author_digest}"
        / "2024"
        / "work-7429378937383308594"
    )
    media = DirectoryReplacementAttempt(valid_mp4(), work_directory)
    archive = ManagedArchive(
        database_path=tmp_path / "archive.db",
        work_access=StaticWorkAccess(),
        media_access=media,
    )

    result = await archive.archive_single(
        SingleArchiveRequest("7429378937383308594", root)
    )

    assert media.replacement_was_blocked is True
    assert (
        root / result.archive_item.relative_directory / "7429378937383308594.mp4"
    ).is_file()
