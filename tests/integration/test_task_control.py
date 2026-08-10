import asyncio
from pathlib import Path

import pytest

from douyin_downloader.archive import ManagedArchive, RemoteArtifact, SingleArchiveRequest
from douyin_downloader.domain import AppError
from tests.integration.test_managed_archive import (
    BlockingWorkAccess,
    StaticMediaAccess,
    StaticWorkAccess,
    valid_mp4,
    valid_png,
)


class PausableMediaAccess:
    def __init__(self) -> None:
        self.video = valid_mp4()
        self.cover = valid_png()
        self.video_ready = asyncio.Event()
        self.release_first_chunk = asyncio.Event()
        self.second_chunk_requested = asyncio.Event()
        self.release_second_chunk = asyncio.Event()

    async def open_video(self, cdn_mirror_urls: tuple[str, ...]) -> RemoteArtifact:
        midpoint = len(self.video) // 2

        async def chunks():
            self.video_ready.set()
            await self.release_first_chunk.wait()
            yield self.video[:midpoint]
            self.second_chunk_requested.set()
            await self.release_second_chunk.wait()
            yield self.video[midpoint:]

        return RemoteArtifact("video/mp4", len(self.video), chunks())

    async def open_cover(self, cdn_mirror_urls: tuple[str, ...]) -> RemoteArtifact:
        async def chunks():
            yield self.cover

        return RemoteArtifact("image/png", len(self.cover), chunks())


class BlockingCoverOnlyMediaAccess:
    def __init__(self) -> None:
        self.cover = valid_png()
        self.cover_started = asyncio.Event()
        self.release_cover = asyncio.Event()

    async def open_video(self, _: tuple[str, ...]) -> RemoteArtifact:
        raise AssertionError("a validated archived video must be preserved")

    async def open_cover(self, _: tuple[str, ...]) -> RemoteArtifact:
        self.cover_started.set()
        await self.release_cover.wait()

        async def chunks():
            yield self.cover

        return RemoteArtifact("image/png", len(self.cover), chunks())


@pytest.mark.asyncio
async def test_pause_after_resolution_stops_before_media_access(tmp_path: Path) -> None:
    root = tmp_path / "library"
    root.mkdir()
    work = BlockingWorkAccess()
    media = StaticMediaAccess(valid_mp4())
    archive = ManagedArchive(
        database_path=tmp_path / "archive.db",
        work_access=work,
        media_access=media,
    )
    running = asyncio.create_task(
        archive.archive_single(
            SingleArchiveRequest("7429378937383308594", root)
        )
    )
    await work.started.wait()
    task_id = archive.list_task_operations()[0].task.task_id

    pausing = asyncio.create_task(archive.pause_task(task_id))
    await asyncio.sleep(0)
    work.release.set()
    paused = await asyncio.wait_for(pausing, timeout=1)

    assert paused.task.lifecycle == "paused"
    assert paused.task.phase == "resolving"
    assert media.requests == []
    await archive.resume_task(task_id)
    completed = await running
    assert completed.operation.result == "success"


@pytest.mark.asyncio
async def test_cancel_during_resolution_stops_before_creating_parts(tmp_path: Path) -> None:
    root = tmp_path / "library"
    root.mkdir()
    work = BlockingWorkAccess()
    media = StaticMediaAccess(valid_mp4())
    archive = ManagedArchive(
        database_path=tmp_path / "archive.db",
        work_access=work,
        media_access=media,
    )
    running = asyncio.create_task(
        archive.archive_single(
            SingleArchiveRequest("7429378937383308594", root)
        )
    )
    await work.started.wait()
    task_id = archive.list_task_operations()[0].task.task_id

    cancelling = asyncio.create_task(
        archive.cancel_task(task_id, retain_parts=True)
    )
    await asyncio.sleep(0)
    work.release.set()
    cancelled = await asyncio.wait_for(cancelling, timeout=1)

    assert cancelled.task.lifecycle == "cancelled"
    assert media.requests == []
    assert list(root.rglob("*.part")) == []
    with pytest.raises(AppError) as error:
        await running
    assert error.value.code == "TASK_CANCELLED"


@pytest.mark.asyncio
async def test_cancelled_repair_preserves_validated_archive_results(tmp_path: Path) -> None:
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
    video_bytes = video_path.read_bytes()
    (work_directory / "7429378937383308594.cover.png").unlink()

    media = BlockingCoverOnlyMediaAccess()
    repair = ManagedArchive(
        database_path=database,
        work_access=StaticWorkAccess(),
        media_access=media,
    )
    running = asyncio.create_task(
        repair.archive_single(
            SingleArchiveRequest("7429378937383308594", root)
        )
    )
    await media.cover_started.wait()
    task_id = repair.list_task_operations()[0].task.task_id
    cancelling = asyncio.create_task(
        repair.cancel_task(task_id, retain_parts=False)
    )
    await asyncio.sleep(0)
    media.release_cover.set()
    cancelled = await cancelling

    assert cancelled.task.lifecycle == "cancelled"
    with pytest.raises(AppError):
        await running
    archive_item = repair.get_work_archive("7429378937383308594")
    assert archive_item is not None
    assert archive_item.status == "needs_repair"
    assert video_path.read_bytes() == video_bytes


@pytest.mark.asyncio
async def test_pause_stops_at_chunk_boundary_and_resume_finishes_same_run(
    tmp_path: Path,
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    media = PausableMediaAccess()
    archive = ManagedArchive(
        database_path=tmp_path / "archive.db",
        work_access=StaticWorkAccess(),
        media_access=media,
    )
    running = asyncio.create_task(
        archive.archive_single(
            SingleArchiveRequest("7429378937383308594", root)
        )
    )
    await media.video_ready.wait()
    operation = archive.list_task_operations()[0]

    try:
        pausing = asyncio.create_task(
            archive.pause_task(operation.task.task_id)
        )
        media.release_first_chunk.set()
        paused = await pausing

        assert paused.task.lifecycle == "paused"
        assert paused.source_tasks[0].task.lifecycle == "paused"
        work = paused.source_tasks[0].work_tasks[0]
        assert work.task.lifecycle == "paused"
        assert work.task.phase == "downloading"
        assert work.task.progress.completed_bytes == len(media.video) // 2
        assert not media.second_chunk_requested.is_set()

        resumed = await archive.resume_task(work.task.task_id)
        assert resumed.task.lifecycle == "running"
        await media.second_chunk_requested.wait()
        media.release_second_chunk.set()
        completed = await running

        assert completed.operation.result == "success"
        assert completed.archive_item.status == "archived"
    finally:
        media.release_first_chunk.set()
        media.release_second_chunk.set()
        if not running.done():
            await running


@pytest.mark.asyncio
async def test_cancel_deletes_incomplete_part_and_returns_terminal_snapshot(
    tmp_path: Path,
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    media = PausableMediaAccess()
    archive = ManagedArchive(
        database_path=tmp_path / "archive.db",
        work_access=StaticWorkAccess(),
        media_access=media,
    )
    running = asyncio.create_task(
        archive.archive_single(
            SingleArchiveRequest("7429378937383308594", root)
        )
    )
    await media.video_ready.wait()
    operation_id = archive.list_task_operations()[0].task.task_id

    try:
        cancelling = asyncio.create_task(
            archive.cancel_task(operation_id, retain_parts=False)
        )
        media.release_first_chunk.set()
        cancelled = await cancelling

        assert cancelled.task.lifecycle == "cancelled"
        assert cancelled.task.phase == "idle"
        assert cancelled.task.result == "cancelled"
        source = cancelled.source_tasks[0]
        assert source.task.lifecycle == "cancelled"
        assert source.work_tasks[0].task.lifecycle == "cancelled"
        assert not media.second_chunk_requested.is_set()
        assert list(root.rglob("*.part")) == []
        assert archive.get_work_archive("7429378937383308594") is None
        with pytest.raises(AppError) as error:
            await running
        assert error.value.code == "TASK_CANCELLED"
    finally:
        media.release_first_chunk.set()
        media.release_second_chunk.set()
        if not running.done():
            running.cancel()
            with pytest.raises(asyncio.CancelledError):
                await running


@pytest.mark.asyncio
async def test_cancel_can_retain_only_the_completed_part_bytes(tmp_path: Path) -> None:
    root = tmp_path / "library"
    root.mkdir()
    media = PausableMediaAccess()
    archive = ManagedArchive(
        database_path=tmp_path / "archive.db",
        work_access=StaticWorkAccess(),
        media_access=media,
    )
    running = asyncio.create_task(
        archive.archive_single(
            SingleArchiveRequest("7429378937383308594", root)
        )
    )
    await media.video_ready.wait()
    operation_id = archive.list_task_operations()[0].task.task_id

    try:
        cancelling = asyncio.create_task(
            archive.cancel_task(operation_id, retain_parts=True)
        )
        media.release_first_chunk.set()
        cancelled = await cancelling

        assert cancelled.task.lifecycle == "cancelled"
        parts = list(root.rglob("*.part"))
        assert len(parts) == 1
        assert parts[0].read_bytes() == media.video[: len(media.video) // 2]
        assert archive.get_work_archive("7429378937383308594") is None
        with pytest.raises(AppError) as error:
            await running
        assert error.value.code == "TASK_CANCELLED"
    finally:
        media.release_first_chunk.set()
        media.release_second_chunk.set()
        if not running.done():
            running.cancel()
            with pytest.raises(asyncio.CancelledError):
                await running


@pytest.mark.asyncio
async def test_repeated_pause_resume_and_cancel_return_stable_snapshots(
    tmp_path: Path,
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    media = PausableMediaAccess()
    archive = ManagedArchive(
        database_path=tmp_path / "archive.db",
        work_access=StaticWorkAccess(),
        media_access=media,
    )
    running = asyncio.create_task(
        archive.archive_single(
            SingleArchiveRequest("7429378937383308594", root)
        )
    )
    await media.video_ready.wait()
    operation = archive.list_task_operations()[0]
    work_id = operation.source_tasks[0].work_tasks[0].task.task_id

    try:
        pausing = asyncio.create_task(archive.pause_task(work_id))
        media.release_first_chunk.set()
        paused = await pausing
        assert await archive.pause_task(work_id) == paused

        resumed = await archive.resume_task(work_id)
        assert await archive.resume_task(work_id) == resumed
        await media.second_chunk_requested.wait()

        cancelling = asyncio.create_task(
            archive.cancel_task(work_id, retain_parts=False)
        )
        media.release_second_chunk.set()
        cancelled = await cancelling
        with pytest.raises(AppError):
            await running

        assert (
            await archive.cancel_task(work_id, retain_parts=False)
        ) == cancelled
    finally:
        media.release_first_chunk.set()
        media.release_second_chunk.set()
        if not running.done():
            running.cancel()
            with pytest.raises(asyncio.CancelledError):
                await running
