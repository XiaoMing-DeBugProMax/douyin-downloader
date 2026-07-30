import struct
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

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


class RecordingFolderOpener:
    def __init__(self) -> None:
        self.opened: list[Path] = []

    def open_folder(self, path: Path) -> None:
        self.opened.append(path)


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
