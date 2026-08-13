from __future__ import annotations

import hashlib
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from douyin_downloader.archive_artifacts import ArchiveMetadata, validate_metadata
from douyin_downloader.archive_paths import is_reparse_point, pin_work_directory
from douyin_downloader.archive_store import ArchiveStore
from douyin_downloader.database_safety import (
    database_is_safe_and_valid,
    file_contains_sensitive_marker,
)
from douyin_downloader.domain import AppError

_DAILY_BACKUP = re.compile(r"^archive\.daily-(\d{4}-\d{2}-\d{2})\.bak$")
_MIGRATION_BACKUP = re.compile(r"^archive\.pre-([a-z0-9-]+)\.bak$")


@dataclass(frozen=True, slots=True)
class DatabaseBackup:
    name: str
    path: Path
    local_date: date
    kind: str = "daily"


@dataclass(frozen=True, slots=True)
class DatabaseRecoveryStatus:
    state: str
    backups: tuple[DatabaseBackup, ...]
    quarantined_path: Path | None = None
    history_recovery: str = "complete"
    rebuilt_archives: int = 0


class DatabaseRecovery:
    def __init__(
        self,
        database_path: Path,
        *,
        today: Callable[[], date] = date.today,
        now: Callable[[], datetime] = datetime.now,
    ) -> None:
        self._database_path = database_path
        self._today = today
        self._now = now
        self.backup_directory = database_path.parent / "backups"
        self._status = DatabaseRecoveryStatus("healthy", ())

    def prepare_startup(self) -> DatabaseRecoveryStatus:
        if not self._database_path.exists():
            quarantined = self._latest_quarantined_database()
            state = "recovery_required" if quarantined is not None else "healthy"
            self._status = DatabaseRecoveryStatus(
                state,
                self.list_valid_backups(),
                quarantined,
            )
            return self._status
        if not database_is_safe_and_valid(self._database_path):
            quarantined = self._quarantine_database()
            self._status = DatabaseRecoveryStatus(
                "recovery_required",
                self.list_valid_backups(),
                quarantined,
            )
            return self._status
        self._create_daily_backup()
        self._prune_daily_backups()
        self._status = DatabaseRecoveryStatus("healthy", self.list_valid_backups())
        return self._status

    def status(self) -> DatabaseRecoveryStatus:
        return self._status

    def list_valid_backups(self) -> tuple[DatabaseBackup, ...]:
        backups: list[DatabaseBackup] = []
        candidates = (
            tuple(self.backup_directory.iterdir())
            if self.backup_directory.is_dir()
            else ()
        ) + tuple(self._database_path.parent.glob("archive.pre-*.bak"))
        for path in candidates:
            match = _DAILY_BACKUP.fullmatch(path.name)
            migration_match = _MIGRATION_BACKUP.fullmatch(path.name)
            if not database_is_safe_and_valid(path):
                continue
            if match is not None:
                try:
                    local_date = date.fromisoformat(match.group(1))
                except ValueError:
                    continue
                backups.append(DatabaseBackup(path.name, path, local_date))
            elif migration_match is not None:
                backups.append(
                    DatabaseBackup(
                        path.name,
                        path,
                        date.fromtimestamp(path.stat().st_mtime),
                        "migration",
                    )
                )
        return tuple(
            sorted(
                backups,
                key=lambda item: (item.local_date, item.kind == "daily"),
                reverse=True,
            )
        )

    def restore(self, backup_name: str) -> DatabaseRecoveryStatus:
        backup = next(
            (item for item in self.list_valid_backups() if item.name == backup_name),
            None,
        )
        if backup is None:
            raise AppError(
                "DATABASE_BACKUP_INVALID",
                "所选数据库备份无效或不可恢复。",
                409,
            )
        if self._database_path.exists():
            raise AppError(
                "DATABASE_RESTORE_TARGET_EXISTS",
                "当前数据库仍然存在，不能覆盖恢复。",
                409,
            )
        part_path = self._database_path.with_suffix(".restore.part")
        part_path.unlink(missing_ok=True)
        try:
            _copy_database(backup.path, part_path)
        except BaseException:
            part_path.unlink(missing_ok=True)
            raise
        if not database_is_safe_and_valid(part_path):
            part_path.unlink(missing_ok=True)
            raise AppError("DATABASE_RESTORE_FAILED", "数据库恢复后校验失败。", 409)
        part_path.replace(self._database_path)
        if not database_is_safe_and_valid(self._database_path):
            raise AppError("DATABASE_RESTORE_FAILED", "数据库恢复后校验失败。", 409)
        self._status = DatabaseRecoveryStatus("healthy", self.list_valid_backups())
        return self._status

    def rebuild_from_metadata(self, archive_root: Path) -> DatabaseRecoveryStatus:
        if (
            not archive_root.is_absolute()
            or not archive_root.is_dir()
            or is_reparse_point(archive_root)
        ):
            raise AppError("DATABASE_REBUILD_ROOT_INVALID", "归档根目录无效。", 409)
        root = archive_root.resolve(strict=True)
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        part_path = self._database_path.with_suffix(".rebuild.part")
        part_path.unlink(missing_ok=True)
        rebuilt = 0
        seen: set[str] = set()
        try:
            ArchiveStore(part_path).initialize()
            connection = sqlite3.connect(part_path)
            connection.execute("PRAGMA foreign_keys = ON")
            try:
                with connection:
                    for metadata_path in sorted(root.rglob("*.metadata.json")):
                        item = _validated_rebuild_item(root, metadata_path)
                        if item is None or item[0].work.aweme_id in seen:
                            continue
                        document, relative_directory, artifacts = item
                        aweme_id = document.work.aweme_id
                        seen.add(aweme_id)
                        operation_id = f"rebuild-{aweme_id}"
                        include_audio = any(row[0] == "audio" for row in artifacts)
                        include_description = any(
                            row[0] == "description" for row in artifacts
                        )
                        connection.execute(
                            """
                            INSERT INTO archive_operations
                                (operation_id, lifecycle, phase, result, root_path,
                                 naming_template, profile_audio,
                                 profile_description, download_concurrency,
                                 retry_limit, history_visible)
                            VALUES (?, 'finished', 'idle', 'success', ?,
                                    '{aweme_id}', ?, ?, 3, 3, 0)
                            """,
                            (
                                operation_id,
                                str(root),
                                int(include_audio),
                                int(include_description),
                            ),
                        )
                        connection.execute(
                            """
                            INSERT INTO archive_items
                                (aweme_id, operation_id, root_path,
                                 relative_directory, status, author_nickname,
                                 published_at, naming_template, profile_audio,
                                 profile_description, download_concurrency,
                                 retry_limit)
                            VALUES (?, ?, ?, ?, 'archived', ?, ?, '{aweme_id}',
                                    ?, ?, 3, 3)
                            """,
                            (
                                aweme_id,
                                operation_id,
                                str(root),
                                str(relative_directory),
                                document.work.author.nickname,
                                document.work.published_at,
                                int(include_audio),
                                int(include_description),
                            ),
                        )
                        connection.executemany(
                            """
                            INSERT INTO archive_artifacts
                                (aweme_id, kind, relative_path,
                                 part_relative_path, size_bytes, mime_type,
                                 sha256, status)
                            VALUES (?, ?, ?, NULL, ?, ?, ?, 'archived')
                            """,
                            ((aweme_id, *row) for row in artifacts),
                        )
                        rebuilt += 1
            finally:
                connection.close()
            if rebuilt == 0 or not database_is_safe_and_valid(part_path):
                raise AppError(
                    "DATABASE_REBUILD_EMPTY",
                    "没有找到可安全重建的档案元数据。",
                    409,
                )
            if self._database_path.exists():
                raise AppError(
                    "DATABASE_REBUILD_TARGET_EXISTS",
                    "当前数据库仍然存在，不能覆盖重建。",
                    409,
                )
            part_path.replace(self._database_path)
            self._status = DatabaseRecoveryStatus(
                "healthy",
                self.list_valid_backups(),
                history_recovery="incomplete",
                rebuilt_archives=rebuilt,
            )
            return self._status
        except BaseException:
            part_path.unlink(missing_ok=True)
            raise

    def _create_daily_backup(self) -> None:
        self.backup_directory.mkdir(parents=True, exist_ok=True)
        backup_path = self.backup_directory / (
            f"archive.daily-{self._today().isoformat()}.bak"
        )
        if backup_path.exists() and database_is_safe_and_valid(backup_path):
            return
        part_path = backup_path.with_suffix(".bak.part")
        part_path.unlink(missing_ok=True)
        try:
            _copy_database(self._database_path, part_path)
        except BaseException:
            part_path.unlink(missing_ok=True)
            raise
        if not database_is_safe_and_valid(part_path):
            part_path.unlink(missing_ok=True)
            raise AppError("DATABASE_BACKUP_FAILED", "数据库备份校验失败。", 500)
        part_path.replace(backup_path)

    def _prune_daily_backups(self) -> None:
        if not self.backup_directory.is_dir():
            return
        dated_paths: list[tuple[date, Path]] = []
        for path in self.backup_directory.iterdir():
            match = _DAILY_BACKUP.fullmatch(path.name)
            if match is None:
                continue
            try:
                dated_paths.append((date.fromisoformat(match.group(1)), path))
            except ValueError:
                continue
        for _, path in sorted(dated_paths, reverse=True)[7:]:
            path.unlink()

    def _quarantine_database(self) -> Path:
        timestamp = self._now().strftime("%Y%m%d-%H%M%S")
        suffix = 0
        while True:
            discriminator = "" if suffix == 0 else f"-{suffix}"
            quarantined = self._database_path.with_name(
                f"{self._database_path.stem}.corrupt-{timestamp}{discriminator}"
                f"{self._database_path.suffix}"
            )
            if not quarantined.exists():
                break
            suffix += 1
        self._database_path.replace(quarantined)
        return quarantined

    def _latest_quarantined_database(self) -> Path | None:
        candidates = tuple(
            self._database_path.parent.glob(
                f"{self._database_path.stem}.corrupt-*{self._database_path.suffix}"
            )
        )
        return max(candidates, key=lambda path: path.stat().st_mtime, default=None)


def _copy_database(source_path: Path, destination_path: Path) -> None:
    source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    try:
        destination = sqlite3.connect(destination_path)
        try:
            source.backup(destination)
            destination.commit()
        finally:
            destination.close()
    finally:
        source.close()


def _validated_rebuild_item(
    root: Path,
    metadata_path: Path,
) -> tuple[ArchiveMetadata, Path, tuple[tuple[str, str, int, str, str], ...]] | None:
    try:
        relative_directory = metadata_path.parent.relative_to(root)
        with pin_work_directory(root, relative_directory, create=False) as directory:
            pinned_metadata = directory / metadata_path.name
            if is_reparse_point(pinned_metadata) or not _file_is_sensitive_safe(
                pinned_metadata
            ):
                return None
            aweme_id = metadata_path.name.removesuffix(".metadata.json")
            if not aweme_id.isdigit():
                return None
            document = validate_metadata(pinned_metadata, aweme_id)
            artifacts: list[tuple[str, str, int, str, str]] = []
            for artifact in document.artifacts:
                path = directory / artifact.path
                if (
                    is_reparse_point(path)
                    or not path.is_file()
                    or file_contains_sensitive_marker(path)
                ):
                    return None
                payload = path.read_bytes()
                if (
                    len(payload) != artifact.size_bytes
                    or hashlib.sha256(payload).hexdigest() != artifact.sha256
                ):
                    return None
                artifacts.append(
                    (
                        artifact.kind,
                        artifact.path,
                        artifact.size_bytes,
                        artifact.mime_type,
                        artifact.sha256,
                    )
                )
            metadata_payload = pinned_metadata.read_bytes()
            artifacts.append(
                (
                    "metadata",
                    pinned_metadata.name,
                    len(metadata_payload),
                    "application/json",
                    hashlib.sha256(metadata_payload).hexdigest(),
                )
            )
            return document, relative_directory, tuple(artifacts)
    except (OSError, AppError, ValueError):
        return None


def _file_is_sensitive_safe(path: Path) -> bool:
    return not file_contains_sensitive_marker(path)
