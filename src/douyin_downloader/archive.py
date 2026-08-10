from __future__ import annotations

import asyncio
import math
import threading
import time
import weakref
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from douyin_downloader.archive_adapters import (
    AtomicFilePromoter,
    FilePromoter,
    FolderOpener,
    HttpMediaAccess,
    MediaAccess,
    RemoteArtifact,
    WindowsDirectoryChooser,
    WindowsFolderOpener,
)
from douyin_downloader.archive_artifacts import ArtifactRecord
from douyin_downloader.archive_paths import pin_work_directory, work_directory
from douyin_downloader.archive_pipeline import (
    ArchiveArtifactPipeline,
    PreparedArchive,
)
from douyin_downloader.archive_store import (
    ArchiveStore,
    StoredArchive,
    StoredTask,
    StoredTaskOperation,
    TaskIds,
)
from douyin_downloader.async_tools import run_in_thread_cancellation_safe
from douyin_downloader.audio_artifacts import (
    AudioArtifactTool,
    FfmpegAudioArtifactTool,
)
from douyin_downloader.domain import AppError, ResolvedWork
from douyin_downloader.resources import ffmpeg_executable_path, ffprobe_executable_path
from douyin_downloader.settings import (
    ArchiveProfile,
    NamingTemplate,
    OperationSettingsSnapshot,
)

__all__ = [
    "ArchiveItemSnapshot",
    "ArchiveOperationSnapshot",
    "TaskCenterOperationSnapshot",
    "TaskCenterSourceSnapshot",
    "TaskCenterWorkSnapshot",
    "TaskErrorSnapshot",
    "TaskProgressSnapshot",
    "HttpMediaAccess",
    "ManagedArchive",
    "RemoteArtifact",
    "SingleArchiveRequest",
    "TaskSnapshot",
    "WindowsDirectoryChooser",
]


@dataclass(frozen=True, slots=True)
class SingleArchiveRequest:
    aweme_id: str
    archive_root: Path
    naming_template: NamingTemplate = field(default_factory=NamingTemplate)
    profile: ArchiveProfile = field(default_factory=ArchiveProfile)
    download_concurrency: int = 3
    retry_limit: int = 3

    @classmethod
    def from_settings(
        cls,
        aweme_id: str,
        settings: OperationSettingsSnapshot,
    ) -> SingleArchiveRequest:
        return cls(
            aweme_id=aweme_id,
            archive_root=settings.archive_root,
            naming_template=settings.naming_template,
            profile=settings.profile,
            download_concurrency=settings.download_concurrency,
            retry_limit=settings.retry_limit,
        )

    def settings_snapshot(self) -> OperationSettingsSnapshot:
        return OperationSettingsSnapshot(
            archive_root=self.archive_root,
            naming_template=self.naming_template,
            profile=self.profile,
            download_concurrency=self.download_concurrency,
            retry_limit=self.retry_limit,
        )


@dataclass(frozen=True, slots=True)
class TaskErrorSnapshot:
    code: str
    message: str
    suggestion: str


@dataclass(frozen=True, slots=True)
class TaskProgressSnapshot:
    completed_items: int = 0
    total_items: int = 1
    completed_bytes: int = 0
    total_bytes: int | None = None
    percentage: float | None = None
    speed_bytes_per_second: float | None = None
    eta_seconds: int | None = None


@dataclass(frozen=True, slots=True)
class TaskSnapshot:
    task_id: str
    lifecycle: str
    phase: str
    result: str
    error: TaskErrorSnapshot | None = None
    progress: TaskProgressSnapshot = field(default_factory=TaskProgressSnapshot)


@dataclass(frozen=True, slots=True)
class TaskCenterWorkSnapshot:
    task: TaskSnapshot
    aweme_id: str


@dataclass(frozen=True, slots=True)
class TaskCenterSourceSnapshot:
    task: TaskSnapshot
    work_tasks: tuple[TaskCenterWorkSnapshot, ...]


@dataclass(frozen=True, slots=True)
class TaskCenterOperationSnapshot:
    task: TaskSnapshot
    source_tasks: tuple[TaskCenterSourceSnapshot, ...]


@dataclass(frozen=True, slots=True)
class ArchiveItemSnapshot:
    aweme_id: str
    status: str
    relative_directory: Path
    audio_outcome: str = "not_requested"
    description_outcome: str = "not_requested"


@dataclass(frozen=True, slots=True)
class ArchiveOperationSnapshot:
    operation: TaskSnapshot
    source_task: TaskSnapshot
    work_task: TaskSnapshot
    archive_item: ArchiveItemSnapshot
    settings: OperationSettingsSnapshot


@dataclass(frozen=True, slots=True)
class _AuditedArchive:
    stored: StoredArchive
    status: str
    valid_artifacts: dict[str, ArtifactRecord]


class _TaskProgressRecorder:
    def __init__(
        self,
        store: ArchiveStore,
        ids: TaskIds,
        remote_artifact_count: int,
    ) -> None:
        self._store = store
        self._ids = ids
        self._remaining_remote_artifacts = remote_artifact_count
        self._known_total = 0
        self._all_sizes_known = True
        self._completed_bytes = 0
        self._phase = "resolving"
        self._first_byte_at: float | None = None
        self._sample_count = 0
        self._speed_bytes_per_second: float | None = None
        self._eta_seconds: int | None = None

    async def set_phase(self, phase: str) -> None:
        self._phase = phase
        await self._persist()

    async def remote_started(self, expected_size: int | None) -> None:
        self._remaining_remote_artifacts -= 1
        if expected_size is None:
            self._all_sizes_known = False
        else:
            self._known_total += expected_size
        await self._persist()

    async def advance_bytes(self, count: int) -> None:
        self._completed_bytes += count
        now = time.monotonic()
        if self._first_byte_at is None:
            self._first_byte_at = now
        self._sample_count += 1
        elapsed = now - self._first_byte_at
        total_bytes = self._reliable_total()
        if self._sample_count >= 2 and elapsed >= 0.25:
            self._speed_bytes_per_second = self._completed_bytes / elapsed
            self._eta_seconds = (
                math.ceil(
                    max(total_bytes - self._completed_bytes, 0)
                    / self._speed_bytes_per_second
                )
                if total_bytes is not None and self._speed_bytes_per_second > 0
                else None
            )
        await self._persist()

    async def _persist(self) -> None:
        total_bytes = self._reliable_total()
        await asyncio.to_thread(
            self._store.update_progress,
            self._ids,
            phase=self._phase,
            completed_bytes=self._completed_bytes,
            total_bytes=total_bytes,
            speed_bytes_per_second=self._speed_bytes_per_second,
            eta_seconds=self._eta_seconds,
        )

    def _reliable_total(self) -> int | None:
        return (
            self._known_total
            if self._remaining_remote_artifacts == 0 and self._all_sizes_known
            else None
        )


class WorkAccess(Protocol):
    async def fetch_work(self, aweme_id: str) -> ResolvedWork: ...


class ManagedArchive:
    def __init__(
        self,
        *,
        database_path: Path,
        work_access: WorkAccess,
        media_access: MediaAccess,
        audio_tool: AudioArtifactTool | None = None,
        folder_opener: FolderOpener | None = None,
        file_promoter: FilePromoter | None = None,
    ) -> None:
        self._store = ArchiveStore(database_path)
        self._work_access = work_access
        self._artifact_pipeline = ArchiveArtifactPipeline(
            media_access,
            audio_tool
            or FfmpegAudioArtifactTool(
                ffmpeg_executable_path(),
                ffprobe_executable_path(),
            ),
        )
        self._folder_opener = folder_opener or WindowsFolderOpener()
        self._file_promoter = file_promoter or AtomicFilePromoter()
        self._integrity_locks: weakref.WeakValueDictionary[
            str,
            threading.Lock,
        ] = weakref.WeakValueDictionary()
        self._integrity_locks_guard = threading.Lock()

    async def archive_single(
        self,
        request: SingleArchiveRequest,
    ) -> ArchiveOperationSnapshot:
        root = request.archive_root
        if not root.is_absolute() or not root.is_dir():
            raise AppError(
                "ARCHIVE_ROOT_INVALID",
                "请选择有效的本地归档目录。",
                400,
            )
        root = root.resolve(strict=True)
        if not request.aweme_id.isdigit():
            raise AppError("INVALID_INPUT", "作品标识无效。", 400)
        settings = request.settings_snapshot()
        settings = OperationSettingsSnapshot(
            archive_root=root,
            naming_template=settings.naming_template,
            profile=settings.profile,
            download_concurrency=settings.download_concurrency,
            retry_limit=settings.retry_limit,
        )
        async with _hold_thread_lock(self._integrity_lock(request.aweme_id)):
            return await self._archive_single_locked(request.aweme_id, settings)

    async def _archive_single_locked(
        self,
        aweme_id: str,
        settings: OperationSettingsSnapshot,
    ) -> ArchiveOperationSnapshot:
        root = settings.archive_root
        existing = await run_in_thread_cancellation_safe(
            self._audit_archive_unlocked,
            aweme_id,
        )
        valid_artifacts: dict[str, ArtifactRecord] = {}
        existing_relative_directory: Path | None = None
        if existing is not None:
            effective_profile = ArchiveProfile(
                include_audio=(
                    existing.stored.settings.profile.include_audio
                    or settings.profile.include_audio
                ),
                include_description=(
                    existing.stored.settings.profile.include_description
                    or settings.profile.include_description
                ),
            )
            if (
                existing.status == "archived"
                and effective_profile == existing.stored.settings.profile
            ):
                return _archive_snapshot(
                    existing.stored.ids,
                    aweme_id,
                    existing.stored.relative_directory,
                    "archived",
                    existing.stored.settings,
                    artifacts=existing.stored.artifacts,
                )
            if existing.status == "location_unavailable":
                raise AppError(
                    "ARCHIVE_LOCATION_UNAVAILABLE",
                    "归档位置当前不可用。",
                    409,
                )
            root = existing.stored.root
            settings = replace(
                settings,
                archive_root=existing.stored.root,
                naming_template=existing.stored.settings.naming_template,
                profile=effective_profile,
            )
            existing_relative_directory = existing.stored.relative_directory
            valid_artifacts = existing.valid_artifacts

        ids = TaskIds(uuid4().hex, uuid4().hex, uuid4().hex)
        prepared: PreparedArchive | None = None
        promotion_started = False
        operation_result = "success"
        try:
            self._store.create_running(ids, aweme_id, settings)
            progress = _TaskProgressRecorder(
                self._store,
                ids,
                int("video" not in valid_artifacts)
                + int("cover" not in valid_artifacts),
            )
            resolved = await self._work_access.fetch_work(aweme_id)
            if resolved.snapshot.aweme_id != aweme_id:
                raise AppError(
                    "UPSTREAM_BLOCKED",
                    "解析服务暂时不可用，请稍后重试。",
                    502,
                )
            relative_directory = (
                existing_relative_directory
                if existing_relative_directory is not None
                else work_directory(resolved)
            )
            with pin_work_directory(
                root,
                relative_directory,
                create=True,
            ) as output_directory:
                base_name = settings.artifact_base_name(resolved)
                registered_artifacts: dict[str, ArtifactRecord] = (
                    {artifact.kind: artifact for artifact in existing.stored.artifacts}
                    if existing is not None
                    else {}
                )
                prepared = await self._artifact_pipeline.prepare(
                    output_directory,
                    aweme_id,
                    ids.operation,
                    resolved,
                    valid_artifacts,
                    registered_artifacts,
                    base_name,
                    settings.profile,
                    progress,
                )
                operation_result = prepared.result
                await progress.set_phase("promoting")
                self._store.prepare_promotion(
                    ids,
                    aweme_id,
                    root,
                    relative_directory,
                    prepared.artifacts,
                )
                promotion_started = True
                for part_path, final_path in prepared.promotions:
                    self._file_promoter.promote(part_path, final_path)
                self._store.finish_promotion(ids, aweme_id, operation_result)
        except Exception as error:
            if not promotion_started:
                try:
                    if prepared is not None:
                        prepared.discard_parts()
                finally:
                    self._store.fail(ids, _task_error_code(error))
            raise

        return _archive_snapshot(
            ids,
            aweme_id,
            relative_directory,
            "archived" if operation_result == "success" else "needs_repair",
            settings,
            result=operation_result,
            artifacts=prepared.artifacts if prepared is not None else (),
        )

    def get_work_archive(self, aweme_id: str) -> ArchiveItemSnapshot | None:
        existing = self._audit_archive(aweme_id)
        if existing is None:
            return None
        return ArchiveItemSnapshot(
            aweme_id=aweme_id,
            status=existing.status,
            relative_directory=existing.stored.relative_directory,
            audio_outcome=_artifact_outcome(
                existing.stored.settings.profile.include_audio,
                existing.stored.artifacts,
                "audio",
            ),
            description_outcome=_artifact_outcome(
                existing.stored.settings.profile.include_description,
                existing.stored.artifacts,
                "description",
            ),
        )

    def list_task_operations(self) -> tuple[TaskCenterOperationSnapshot, ...]:
        return tuple(
            _task_center_snapshot(operation)
            for operation in self._store.list_task_operations()
        )

    def clear_task_operation(self, operation_id: str) -> None:
        outcome = self._store.clear_task_operation(operation_id)
        if outcome == "not_found":
            raise AppError("TASK_NOT_FOUND", "没有找到该任务记录。", 404)
        if outcome == "active":
            raise AppError(
                "TASK_HISTORY_ACTIVE",
                "活动、暂停或已中断的任务不能清理。",
                409,
            )

    def open_work_folder(self, aweme_id: str) -> None:
        existing = self._audit_archive(aweme_id)
        if existing is None:
            raise AppError("ARCHIVE_NOT_FOUND", "没有找到该作品的本地归档。", 404)
        if existing.status == "location_unavailable":
            raise AppError(
                "ARCHIVE_LOCATION_UNAVAILABLE",
                "归档位置当前不可用。",
                409,
            )
        try:
            pinned_directory = pin_work_directory(
                existing.stored.root,
                existing.stored.relative_directory,
                create=False,
            )
        except AppError as error:
            raise AppError(
                "ARCHIVE_LOCATION_UNAVAILABLE",
                "归档位置当前不可用。",
                409,
            ) from error
        try:
            with pinned_directory as output_directory:
                self._folder_opener.open_folder(output_directory)
        except OSError as error:
            raise AppError("ARCHIVE_OPEN_FAILED", "无法打开归档文件夹。", 500) from error

    def _audit_archive(self, aweme_id: str) -> _AuditedArchive | None:
        with self._integrity_lock(aweme_id):
            return self._audit_archive_unlocked(aweme_id)

    def _audit_archive_unlocked(self, aweme_id: str) -> _AuditedArchive | None:
        self._recover_promotions(aweme_id)
        stored = self._store.load_archive(aweme_id)
        if stored is None:
            return None
        try:
            with pin_work_directory(
                stored.root,
                stored.relative_directory,
                create=False,
            ) as output_directory:
                valid_artifacts = self._artifact_pipeline.audit(
                    output_directory,
                    stored.aweme_id,
                    stored.artifacts,
                )
        except AppError:
            expected_directory = stored.root / stored.relative_directory
            if stored.root.is_dir() and not expected_directory.exists():
                if stored.status != "needs_repair":
                    self._store.set_archive_status(aweme_id, "needs_repair")
                return _AuditedArchive(stored, "needs_repair", {})
            return _AuditedArchive(stored, "location_unavailable", {})

        expected_artifacts = {"video", "cover", "metadata"}
        if stored.settings.profile.include_audio:
            expected_artifacts.add("audio")
        if stored.settings.profile.include_description:
            expected_artifacts.add("description")
        status = "archived" if set(valid_artifacts) == expected_artifacts else "needs_repair"
        if stored.status != status:
            self._store.set_archive_status(aweme_id, status)
        return _AuditedArchive(stored, status, valid_artifacts)

    def _integrity_lock(self, aweme_id: str) -> threading.Lock:
        with self._integrity_locks_guard:
            return self._integrity_locks.setdefault(aweme_id, threading.Lock())

    def _recover_promotions(self, aweme_id: str) -> None:
        for promotion in self._store.pending_promotions():
            if promotion.aweme_id != aweme_id:
                continue
            try:
                with pin_work_directory(
                    promotion.root,
                    promotion.relative_directory,
                    create=False,
                ) as output_directory:
                    result = self._artifact_pipeline.recover(
                        output_directory,
                        promotion.aweme_id,
                        promotion.artifacts,
                    )
                    self._store.finish_promotion(
                        promotion.ids,
                        promotion.aweme_id,
                        result,
                    )
            except Exception:
                self._store.discard_promotion(promotion)


def _archive_snapshot(
    ids: TaskIds,
    aweme_id: str,
    relative_directory: Path,
    status: str,
    settings: OperationSettingsSnapshot,
    *,
    result: str = "success",
    artifacts: tuple[ArtifactRecord, ...] = (),
) -> ArchiveOperationSnapshot:
    def task(task_id: str) -> TaskSnapshot:
        return TaskSnapshot(task_id, "finished", "idle", result)

    return ArchiveOperationSnapshot(
        operation=task(ids.operation),
        source_task=task(ids.source),
        work_task=task(ids.work),
        archive_item=ArchiveItemSnapshot(
            aweme_id,
            status,
            relative_directory,
            _artifact_outcome(settings.profile.include_audio, artifacts, "audio"),
            _artifact_outcome(
                settings.profile.include_description,
                artifacts,
                "description",
            ),
        ),
        settings=settings,
    )


def _task_center_snapshot(
    operation: StoredTaskOperation,
) -> TaskCenterOperationSnapshot:
    def task(
        stored: StoredTask,
        *,
        completed_items: int,
        total_items: int,
    ) -> TaskSnapshot:
        return TaskSnapshot(
            stored.task_id,
            stored.lifecycle,
            stored.phase,
            stored.result,
            _task_error(stored.error_code),
            TaskProgressSnapshot(
                completed_items=completed_items,
                total_items=total_items,
                completed_bytes=stored.completed_bytes,
                total_bytes=stored.total_bytes,
                speed_bytes_per_second=stored.speed_bytes_per_second,
                eta_seconds=stored.eta_seconds,
                percentage=(
                    round(stored.completed_bytes / stored.total_bytes * 100, 1)
                    if stored.total_bytes is not None and stored.total_bytes > 0
                    else (
                        100.0
                        if total_items > 0 and completed_items == total_items
                        else None
                    )
                ),
            ),
        )

    source_snapshots: list[TaskCenterSourceSnapshot] = []
    for source in operation.source_tasks:
        work_snapshots = tuple(
            TaskCenterWorkSnapshot(
                task=task(
                    work.task,
                    completed_items=int(work.task.lifecycle == "finished"),
                    total_items=1,
                ),
                aweme_id=work.aweme_id,
            )
            for work in source.work_tasks
        )
        source_snapshots.append(
            TaskCenterSourceSnapshot(
                task=task(
                    source.task,
                    completed_items=sum(
                        item.task.progress.completed_items for item in work_snapshots
                    ),
                    total_items=len(work_snapshots),
                ),
                work_tasks=work_snapshots,
            )
        )
    total_items = sum(source.task.progress.total_items for source in source_snapshots)
    completed_items = sum(
        source.task.progress.completed_items for source in source_snapshots
    )
    return TaskCenterOperationSnapshot(
        task=task(
            operation.task,
            completed_items=completed_items,
            total_items=total_items,
        ),
        source_tasks=tuple(source_snapshots),
    )


_TASK_ERRORS = {
    "UPSTREAM_BLOCKED": TaskErrorSnapshot(
        "UPSTREAM_BLOCKED",
        "解析服务暂时不可用。",
        "请稍后重试此归档操作。",
    ),
    "VIDEO_NOT_FOUND": TaskErrorSnapshot(
        "VIDEO_NOT_FOUND",
        "没有找到该作品。",
        "请确认作品仍公开可访问后重试。",
    ),
    "ARCHIVE_FAILED": TaskErrorSnapshot(
        "ARCHIVE_FAILED",
        "归档成果未能通过完整性检查。",
        "请检查磁盘空间和归档位置后重试。",
    ),
}


def _task_error_code(error: Exception) -> str:
    if isinstance(error, AppError) and error.code in _TASK_ERRORS:
        return error.code
    return "ARCHIVE_FAILED"


def _task_error(code: str | None) -> TaskErrorSnapshot | None:
    if code is None:
        return None
    return _TASK_ERRORS.get(code, _TASK_ERRORS["ARCHIVE_FAILED"])


def _artifact_outcome(
    requested: bool,
    artifacts: tuple[ArtifactRecord, ...],
    kind: str,
) -> str:
    if not requested:
        return "not_requested"
    artifact = next((item for item in artifacts if item.kind == kind), None)
    if artifact is None:
        return "missing"
    if artifact.status in {"archived", "promoting"}:
        return "ready"
    return artifact.status


@asynccontextmanager
async def _hold_thread_lock(lock: threading.Lock) -> AsyncIterator[None]:
    while not lock.acquire(blocking=False):  # noqa: ASYNC110
        # Polling is intentional: a background blocking acquire could outlive
        # cancellation and orphan the cross-thread lock.
        await asyncio.sleep(0.01)
    try:
        yield
    finally:
        lock.release()
