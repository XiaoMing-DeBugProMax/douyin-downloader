import asyncio
from pathlib import Path

import pytest

from douyin_downloader.archive import ManagedArchive, RemoteArtifact, SingleArchiveRequest
from douyin_downloader.domain import AppError
from tests.integration.test_managed_archive import (
    StaticMediaAccess,
    StaticWorkAccess,
    valid_mp4,
    valid_png,
)


class FailingWorkAccess:
    async def fetch_work(self, aweme_id: str) -> None:
        raise AppError("UPSTREAM_BLOCKED", "解析服务暂时不可用，请稍后重试。", 502)


class UnsupportedWorkAccess:
    async def fetch_work(self, aweme_id: str) -> None:
        raise AppError("UNSUPPORTED_CONTENT", "private upstream details", 422)


class UnexpectedMediaAccess:
    async def open_video(self, cdn_mirror_urls: tuple[str, ...]) -> None:
        raise AssertionError(cdn_mirror_urls)

    async def open_cover(self, cdn_mirror_urls: tuple[str, ...]) -> None:
        raise AssertionError(cdn_mirror_urls)


class BlockingFailingWorkAccess:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def fetch_work(self, aweme_id: str) -> None:
        self.started.set()
        await self.release.wait()
        raise AppError("UPSTREAM_BLOCKED", "private upstream details", 502)


class BlockingVideoMediaAccess:
    def __init__(self) -> None:
        self.video = valid_mp4()
        self.cover = valid_png()
        self.first_chunk_written = asyncio.Event()
        self.release = asyncio.Event()

    async def open_video(self, cdn_mirror_urls: tuple[str, ...]) -> RemoteArtifact:
        midpoint = len(self.video) // 2

        async def chunks():
            yield self.video[:midpoint]
            self.first_chunk_written.set()
            await self.release.wait()
            yield self.video[midpoint:]

        return RemoteArtifact("video/mp4", len(self.video), chunks())

    async def open_cover(self, cdn_mirror_urls: tuple[str, ...]) -> RemoteArtifact:
        async def chunks():
            yield self.cover

        return RemoteArtifact("image/png", len(self.cover), chunks())


class BlockingCoverMediaAccess:
    def __init__(self) -> None:
        self.video = valid_mp4()
        self.cover = valid_png()
        self.first_cover_chunk_written = asyncio.Event()
        self.release = asyncio.Event()

    async def open_video(self, cdn_mirror_urls: tuple[str, ...]) -> RemoteArtifact:
        async def chunks():
            yield self.video

        return RemoteArtifact("video/mp4", len(self.video), chunks())

    async def open_cover(self, cdn_mirror_urls: tuple[str, ...]) -> RemoteArtifact:
        midpoint = len(self.cover) // 2

        async def chunks():
            await asyncio.sleep(0.3)
            yield self.cover[:midpoint]
            self.first_cover_chunk_written.set()
            await self.release.wait()
            yield self.cover[midpoint:]

        return RemoteArtifact("image/png", len(self.cover), chunks())

@pytest.mark.asyncio
async def test_failed_single_archive_remains_queryable_as_three_task_levels(
    tmp_path: Path,
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    database = tmp_path / "archive.db"
    archive = ManagedArchive(
        database_path=database,
        work_access=FailingWorkAccess(),  # type: ignore[arg-type]
        media_access=UnexpectedMediaAccess(),  # type: ignore[arg-type]
    )

    with pytest.raises(AppError):
        await archive.archive_single(
            SingleArchiveRequest("7429378937383308594", root)
        )

    restarted = ManagedArchive(
        database_path=database,
        work_access=FailingWorkAccess(),  # type: ignore[arg-type]
        media_access=UnexpectedMediaAccess(),  # type: ignore[arg-type]
    )
    operations = restarted.list_task_operations()

    assert len(operations) == 1
    operation = operations[0]
    assert (operation.task.lifecycle, operation.task.phase, operation.task.result) == (
        "finished",
        "idle",
        "failed",
    )
    assert len(operation.source_tasks) == 1
    source = operation.source_tasks[0]
    assert (source.task.lifecycle, source.task.phase, source.task.result) == (
        "finished",
        "idle",
        "failed",
    )
    assert len(source.work_tasks) == 1
    work = source.work_tasks[0]
    assert work.aweme_id == "7429378937383308594"
    assert (work.task.lifecycle, work.task.phase, work.task.result) == (
        "finished",
        "idle",
        "failed",
    )


@pytest.mark.asyncio
async def test_failed_task_exposes_only_stable_actionable_error_details(
    tmp_path: Path,
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    archive = ManagedArchive(
        database_path=tmp_path / "archive.db",
        work_access=FailingWorkAccess(),  # type: ignore[arg-type]
        media_access=UnexpectedMediaAccess(),  # type: ignore[arg-type]
    )

    with pytest.raises(AppError):
        await archive.archive_single(
            SingleArchiveRequest("7429378937383308594", root)
        )

    operation = archive.list_task_operations()[0]
    error = operation.source_tasks[0].work_tasks[0].task.error
    assert error is not None
    assert error.code == "UPSTREAM_BLOCKED"
    assert error.message == "解析服务暂时不可用。"
    assert error.suggestion == "请稍后重试此归档操作。"
    assert operation.task.progress.percentage is None


@pytest.mark.asyncio
async def test_known_failure_keeps_its_safe_specific_error_code(tmp_path: Path) -> None:
    root = tmp_path / "library"
    root.mkdir()
    archive = ManagedArchive(
        database_path=tmp_path / "archive.db",
        work_access=UnsupportedWorkAccess(),  # type: ignore[arg-type]
        media_access=UnexpectedMediaAccess(),  # type: ignore[arg-type]
    )

    with pytest.raises(AppError):
        await archive.archive_single(
            SingleArchiveRequest("7429378937383308594", root)
        )

    error = archive.list_task_operations()[0].task.error
    assert error is not None
    assert error.code == "UNSUPPORTED_CONTENT"
    assert error.message == "当前作品类型暂不支持归档。"
    assert error.suggestion == "请选择公开的单视频作品后重试。"
    assert "private" not in f"{error.message} {error.suggestion}"


@pytest.mark.asyncio
async def test_running_task_hides_unreliable_percentage_speed_and_eta(
    tmp_path: Path,
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    work_access = BlockingFailingWorkAccess()
    archive = ManagedArchive(
        database_path=tmp_path / "archive.db",
        work_access=work_access,  # type: ignore[arg-type]
        media_access=UnexpectedMediaAccess(),  # type: ignore[arg-type]
    )

    running = asyncio.create_task(
        archive.archive_single(
            SingleArchiveRequest("7429378937383308594", root)
        )
    )
    await work_access.started.wait()
    try:
        operation = archive.list_task_operations()[0]
        assert (operation.task.lifecycle, operation.task.phase, operation.task.result) == (
            "running",
            "resolving",
            "none",
        )
        assert operation.task.progress.completed_items == 0
        assert operation.task.progress.total_items == 1
        assert operation.task.progress.completed_bytes == 0
        assert operation.task.progress.total_bytes is None
        assert operation.task.progress.percentage is None
        assert operation.task.progress.speed_bytes_per_second is None
        assert operation.task.progress.eta_seconds is None
    finally:
        work_access.release.set()
        with pytest.raises(AppError):
            await running


@pytest.mark.asyncio
async def test_running_download_persists_bytes_without_inventing_a_total(
    tmp_path: Path,
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    media = BlockingVideoMediaAccess()
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
    await media.first_chunk_written.wait()
    try:
        operation = archive.list_task_operations()[0]
        assert operation.task.phase == "downloading"
        assert operation.task.progress.completed_bytes == len(media.video) // 2
        assert operation.task.progress.total_bytes is None
        assert operation.task.progress.percentage is None
    finally:
        media.release.set()
        await running

@pytest.mark.asyncio
async def test_reliable_download_sample_exposes_total_percentage_speed_and_eta(
    tmp_path: Path,
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    media = BlockingCoverMediaAccess()
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
    await media.first_cover_chunk_written.wait()
    try:
        progress = archive.list_task_operations()[0].task.progress
        expected_completed = len(media.video) + len(media.cover) // 2
        expected_total = len(media.video) + len(media.cover)
        assert progress.completed_bytes == expected_completed
        assert progress.total_bytes == expected_total
        assert progress.percentage == round(expected_completed / expected_total * 100, 1)
        assert progress.speed_bytes_per_second is not None
        assert progress.speed_bytes_per_second > 0
        assert progress.eta_seconds is not None
        assert progress.eta_seconds >= 0
    finally:
        media.release.set()
        await running

    finished_progress = archive.list_task_operations()[0].task.progress
    assert finished_progress.speed_bytes_per_second is None
    assert finished_progress.eta_seconds is None


@pytest.mark.asyncio
async def test_clearing_finished_history_preserves_archive_and_artifacts(
    tmp_path: Path,
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    archive = ManagedArchive(
        database_path=tmp_path / "archive.db",
        work_access=StaticWorkAccess(),
        media_access=StaticMediaAccess(valid_mp4()),
    )
    completed = await archive.archive_single(
        SingleArchiveRequest("7429378937383308594", root)
    )
    work_directory = root / completed.archive_item.relative_directory

    archive.clear_task_operation(completed.operation.task_id)

    assert archive.list_task_operations() == ()
    item = archive.get_work_archive("7429378937383308594")
    assert item is not None
    assert item.status == "archived"
    assert work_directory.is_dir()
    assert {path.suffix for path in work_directory.iterdir()} >= {".mp4", ".png", ".json"}


@pytest.mark.asyncio
async def test_active_task_history_cannot_be_cleared(tmp_path: Path) -> None:
    root = tmp_path / "library"
    root.mkdir()
    work_access = BlockingFailingWorkAccess()
    archive = ManagedArchive(
        database_path=tmp_path / "archive.db",
        work_access=work_access,  # type: ignore[arg-type]
        media_access=UnexpectedMediaAccess(),  # type: ignore[arg-type]
    )
    running = asyncio.create_task(
        archive.archive_single(
            SingleArchiveRequest("7429378937383308594", root)
        )
    )
    await work_access.started.wait()
    operation_id = archive.list_task_operations()[0].task.task_id
    try:
        with pytest.raises(AppError) as error:
            archive.clear_task_operation(operation_id)
        assert error.value.code == "TASK_HISTORY_ACTIVE"
        assert len(archive.list_task_operations()) == 1
    finally:
        work_access.release.set()
        with pytest.raises(AppError):
            await running


@pytest.mark.asyncio
async def test_restart_marks_running_history_interrupted_and_protects_it(
    tmp_path: Path,
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    work_access = BlockingFailingWorkAccess()
    database = tmp_path / "archive.db"
    archive = ManagedArchive(
        database_path=database,
        work_access=work_access,  # type: ignore[arg-type]
        media_access=UnexpectedMediaAccess(),  # type: ignore[arg-type]
    )
    running = asyncio.create_task(
        archive.archive_single(
            SingleArchiveRequest("7429378937383308594", root)
        )
    )
    await work_access.started.wait()
    restarted = ManagedArchive(
        database_path=database,
        work_access=FailingWorkAccess(),  # type: ignore[arg-type]
        media_access=UnexpectedMediaAccess(),  # type: ignore[arg-type]
    )
    operation = restarted.list_task_operations()[0]
    try:
        assert operation.task.lifecycle == "interrupted"
        assert operation.task.phase == "idle"
        assert operation.task.result == "none"
        assert operation.source_tasks[0].task.lifecycle == "interrupted"
        assert operation.source_tasks[0].work_tasks[0].task.lifecycle == "interrupted"
        with pytest.raises(AppError) as error:
            restarted.clear_task_operation(operation.task.task_id)
        assert error.value.code == "TASK_HISTORY_ACTIVE"
    finally:
        work_access.release.set()
        with pytest.raises(AppError):
            await running
