from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from string import Formatter

from douyin_downloader.archive_paths import is_reparse_point
from douyin_downloader.domain import AppError, ResolvedWork

_DEFAULT_NAMING_TEMPLATE = "{aweme_id}"
_TEMPLATE_FIELDS = frozenset({"date", "author", "description", "aweme_id"})
_MAX_TEMPLATE_LENGTH = 200
_MAX_BASE_NAME_LENGTH = 120
_WINDOWS_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_DEVICE_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{number}" for number in range(1, 10)}
    | {f"LPT{number}" for number in range(1, 10)}
)


@dataclass(frozen=True, slots=True)
class ArchiveProfile:
    include_audio: bool = False
    include_description: bool = False


@dataclass(frozen=True, slots=True)
class CurrentSettings:
    archive_root: Path | None
    naming_template: str
    profile: ArchiveProfile
    download_concurrency: int
    retry_limit: int


@dataclass(frozen=True, slots=True)
class OperationSettingsSnapshot:
    archive_root: Path
    naming_template: str
    profile: ArchiveProfile
    download_concurrency: int
    retry_limit: int

    def artifact_base_name(self, resolved: ResolvedWork) -> str:
        snapshot = resolved.snapshot
        published_at = snapshot.published_at
        date = (
            datetime.fromtimestamp(published_at, UTC).date().isoformat()
            if published_at is not None
            else "unknown-date"
        )
        rendered = self.naming_template.format_map(
            {
                "date": date,
                "author": snapshot.author.nickname,
                "description": snapshot.description,
                "aweme_id": snapshot.aweme_id,
            }
        )
        return _safe_windows_base_name(rendered, snapshot.aweme_id)


@dataclass(frozen=True, slots=True)
class SettingsUpdate:
    naming_template: str
    profile: ArchiveProfile
    download_concurrency: int
    retry_limit: int


class SettingsModule:
    """Owns current archive defaults and produces immutable operation snapshots."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path

    def current(self) -> CurrentSettings:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT archive_root, naming_template, profile_audio,
                       profile_description, download_concurrency, retry_limit
                FROM current_settings
                WHERE settings_id = 1
                """
            ).fetchone()
        if row is None:  # pragma: no cover - protected by schema initialization
            raise RuntimeError("current settings row was not initialized")
        return CurrentSettings(
            archive_root=Path(str(row[0])) if row[0] is not None else None,
            naming_template=str(row[1]),
            profile=ArchiveProfile(bool(row[2]), bool(row[3])),
            download_concurrency=int(row[4]),
            retry_limit=int(row[5]),
        )

    def update(self, changes: SettingsUpdate) -> CurrentSettings:
        _validate_update(changes)
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE current_settings
                SET naming_template = ?, profile_audio = ?,
                    profile_description = ?, download_concurrency = ?,
                    retry_limit = ?
                WHERE settings_id = 1
                """,
                (
                    changes.naming_template,
                    int(changes.profile.include_audio),
                    int(changes.profile.include_description),
                    changes.download_concurrency,
                    changes.retry_limit,
                ),
            )
        return self.current()

    def set_archive_root(self, archive_root: Path) -> CurrentSettings:
        resolved_root = _validated_archive_root(archive_root)
        with self._connection() as connection:
            connection.execute(
                "UPDATE current_settings SET archive_root = ? WHERE settings_id = 1",
                (str(resolved_root),),
            )
        return self.current()

    def capture(self) -> OperationSettingsSnapshot:
        current = self.current()
        if current.archive_root is None:
            raise AppError(
                "ARCHIVE_ROOT_REQUIRED",
                "请先在设置中选择默认归档根目录。",
                409,
            )
        root = _validated_archive_root(current.archive_root)
        return OperationSettingsSnapshot(
            archive_root=root,
            naming_template=current.naming_template,
            profile=current.profile,
            download_concurrency=current.download_concurrency,
            retry_limit=current.retry_limit,
        )

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self._database_path)
        try:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS current_settings (
                    settings_id INTEGER PRIMARY KEY CHECK (settings_id = 1),
                    archive_root TEXT,
                    naming_template TEXT NOT NULL,
                    profile_audio INTEGER NOT NULL CHECK (profile_audio IN (0, 1)),
                    profile_description INTEGER NOT NULL
                        CHECK (profile_description IN (0, 1)),
                    download_concurrency INTEGER NOT NULL
                        CHECK (download_concurrency BETWEEN 1 AND 5),
                    retry_limit INTEGER NOT NULL CHECK (retry_limit BETWEEN 0 AND 3)
                )
                """
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO current_settings
                    (settings_id, archive_root, naming_template, profile_audio,
                     profile_description, download_concurrency, retry_limit)
                VALUES (1, NULL, ?, 0, 0, 3, 3)
                """,
                (_DEFAULT_NAMING_TEMPLATE,),
            )
            with connection:
                yield connection
        finally:
            connection.close()


def _validate_update(changes: SettingsUpdate) -> None:
    if not 1 <= changes.download_concurrency <= 5:
        raise AppError(
            "SETTINGS_INVALID",
            "下载并发必须是 1 到 5 之间的整数。",
            400,
        )
    if not 0 <= changes.retry_limit <= 3:
        raise AppError(
            "SETTINGS_INVALID",
            "失败重试必须是 0 到 3 之间的整数。",
            400,
        )
    _validate_naming_template(changes.naming_template)


def _validate_naming_template(template: str) -> None:
    if (
        not template
        or len(template) > _MAX_TEMPLATE_LENGTH
        or "\x00" in template
        or "/" in template
        or "\\" in template
    ):
        raise _template_invalid()
    try:
        fields = tuple(Formatter().parse(template))
    except ValueError as error:
        raise _template_invalid() from error
    for _, field_name, format_spec, conversion in fields:
        if field_name is None:
            continue
        if (
            field_name not in _TEMPLATE_FIELDS
            or format_spec
            or conversion is not None
        ):
            raise _template_invalid()


def _validated_archive_root(archive_root: Path) -> Path:
    if (
        not archive_root.is_absolute()
        or not archive_root.is_dir()
        or is_reparse_point(archive_root)
    ):
        raise AppError(
            "ARCHIVE_ROOT_INVALID",
            "请选择有效的本地归档目录。",
            400,
        )
    try:
        return archive_root.resolve(strict=True)
    except OSError as error:
        raise AppError(
            "ARCHIVE_ROOT_INVALID",
            "请选择有效的本地归档目录。",
            400,
        ) from error


def _template_invalid() -> AppError:
    return AppError(
        "SETTINGS_INVALID",
        "基础名称模板包含不支持的字段或路径字符。",
        400,
    )


def _safe_windows_base_name(value: str, fallback: str) -> str:
    normalized = _WINDOWS_INVALID.sub("_", value)
    normalized = " ".join(normalized.split()).strip().rstrip(" .")
    if normalized in {"", ".", ".."}:
        normalized = fallback
    normalized = normalized[:_MAX_BASE_NAME_LENGTH].rstrip(" .") or fallback
    device_stem = normalized.split(".", 1)[0].upper()
    if device_stem in _WINDOWS_DEVICE_NAMES:
        normalized = f"_{normalized}"
    return normalized
