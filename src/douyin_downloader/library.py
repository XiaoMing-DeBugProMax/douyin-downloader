from __future__ import annotations

from contextlib import AbstractContextManager
from pathlib import Path
from typing import Protocol

from douyin_downloader.archive import (
    ArchiveOperationSnapshot,
    WorkArchiveSnapshot,
)
from douyin_downloader.archive_adapters import RecycleBin, WindowsRecycleBin
from douyin_downloader.archive_paths import PinnedWorkDirectory
from douyin_downloader.domain import AppError
from douyin_downloader.settings import ArchiveProfile


class ManagedArchiveGateway(Protocol):
    def hold_registered_archive(self, aweme_id: str) -> AbstractContextManager[None]: ...

    def registered_archive_ids(self) -> tuple[str, ...]: ...

    def inspect_registered_archive(
        self,
        aweme_id: str,
    ) -> WorkArchiveSnapshot | None: ...

    async def rearchive_registered(
        self,
        aweme_id: str,
        *,
        profile: ArchiveProfile,
        force: bool = False,
    ) -> ArchiveOperationSnapshot: ...

    def validate_registered_location(
        self, aweme_id: str, archive_root: Path
    ) -> WorkArchiveSnapshot: ...

    def update_registered_location(self, aweme_id: str, archive_root: Path) -> None: ...

    def pin_registered_directory(self, aweme_id: str) -> PinnedWorkDirectory: ...

    def remove_registered_archive(self, aweme_id: str) -> None: ...


class LocalArchiveLibrary:
    """Work-centred library boundary over managed archive commands."""

    def __init__(
        self,
        managed_archive: ManagedArchiveGateway,
        recycle_bin: RecycleBin | None = None,
    ) -> None:
        self._managed_archive = managed_archive
        self._recycle_bin = recycle_bin or WindowsRecycleBin()

    def list_works(self) -> tuple[WorkArchiveSnapshot, ...]:
        items: list[WorkArchiveSnapshot] = []
        for aweme_id in self._managed_archive.registered_archive_ids():
            item = self._managed_archive.inspect_registered_archive(aweme_id)
            if item is not None:
                items.append(item)
        return tuple(items)

    def get_work(self, aweme_id: str) -> WorkArchiveSnapshot | None:
        return self._managed_archive.inspect_registered_archive(aweme_id)

    async def supplement(
        self,
        aweme_id: str,
        *,
        include_audio: bool,
        include_description: bool,
    ) -> ArchiveOperationSnapshot:
        item = self._required_work(aweme_id)
        return await self._managed_archive.rearchive_registered(
            aweme_id,
            profile=ArchiveProfile(
                include_audio=item.profile.include_audio or include_audio,
                include_description=(
                    item.profile.include_description or include_description
                ),
            ),
        )

    async def repair(self, aweme_id: str) -> ArchiveOperationSnapshot:
        item = self._required_work(aweme_id)
        return await self._managed_archive.rearchive_registered(
            aweme_id,
            profile=item.profile,
        )

    async def force_rearchive(
        self,
        aweme_id: str,
        *,
        confirm_overwrite: bool,
    ) -> ArchiveOperationSnapshot:
        if not aweme_id.isdigit():
            raise AppError("INVALID_INPUT", "作品标识无效。", 400)
        if not confirm_overwrite:
            raise AppError(
                "ARCHIVE_OVERWRITE_CONFIRMATION_REQUIRED",
                "强制重新归档前必须明确确认覆盖现有成果。",
                409,
            )
        item = self._required_work(aweme_id)
        return await self._managed_archive.rearchive_registered(
            aweme_id,
            profile=item.profile,
            force=True,
        )

    def relocate(self, aweme_id: str, archive_root: Path) -> WorkArchiveSnapshot:
        with self._managed_archive.hold_registered_archive(aweme_id):
            candidate = self._managed_archive.validate_registered_location(
                aweme_id, archive_root
            )
            self._managed_archive.update_registered_location(aweme_id, candidate.root)
            return candidate

    def delete(self, aweme_id: str, *, confirm_recycle: bool) -> None:
        if not confirm_recycle:
            raise AppError(
                "ARCHIVE_DELETE_CONFIRMATION_REQUIRED",
                "删除本地档案前必须明确确认移入回收站。",
                409,
            )
        with self._managed_archive.hold_registered_archive(aweme_id):
            try:
                with self._managed_archive.pin_registered_directory(
                    aweme_id
                ) as directory:
                    self._recycle_bin.move_to_recycle_bin(directory)
            except (AppError, OSError) as error:
                raise AppError(
                    "ARCHIVE_RECYCLE_FAILED",
                    "无法将档案移入回收站，文件与档案记录均已保留。",
                    409,
                ) from error
            self._managed_archive.remove_registered_archive(aweme_id)

    def _required_work(self, aweme_id: str) -> WorkArchiveSnapshot:
        item = self.get_work(aweme_id)
        if item is None:
            raise AppError("ARCHIVE_NOT_FOUND", "没有找到该作品的本地档案。", 404)
        return item
