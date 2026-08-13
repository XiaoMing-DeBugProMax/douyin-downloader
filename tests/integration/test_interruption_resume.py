import asyncio
import hashlib
from pathlib import Path

import httpx
import pytest

from douyin_downloader.archive import (
    HttpMediaAccess,
    ManagedArchive,
    RemoteArtifact,
    RemoteResumeRequest,
    SingleArchiveRequest,
)
from douyin_downloader.domain import AppError, TransientUpstreamError
from douyin_downloader.settings import NamingTemplate
from tests.integration.test_managed_archive import (
    SimulatedProcessCrash,
    StaticMediaAccess,
    StaticWorkAccess,
    UnexpectedMediaAccess,
    UnexpectedWorkAccess,
    valid_mp4,
)
from tests.integration.test_task_control import PausableMediaAccess


class RecordingWorkAccess(StaticWorkAccess):
    def __init__(self) -> None:
        self.requests: list[str] = []

    async def fetch_work(self, aweme_id: str):
        self.requests.append(aweme_id)
        return await super().fetch_work(aweme_id)


class FlakyWorkAccess(StaticWorkAccess):
    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.requests: list[str] = []

    async def fetch_work(self, aweme_id: str):
        self.requests.append(aweme_id)
        if len(self.requests) <= self.failures:
            raise TransientUpstreamError("temporary upstream failure")
        return await super().fetch_work(aweme_id)


class UserRetryWorkAccess(StaticWorkAccess):
    def __init__(self) -> None:
        self.requests: list[str] = []

    async def fetch_work(self, aweme_id: str):
        self.requests.append(aweme_id)
        if len(self.requests) == 1:
            raise AppError("VIDEO_NOT_FOUND", "没有找到该作品。", 404)
        return await super().fetch_work(aweme_id)


class RecordingRetryDelay:
    def __init__(self) -> None:
        self.attempts: list[int] = []

    async def wait(self, attempt: int) -> None:
        self.attempts.append(attempt)


class FlakyMediaAccess(StaticMediaAccess):
    def __init__(self, payload: bytes, failures: int) -> None:
        super().__init__(payload)
        self.failures = failures
        self.video_attempts = 0

    async def open_video(
        self,
        cdn_mirror_urls: tuple[str, ...],
        *,
        resume: RemoteResumeRequest | None = None,
    ) -> RemoteArtifact:
        del resume
        self.video_attempts += 1
        if self.video_attempts <= self.failures:
            raise TransientUpstreamError("temporary media failure")
        return await super().open_video(cdn_mirror_urls)


class DeterministicMediaAccess(StaticMediaAccess):
    def __init__(self, payload: bytes) -> None:
        super().__init__(payload)
        self.video_attempts = 0

    async def open_video(
        self,
        cdn_mirror_urls: tuple[str, ...],
        *,
        resume: RemoteResumeRequest | None = None,
    ) -> RemoteArtifact:
        del cdn_mirror_urls, resume
        self.video_attempts += 1
        raise AppError("DOWNLOAD_FAILED", "invalid media response", 502)


class UnclassifiedTimeoutMediaAccess(StaticMediaAccess):
    def __init__(self, payload: bytes) -> None:
        super().__init__(payload)
        self.video_attempts = 0

    async def open_video(
        self,
        cdn_mirror_urls: tuple[str, ...],
        *,
        resume: RemoteResumeRequest | None = None,
    ) -> RemoteArtifact:
        del cdn_mirror_urls, resume
        self.video_attempts += 1
        raise TimeoutError("unclassified local timeout")


class RepairRetryMediaAccess(StaticMediaAccess):
    def __init__(self, payload: bytes) -> None:
        super().__init__(payload)
        self.fail_cover = True
        self.video_attempts = 0
        self.cover_attempts = 0

    async def open_video(
        self,
        cdn_mirror_urls: tuple[str, ...],
        *,
        resume: RemoteResumeRequest | None = None,
    ) -> RemoteArtifact:
        del cdn_mirror_urls, resume
        self.video_attempts += 1
        raise AssertionError("a healthy completed video must not be downloaded again")

    async def open_cover(self, cdn_mirror_urls: tuple[str, ...]) -> RemoteArtifact:
        self.cover_attempts += 1
        if self.fail_cover:
            raise AppError("DOWNLOAD_FAILED", "invalid cover response", 502)
        return await super().open_cover(cdn_mirror_urls)


class FaultInjectedResumableMediaAccess(StaticMediaAccess):
    def __init__(self, payload: bytes, *, crash_after_first_chunk: bool) -> None:
        super().__init__(payload)
        self.crash_after_first_chunk = crash_after_first_chunk
        self.resume_requests: list[RemoteResumeRequest | None] = []

    async def open_video(
        self,
        cdn_mirror_urls: tuple[str, ...],
        *,
        resume: RemoteResumeRequest | None = None,
    ) -> RemoteArtifact:
        del cdn_mirror_urls
        self.resume_requests.append(resume)
        offset = resume.offset if resume is not None else 0

        async def chunks():
            if self.crash_after_first_chunk:
                midpoint = len(self.payload) // 2
                yield self.payload[:midpoint]
                raise SimulatedProcessCrash
            yield self.payload[offset:]

        return RemoteArtifact(
            "video/mp4",
            len(self.payload),
            chunks(),
            resume_offset=offset,
            resume_validator='"video-v1"',
        )


class ResumablePausableMediaAccess:
    def __init__(self) -> None:
        self.video = PausableMediaAccess().video
        self.cover = PausableMediaAccess().cover
        self.video_ready = asyncio.Event()
        self.release_first_chunk = asyncio.Event()
        self.resume_requests: list[RemoteResumeRequest | None] = []
        self.video_opens = 0

    async def open_video(
        self,
        cdn_mirror_urls: tuple[str, ...],
        *,
        resume: RemoteResumeRequest | None = None,
    ) -> RemoteArtifact:
        del cdn_mirror_urls
        self.video_opens += 1
        if self.video_opens == 1:
            midpoint = len(self.video) // 2

            async def interrupted_chunks():
                self.video_ready.set()
                await self.release_first_chunk.wait()
                yield self.video[:midpoint]
                await asyncio.Event().wait()

            return RemoteArtifact(
                "video/mp4",
                len(self.video),
                interrupted_chunks(),
                resume_validator='"video-v1"',
            )

        self.resume_requests.append(resume)
        offset = resume.offset if resume is not None else 0

        async def resumed_chunks():
            yield self.video[offset:]

        return RemoteArtifact(
            "video/mp4",
            len(self.video),
            resumed_chunks(),
            resume_offset=offset,
            resume_validator='"video-v1"',
        )

    async def open_cover(self, cdn_mirror_urls: tuple[str, ...]) -> RemoteArtifact:
        del cdn_mirror_urls

        async def chunks():
            yield self.cover

        return RemoteArtifact("image/png", len(self.cover), chunks())


@pytest.mark.asyncio
async def test_restart_marks_paused_work_interrupted_without_remote_access(
    tmp_path: Path,
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    database = tmp_path / "archive.db"
    media = PausableMediaAccess()
    archive = ManagedArchive(
        database_path=database,
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
    pausing = asyncio.create_task(archive.pause_task(operation_id))
    media.release_first_chunk.set()
    paused = await pausing
    assert paused.task.lifecycle == "paused"

    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    await asyncio.sleep(0)

    restarted = ManagedArchive(
        database_path=database,
        work_access=UnexpectedWorkAccess(),
        media_access=UnexpectedMediaAccess(),
    )
    interrupted = restarted.list_task_operations()[0]

    assert interrupted.task.lifecycle == "interrupted"
    assert interrupted.task.phase == "idle"
    assert interrupted.source_tasks[0].task.lifecycle == "interrupted"
    assert interrupted.source_tasks[0].work_tasks[0].task.lifecycle == "interrupted"
    assert list(root.rglob("*.part"))


@pytest.mark.asyncio
async def test_active_archive_can_be_discovered_and_paused_as_a_group(
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

    assert archive.has_active_tasks() is True
    pausing = asyncio.create_task(archive.pause_all())
    media.release_first_chunk.set()
    await pausing

    operation = archive.list_task_operations()[0]
    assert operation.task.lifecycle == "paused"
    assert operation.source_tasks[0].task.lifecycle == "paused"
    assert operation.source_tasks[0].work_tasks[0].task.lifecycle == "paused"
    assert archive.has_active_tasks() is True

    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running


@pytest.mark.asyncio
async def test_stop_and_exit_interrupts_active_archive_and_retains_parts(
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

    interrupting = asyncio.create_task(archive.interrupt_all())
    media.release_first_chunk.set()
    await interrupting
    with pytest.raises(AppError) as stopped:
        await running

    assert stopped.value.code == "TASK_INTERRUPTED"
    operation = archive.list_task_operations()[0]
    assert operation.task.lifecycle == "interrupted"
    assert operation.task.phase == "idle"
    assert operation.task.result == "none"
    assert operation.source_tasks[0].task.lifecycle == "interrupted"
    assert operation.source_tasks[0].work_tasks[0].task.lifecycle == "interrupted"
    assert archive.has_active_tasks() is False
    assert list(root.rglob("*.part"))


@pytest.mark.asyncio
async def test_stop_persists_interruption_before_active_chunk_reaches_checkpoint(
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

    interrupting = asyncio.create_task(archive.interrupt_all())
    await asyncio.sleep(0)

    operation = archive.list_task_operations()[0]
    assert operation.task.lifecycle == "interrupted"
    assert operation.source_tasks[0].task.lifecycle == "interrupted"
    assert operation.source_tasks[0].work_tasks[0].task.lifecycle == "interrupted"
    assert interrupting.done() is False

    media.release_first_chunk.set()
    await interrupting
    with pytest.raises(AppError) as stopped:
        await running
    assert stopped.value.code == "TASK_INTERRUPTED"


@pytest.mark.asyncio
async def test_process_crash_after_durable_chunk_requires_explicit_continue(
    tmp_path: Path,
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    database = tmp_path / "archive.db"
    payload = valid_mp4()
    crashing_media = FaultInjectedResumableMediaAccess(
        payload,
        crash_after_first_chunk=True,
    )
    interrupted = ManagedArchive(
        database_path=database,
        work_access=StaticWorkAccess(),
        media_access=crashing_media,
    )

    with pytest.raises(SimulatedProcessCrash):
        await interrupted.archive_single(
            SingleArchiveRequest("7429378937383308594", root)
        )

    assert list(root.rglob("*.part"))
    assert list(root.rglob("*.resume.json"))
    assert list(root.rglob("*.mp4")) == []

    resumed_media = FaultInjectedResumableMediaAccess(
        payload,
        crash_after_first_chunk=False,
    )
    restarted = ManagedArchive(
        database_path=database,
        work_access=StaticWorkAccess(),
        media_access=resumed_media,
    )
    operation = restarted.list_task_operations()[0]
    work = operation.source_tasks[0].work_tasks[0]
    assert operation.task.lifecycle == "interrupted"
    assert work.task.lifecycle == "interrupted"
    assert resumed_media.resume_requests == []

    continued = await restarted.resume_task(work.task.task_id)

    assert continued.task.result == "success"
    assert len(resumed_media.resume_requests) == 1
    resume = resumed_media.resume_requests[0]
    assert resume is not None
    assert resume.offset == len(payload) // 2
    archived = restarted.get_work_archive("7429378937383308594")
    assert archived is not None
    video_path = root / archived.relative_directory / "7429378937383308594.mp4"
    assert video_path.read_bytes() == payload
    assert list(root.rglob("*.part")) == []
    assert list(root.rglob("*.resume.json")) == []


@pytest.mark.asyncio
async def test_user_continues_interrupted_work_with_original_task_and_settings(
    tmp_path: Path,
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    database = tmp_path / "archive.db"
    media = PausableMediaAccess()
    archive = ManagedArchive(
        database_path=database,
        work_access=StaticWorkAccess(),
        media_access=media,
    )
    running = asyncio.create_task(
        archive.archive_single(
            SingleArchiveRequest(
                "7429378937383308594",
                root,
                naming_template=NamingTemplate("continued-{aweme_id}"),
                retry_limit=0,
            )
        )
    )
    await media.video_ready.wait()
    before_restart = archive.list_task_operations()[0]
    work_id = before_restart.source_tasks[0].work_tasks[0].task.task_id
    pausing = asyncio.create_task(archive.pause_task(work_id))
    media.release_first_chunk.set()
    await pausing
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    await asyncio.sleep(0)

    work_access = RecordingWorkAccess()
    restarted = ManagedArchive(
        database_path=database,
        work_access=work_access,
        media_access=StaticMediaAccess(media.video),
    )
    continued = await restarted.resume_task(work_id)

    assert continued.task.task_id == before_restart.task.task_id
    assert continued.task.lifecycle == "finished"
    assert continued.task.result == "success"
    assert work_access.requests == ["7429378937383308594"]
    archived = restarted.get_work_archive("7429378937383308594")
    assert archived is not None
    work_directory = root / archived.relative_directory
    assert (work_directory / "continued-7429378937383308594.mp4").is_file()
    assert list(root.rglob("*.part")) == []


@pytest.mark.asyncio
async def test_continue_uses_range_only_with_matching_persisted_provenance(
    tmp_path: Path,
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    database = tmp_path / "archive.db"
    media = ResumablePausableMediaAccess()
    archive = ManagedArchive(
        database_path=database,
        work_access=StaticWorkAccess(),
        media_access=media,
    )
    running = asyncio.create_task(
        archive.archive_single(
            SingleArchiveRequest("7429378937383308594", root)
        )
    )
    await media.video_ready.wait()
    work_id = (
        archive.list_task_operations()[0]
        .source_tasks[0]
        .work_tasks[0]
        .task.task_id
    )
    pausing = asyncio.create_task(archive.pause_task(work_id))
    media.release_first_chunk.set()
    await pausing
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    await asyncio.sleep(0)

    restarted = ManagedArchive(
        database_path=database,
        work_access=StaticWorkAccess(),
        media_access=media,
    )
    continued = await restarted.resume_task(work_id)

    request = media.resume_requests[0]
    assert request is not None
    assert request.offset == len(media.video) // 2
    assert request.total_size == len(media.video)
    assert request.validator == '"video-v1"'
    assert continued.task.result == "success"
    archived = restarted.get_work_archive("7429378937383308594")
    assert archived is not None
    video_path = root / archived.relative_directory / "7429378937383308594.mp4"
    assert video_path.read_bytes() == media.video
    assert list(root.rglob("*.part")) == []
    assert list(root.rglob("*.resume.json")) == []


@pytest.mark.asyncio
async def test_continue_restarts_when_retained_part_was_modified(tmp_path: Path) -> None:
    root = tmp_path / "library"
    root.mkdir()
    database = tmp_path / "archive.db"
    media = ResumablePausableMediaAccess()
    archive = ManagedArchive(
        database_path=database,
        work_access=StaticWorkAccess(),
        media_access=media,
    )
    running = asyncio.create_task(
        archive.archive_single(
            SingleArchiveRequest("7429378937383308594", root)
        )
    )
    await media.video_ready.wait()
    work_id = (
        archive.list_task_operations()[0]
        .source_tasks[0]
        .work_tasks[0]
        .task.task_id
    )
    pausing = asyncio.create_task(archive.pause_task(work_id))
    media.release_first_chunk.set()
    await pausing
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    await asyncio.sleep(0)
    part_path = next(root.rglob("*.mp4.part"))
    damaged = bytearray(part_path.read_bytes())
    damaged[0] ^= 0xFF
    part_path.write_bytes(damaged)

    restarted = ManagedArchive(
        database_path=database,
        work_access=StaticWorkAccess(),
        media_access=media,
    )
    continued = await restarted.resume_task(work_id)

    assert media.resume_requests == [None]
    assert continued.task.result == "success"
    archived = restarted.get_work_archive("7429378937383308594")
    assert archived is not None
    video_path = root / archived.relative_directory / "7429378937383308594.mp4"
    assert video_path.read_bytes() == media.video


@pytest.mark.asyncio
async def test_transient_failures_retry_with_operation_limit_and_re_resolve(
    tmp_path: Path,
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    work_access = FlakyWorkAccess(failures=2)
    delay = RecordingRetryDelay()
    archive = ManagedArchive(
        database_path=tmp_path / "archive.db",
        work_access=work_access,
        media_access=StaticMediaAccess(PausableMediaAccess().video),
        retry_delay=delay,
    )

    completed = await archive.archive_single(
        SingleArchiveRequest(
            "7429378937383308594",
            root,
            retry_limit=3,
        )
    )

    assert completed.operation.result == "success"
    assert work_access.requests == ["7429378937383308594"] * 3
    assert delay.attempts == [1, 2]


@pytest.mark.asyncio
async def test_transient_media_failures_re_resolve_before_retry(tmp_path: Path) -> None:
    root = tmp_path / "library"
    root.mkdir()
    work_access = RecordingWorkAccess()
    media = FlakyMediaAccess(PausableMediaAccess().video, failures=2)
    delay = RecordingRetryDelay()
    archive = ManagedArchive(
        database_path=tmp_path / "archive.db",
        work_access=work_access,
        media_access=media,
        retry_delay=delay,
    )

    completed = await archive.archive_single(
        SingleArchiveRequest(
            "7429378937383308594",
            root,
            retry_limit=2,
        )
    )

    assert completed.operation.result == "success"
    assert work_access.requests == ["7429378937383308594"] * 3
    assert media.video_attempts == 3
    assert delay.attempts == [1, 2]


@pytest.mark.asyncio
async def test_retry_limit_allows_exactly_three_retries(tmp_path: Path) -> None:
    root = tmp_path / "library"
    root.mkdir()
    work_access = RecordingWorkAccess()
    media = FlakyMediaAccess(PausableMediaAccess().video, failures=4)
    delay = RecordingRetryDelay()
    archive = ManagedArchive(
        database_path=tmp_path / "archive.db",
        work_access=work_access,
        media_access=media,
        retry_delay=delay,
    )

    with pytest.raises(AppError) as failure:
        await archive.archive_single(
            SingleArchiveRequest(
                "7429378937383308594",
                root,
                retry_limit=3,
            )
        )

    assert failure.value.code == "UPSTREAM_BLOCKED"
    assert work_access.requests == ["7429378937383308594"] * 4
    assert media.video_attempts == 4
    assert delay.attempts == [1, 2, 3]
    failed = archive.list_task_operations()[0].source_tasks[0].work_tasks[0].task
    assert failed.result == "failed"
    assert failed.error is not None
    assert failed.error.code == "UPSTREAM_BLOCKED"


@pytest.mark.asyncio
async def test_deterministic_media_failure_is_not_automatically_retried(
    tmp_path: Path,
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    work_access = RecordingWorkAccess()
    media = DeterministicMediaAccess(PausableMediaAccess().video)
    delay = RecordingRetryDelay()
    archive = ManagedArchive(
        database_path=tmp_path / "archive.db",
        work_access=work_access,
        media_access=media,
        retry_delay=delay,
    )

    with pytest.raises(AppError) as failure:
        await archive.archive_single(
            SingleArchiveRequest(
                "7429378937383308594",
                root,
                retry_limit=3,
            )
        )

    assert failure.value.code == "DOWNLOAD_FAILED"
    assert work_access.requests == ["7429378937383308594"]
    assert media.video_attempts == 1
    assert delay.attempts == []


@pytest.mark.asyncio
async def test_unclassified_timeout_is_not_automatically_retried(tmp_path: Path) -> None:
    root = tmp_path / "library"
    root.mkdir()
    work_access = RecordingWorkAccess()
    media = UnclassifiedTimeoutMediaAccess(PausableMediaAccess().video)
    delay = RecordingRetryDelay()
    archive = ManagedArchive(
        database_path=tmp_path / "archive.db",
        work_access=work_access,
        media_access=media,
        retry_delay=delay,
    )

    with pytest.raises(TimeoutError):
        await archive.archive_single(
            SingleArchiveRequest(
                "7429378937383308594",
                root,
                retry_limit=3,
            )
        )

    assert work_access.requests == ["7429378937383308594"]
    assert media.video_attempts == 1
    assert delay.attempts == []


@pytest.mark.asyncio
async def test_user_retries_failed_work_without_creating_a_second_task(
    tmp_path: Path,
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    work_access = UserRetryWorkAccess()
    delay = RecordingRetryDelay()
    archive = ManagedArchive(
        database_path=tmp_path / "archive.db",
        work_access=work_access,
        media_access=StaticMediaAccess(PausableMediaAccess().video),
        retry_delay=delay,
    )
    with pytest.raises(AppError) as failure:
        await archive.archive_single(
            SingleArchiveRequest(
                "7429378937383308594",
                root,
                retry_limit=3,
            )
        )
    assert failure.value.code == "VIDEO_NOT_FOUND"
    failed = archive.list_task_operations()[0]
    work_id = failed.source_tasks[0].work_tasks[0].task.task_id

    retried = await archive.retry_task(work_id)

    assert retried.task.task_id == failed.task.task_id
    assert retried.task.lifecycle == "finished"
    assert retried.task.result == "success"
    assert work_access.requests == ["7429378937383308594"] * 2
    assert delay.attempts == []
    assert len(archive.list_task_operations()) == 1


@pytest.mark.asyncio
async def test_retry_repairs_only_failed_artifact_and_preserves_completed_video(
    tmp_path: Path,
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    database = tmp_path / "archive.db"
    initial = ManagedArchive(
        database_path=database,
        work_access=StaticWorkAccess(),
        media_access=StaticMediaAccess(valid_mp4()),
    )
    completed = await initial.archive_single(
        SingleArchiveRequest("7429378937383308594", root)
    )
    work_directory = root / completed.archive_item.relative_directory
    video_path = work_directory / "7429378937383308594.mp4"
    cover_path = work_directory / "7429378937383308594.cover.png"
    video_hash = hashlib.sha256(video_path.read_bytes()).hexdigest()
    cover_path.unlink()

    media = RepairRetryMediaAccess(valid_mp4())
    archive = ManagedArchive(
        database_path=database,
        work_access=StaticWorkAccess(),
        media_access=media,
    )
    with pytest.raises(AppError) as failure:
        await archive.archive_single(
            SingleArchiveRequest(
                "7429378937383308594",
                root,
                retry_limit=3,
            )
        )
    assert failure.value.code == "DOWNLOAD_FAILED"
    failed_operation = next(
        operation
        for operation in archive.list_task_operations()
        if operation.task.result == "failed"
    )
    failed_work_id = failed_operation.source_tasks[0].work_tasks[0].task.task_id

    media.fail_cover = False
    retried = await archive.retry_task(failed_work_id)

    assert retried.task.task_id == failed_operation.task.task_id
    assert retried.task.result == "success"
    assert media.video_attempts == 0
    assert media.cover_attempts == 2
    assert hashlib.sha256(video_path.read_bytes()).hexdigest() == video_hash
    assert cover_path.is_file()
    operations = archive.list_task_operations()
    assert len(operations) == 2
    assert sum(operation.task.result == "success" for operation in operations) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_kind", ["network", "rate_limit", "server"])
async def test_http_transient_failures_use_operation_retry_budget(
    tmp_path: Path,
    failure_kind: str,
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    work_access = RecordingWorkAccess()
    delay = RecordingRetryDelay()
    video = PausableMediaAccess().video
    cover = PausableMediaAccess().cover
    video_requests = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal video_requests
        if request.url.host is not None and request.url.host.endswith("douyinvod.com"):
            video_requests += 1
            if video_requests <= 4:
                if failure_kind == "network":
                    raise httpx.ConnectError("temporary connection failure", request=request)
                status = 429 if failure_kind == "rate_limit" else 503
                return httpx.Response(status, request=request)
            return httpx.Response(
                200,
                headers={
                    "content-type": "video/mp4",
                    "content-length": str(len(video)),
                    "etag": '"video-v1"',
                },
                content=video,
                request=request,
            )
        return httpx.Response(
            200,
            headers={
                "content-type": "image/png",
                "content-length": str(len(cover)),
            },
            content=cover,
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        archive = ManagedArchive(
            database_path=tmp_path / "archive.db",
            work_access=work_access,
            media_access=HttpMediaAccess(client),
            retry_delay=delay,
        )
        completed = await archive.archive_single(
            SingleArchiveRequest(
                "7429378937383308594",
                root,
                retry_limit=2,
            )
        )

    assert completed.operation.result == "success"
    assert work_access.requests == ["7429378937383308594"] * 3
    assert video_requests == 5
    assert delay.attempts == [1, 2]


@pytest.mark.asyncio
async def test_http_stream_interruption_is_re_resolved_and_retried(tmp_path: Path) -> None:
    root = tmp_path / "library"
    root.mkdir()
    work_access = RecordingWorkAccess()
    delay = RecordingRetryDelay()
    video = PausableMediaAccess().video
    cover = PausableMediaAccess().cover
    video_requests = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal video_requests
        if request.url.host is not None and request.url.host.endswith("douyinvod.com"):
            video_requests += 1
            if video_requests == 1:

                class InterruptedStream(httpx.AsyncByteStream):
                    async def __aiter__(self):
                        yield video[: len(video) // 2]
                        raise httpx.ReadError(
                            "temporary stream interruption",
                            request=request,
                        )

                return httpx.Response(
                    200,
                    headers={
                        "content-type": "video/mp4",
                        "content-length": str(len(video)),
                        "etag": '"video-v1"',
                    },
                    stream=InterruptedStream(),
                    request=request,
                )
            return httpx.Response(
                200,
                headers={
                    "content-type": "video/mp4",
                    "content-length": str(len(video)),
                    "etag": '"video-v1"',
                },
                content=video,
                request=request,
            )
        return httpx.Response(
            200,
            headers={"content-type": "image/png", "content-length": str(len(cover))},
            content=cover,
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        archive = ManagedArchive(
            database_path=tmp_path / "archive.db",
            work_access=work_access,
            media_access=HttpMediaAccess(client),
            retry_delay=delay,
        )
        completed = await archive.archive_single(
            SingleArchiveRequest(
                "7429378937383308594",
                root,
                retry_limit=1,
            )
        )

    assert completed.operation.result == "success"
    assert work_access.requests == ["7429378937383308594"] * 2
    assert video_requests == 2
    assert delay.attempts == [1]
