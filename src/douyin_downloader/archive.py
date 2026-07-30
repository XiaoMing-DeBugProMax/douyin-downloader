from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from douyin_downloader.archive_adapters import (
    AtomicFilePromoter,
    FilePromoter,
    FolderOpener,
    HttpMediaAccess,
    MediaAccess,
    RemoteVideo,
    WindowsDirectoryChooser,
    WindowsFolderOpener,
)
from douyin_downloader.archive_paths import (
    pin_work_directory,
    work_directory,
)
from douyin_downloader.archive_store import ArchiveStore, TaskIds
from douyin_downloader.archive_validation import archive_failed, inspect_mp4
from douyin_downloader.domain import AppError, ResolvedWork

__all__ = [
    "ArchiveItemSnapshot",
    "ArchiveOperationSnapshot",
    "HttpMediaAccess",
    "ManagedArchive",
    "RemoteVideo",
    "SingleArchiveRequest",
    "TaskSnapshot",
    "WindowsDirectoryChooser",
]


@dataclass(frozen=True, slots=True)
class SingleArchiveRequest:
    aweme_id: str
    archive_root: Path


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
        self._media_access = media_access
        self._folder_opener = folder_opener or WindowsFolderOpener()
        self._file_promoter = file_promoter or AtomicFilePromoter()
        self._work_locks: dict[str, asyncio.Lock] = {}

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

        work_lock = self._work_locks.setdefault(request.aweme_id, asyncio.Lock())
        async with work_lock:
            return await self._archive_single_locked(request.aweme_id, root)

    async def _archive_single_locked(
        self,
        aweme_id: str,
        root: Path,
    ) -> ArchiveOperationSnapshot:
        existing = self._load_completed_archive(aweme_id)
        if existing is not None:
            snapshot, stored_root = existing
            video_path = (
                stored_root
                / snapshot.archive_item.relative_directory
                / f"{aweme_id}.mp4"
            )
            if video_path.is_file():
                return snapshot

        ids = TaskIds(uuid4().hex, uuid4().hex, uuid4().hex)
        part_path: Path | None = None
        promotion_started = False
        try:
            self._store.create_running(ids, aweme_id, root)
            resolved = await self._work_access.fetch_work(aweme_id)
            if resolved.snapshot.aweme_id != aweme_id:
                raise AppError(
                    "UPSTREAM_BLOCKED",
                    "解析服务暂时不可用，请稍后重试。",
                    502,
                )
            playback = resolved.preferred_playback_source()
            relative_directory = work_directory(resolved)
            with pin_work_directory(
                root,
                relative_directory,
                create=True,
            ) as output_directory:
                try:
                    final_path = output_directory / f"{aweme_id}.mp4"
                    part_path = (
                        output_directory / f"{aweme_id}.{ids.operation}.mp4.part"
                    )

                    remote = await self._media_access.open_video(
                        playback.cdn_mirror_urls
                    )
                    if remote.content_type.split(";", 1)[0].lower() != "video/mp4":
                        raise archive_failed()
                    written = 0
                    with part_path.open("wb") as output:
                        async for chunk in remote.chunks:
                            if chunk:
                                written += len(chunk)
                                output.write(chunk)
                        output.flush()
                    if (
                        remote.expected_size is not None
                        and written != remote.expected_size
                    ):
                        raise archive_failed()
                    duration_ms = inspect_mp4(part_path)
                    expected_duration = resolved.snapshot.duration_ms
                    tolerance = max(2_000, int(expected_duration * 0.15))
                    if (
                        expected_duration > 0
                        and abs(duration_ms - expected_duration) > tolerance
                    ):
                        raise archive_failed()

                    self._store.prepare_promotion(
                        ids,
                        aweme_id,
                        root,
                        relative_directory,
                    )
                    promotion_started = True
                    self._file_promoter.promote(part_path, final_path)
                    self._store.finish_promotion(ids, aweme_id)
                except Exception:
                    if part_path is not None and not promotion_started:
                        part_path.unlink(missing_ok=True)
                    raise
        except Exception:
            if not promotion_started:
                self._store.fail(ids)
            raise

        return _completed_snapshot(ids, aweme_id, relative_directory)

    def get_work_archive(self, aweme_id: str) -> ArchiveItemSnapshot | None:
        existing = self._load_completed_archive(aweme_id)
        return existing[0].archive_item if existing is not None else None

    def open_work_folder(self, aweme_id: str) -> None:
        existing = self._load_completed_archive(aweme_id)
        if existing is None:
            raise AppError("ARCHIVE_NOT_FOUND", "没有找到该作品的本地归档。", 404)
        snapshot, stored_root = existing
        try:
            pinned_directory = pin_work_directory(
                stored_root,
                snapshot.archive_item.relative_directory,
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

    def _load_completed_archive(
        self,
        aweme_id: str,
    ) -> tuple[ArchiveOperationSnapshot, Path] | None:
        self._recover_promotions()
        stored = self._store.load_completed(aweme_id)
        if stored is None:
            return None
        return (
            _completed_snapshot(
                stored.ids,
                stored.aweme_id,
                stored.relative_directory,
            ),
            stored.root,
        )

    def _recover_promotions(self) -> None:
        for promotion in self._store.pending_promotions():
            part_path: Path | None = None
            try:
                with pin_work_directory(
                    promotion.root,
                    promotion.relative_directory,
                    create=False,
                ) as output_directory:
                    try:
                        final_path = output_directory / f"{promotion.aweme_id}.mp4"
                        part_path = (
                            output_directory
                            / (
                                f"{promotion.aweme_id}."
                                f"{promotion.ids.operation}.mp4.part"
                            )
                        )
                        if not final_path.is_file():
                            if not part_path.is_file():
                                raise archive_failed()
                            inspect_mp4(part_path)
                            part_path.replace(final_path)
                        inspect_mp4(final_path)
                        self._store.finish_promotion(
                            promotion.ids,
                            promotion.aweme_id,
                        )
                    except Exception:
                        if part_path is not None:
                            part_path.unlink(missing_ok=True)
                        raise
            except Exception:
                self._store.discard_promotion(promotion)


def _completed_snapshot(
    ids: TaskIds,
    aweme_id: str,
    relative_directory: Path,
) -> ArchiveOperationSnapshot:
    def task(task_id: str) -> TaskSnapshot:
        return TaskSnapshot(task_id, "finished", "idle", "success")

    return ArchiveOperationSnapshot(
        operation=task(ids.operation),
        source_task=task(ids.source),
        work_task=task(ids.work),
        archive_item=ArchiveItemSnapshot(aweme_id, "archived", relative_directory),
    )
