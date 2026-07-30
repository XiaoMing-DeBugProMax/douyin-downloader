import asyncio
import hashlib
import os
import sqlite3
import struct
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from douyin_downloader import archive_paths
from douyin_downloader.archive import ManagedArchive, RemoteVideo, SingleArchiveRequest
from douyin_downloader.domain import (
    AppError,
    AuthorSnapshot,
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
                published_at=1_720_000_000,
                duration_ms=15_000,
                author=AuthorSnapshot(
                    stable_id="MS4wLjABAAAAstable",
                    nickname="测试作者",
                ),
                music=None,
                public_metrics=PublicMetrics(None, None, None, None),
            ),
            cover_urls=(),
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
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.requests: list[tuple[str, ...]] = []

    async def open_video(self, cdn_mirror_urls: tuple[str, ...]) -> RemoteVideo:
        self.requests.append(cdn_mirror_urls)

        async def chunks() -> AsyncIterator[bytes]:
            midpoint = len(self.payload) // 2
            yield self.payload[:midpoint]
            yield self.payload[midpoint:]

        return RemoteVideo(
            content_type="video/mp4",
            expected_size=len(self.payload),
            chunks=chunks(),
        )


class DirectoryReplacementAttempt(StaticMediaAccess):
    def __init__(self, payload: bytes, work_directory: Path) -> None:
        super().__init__(payload)
        self.work_directory = work_directory
        self.replacement_was_blocked = False

    async def open_video(self, cdn_mirror_urls: tuple[str, ...]) -> RemoteVideo:
        moved_directory = self.work_directory.with_name("moved-work")
        try:
            self.work_directory.rename(moved_directory)
        except OSError:
            self.replacement_was_blocked = True
        else:
            self.work_directory.mkdir()
        return await super().open_video(cdn_mirror_urls)


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
