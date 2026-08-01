from __future__ import annotations

import asyncio
import threading
import weakref
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
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
    TaskIds,
)
from douyin_downloader.async_tools import run_in_thread_cancellation_safe
from douyin_downloader.domain import AppError, ResolvedWork
from douyin_downloader.settings import (
    ArchiveProfile,
    NamingTemplate,
    OperationSettingsSnapshot,
)

__all__ = [
    "ArchiveItemSnapshot",
    "ArchiveOperationSnapshot",
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
class TaskSnapshot:
    task_id: str
    lifecycle: str
    phase: str
    result: str


@dataclass(frozen=True, slots=True)
class ArchiveItemSnapshot:
    aweme_id: str
    status: str
    relative_directory: Path


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


class WorkAccess(Protocol):
    async def fetch_work(self, aweme_id: str) -> ResolvedWork: ...


class ManagedArchive:
    def __init__(
        self,
        *,
        database_path: Path,
        work_access: WorkAccess,
        media_access: MediaAccess,
        folder_opener: FolderOpener | None = None,
        file_promoter: FilePromoter | None = None,
    ) -> None:
        self._store = ArchiveStore(database_path)
        self._work_access = work_access
        self._artifact_pipeline = ArchiveArtifactPipeline(media_access)
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
        if settings.profile.include_audio or settings.profile.include_description:
            raise AppError(
                "ARCHIVE_PROFILE_UNAVAILABLE",
                "可选音轨与文案成果将在下一阶段启用，请暂时关闭后再归档。",
                409,
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
            if existing.status == "archived":
                return _archive_snapshot(
                    existing.stored.ids,
                    aweme_id,
                    existing.stored.relative_directory,
                    "archived",
                    existing.stored.settings,
                )
            if existing.status == "location_unavailable":
                raise AppError(
                    "ARCHIVE_LOCATION_UNAVAILABLE",
                    "归档位置当前不可用。",
                    409,
                )
            root = existing.stored.root
            settings = existing.stored.settings
            existing_relative_directory = existing.stored.relative_directory
            valid_artifacts = existing.valid_artifacts

        ids = TaskIds(uuid4().hex, uuid4().hex, uuid4().hex)
        prepared: PreparedArchive | None = None
        promotion_started = False
        try:
            self._store.create_running(ids, aweme_id, settings)
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
                )
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
                self._store.finish_promotion(ids, aweme_id)
        except Exception:
            if not promotion_started:
                try:
                    if prepared is not None:
                        prepared.discard_parts()
                finally:
                    self._store.fail(ids)
            raise

        return _archive_snapshot(
            ids,
            aweme_id,
            relative_directory,
            "archived",
            settings,
        )

    def get_work_archive(self, aweme_id: str) -> ArchiveItemSnapshot | None:
        existing = self._audit_archive(aweme_id)
        if existing is None:
            return None
        return ArchiveItemSnapshot(
            aweme_id=aweme_id,
            status=existing.status,
            relative_directory=existing.stored.relative_directory,
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

        status = (
            "archived"
            if set(valid_artifacts) == {"video", "cover", "metadata"}
            else "needs_repair"
        )
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
                    self._artifact_pipeline.recover(
                        output_directory,
                        promotion.aweme_id,
                        promotion.artifacts,
                    )
                    self._store.finish_promotion(
                        promotion.ids,
                        promotion.aweme_id,
                    )
            except Exception:
                self._store.discard_promotion(promotion)


def _archive_snapshot(
    ids: TaskIds,
    aweme_id: str,
    relative_directory: Path,
    status: str,
    settings: OperationSettingsSnapshot,
) -> ArchiveOperationSnapshot:
    def task(task_id: str) -> TaskSnapshot:
        return TaskSnapshot(task_id, "finished", "idle", "success")

    return ArchiveOperationSnapshot(
        operation=task(ids.operation),
        source_task=task(ids.source),
        work_task=task(ids.work),
        archive_item=ArchiveItemSnapshot(aweme_id, status, relative_directory),
        settings=settings,
    )


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
