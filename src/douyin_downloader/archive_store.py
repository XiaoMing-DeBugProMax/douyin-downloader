from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from douyin_downloader.archive_artifacts import ArtifactKind, ArtifactRecord
from douyin_downloader.database import ensure_archive_schema
from douyin_downloader.settings import (
    ArchiveProfile,
    NamingTemplate,
    OperationSettingsSnapshot,
)


@dataclass(frozen=True, slots=True)
class TaskIds:
    operation: str
    source: str
    work: str


@dataclass(frozen=True, slots=True)
class StoredArchive:
    ids: TaskIds
    aweme_id: str
    root: Path
    relative_directory: Path
    status: str
    artifacts: tuple[ArtifactRecord, ...]
    settings: OperationSettingsSnapshot


@dataclass(frozen=True, slots=True)
class PendingPromotion:
    ids: TaskIds
    aweme_id: str
    root: Path
    relative_directory: Path
    artifacts: tuple[ArtifactRecord, ...]


@dataclass(frozen=True, slots=True)
class StoredTask:
    task_id: str
    lifecycle: str
    phase: str
    result: str
    error_code: str | None
    completed_bytes: int
    total_bytes: int | None
    speed_bytes_per_second: float | None
    eta_seconds: int | None


@dataclass(frozen=True, slots=True)
class StoredWorkTask:
    task: StoredTask
    aweme_id: str


@dataclass(frozen=True, slots=True)
class StoredSourceTask:
    task: StoredTask
    work_tasks: tuple[StoredWorkTask, ...]


@dataclass(frozen=True, slots=True)
class StoredTaskOperation:
    task: StoredTask
    source_tasks: tuple[StoredSourceTask, ...]


class ArchiveStore:
    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path

    def load_archive(self, aweme_id: str) -> StoredArchive | None:
        if not self._database_path.is_file():
            return None
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT o.operation_id, s.source_task_id, w.work_task_id,
                       i.root_path, i.relative_directory, i.status,
                       o.naming_template, o.profile_audio,
                       o.profile_description, o.download_concurrency,
                       o.retry_limit
                FROM archive_items AS i
                JOIN archive_operations AS o ON o.operation_id = i.operation_id
                JOIN source_tasks AS s ON s.operation_id = o.operation_id
                JOIN work_tasks AS w ON w.source_task_id = s.source_task_id
                WHERE i.aweme_id = ?
                """,
                (aweme_id,),
            ).fetchone()
        if row is None:
            return None
        return StoredArchive(
            ids=TaskIds(str(row[0]), str(row[1]), str(row[2])),
            aweme_id=aweme_id,
            root=Path(str(row[3])),
            relative_directory=Path(str(row[4])),
            status=str(row[5]),
            artifacts=self._load_artifacts(aweme_id),
            settings=OperationSettingsSnapshot(
                archive_root=Path(str(row[3])),
                naming_template=NamingTemplate(str(row[6])),
                profile=ArchiveProfile(bool(row[7]), bool(row[8])),
                download_concurrency=int(row[9]),
                retry_limit=int(row[10]),
            ),
        )

    def set_archive_status(self, aweme_id: str, status: str) -> None:
        with self._connection() as connection:
            connection.execute(
                "UPDATE archive_items SET status=? WHERE aweme_id=?",
                (status, aweme_id),
            )

    def list_task_operations(self) -> tuple[StoredTaskOperation, ...]:
        if not self._database_path.is_file():
            return ()
        with self._connection() as connection:
            operation_rows = connection.execute(
                """
                SELECT operation_id, lifecycle, phase, result, error_code,
                       completed_bytes, total_bytes, speed_bytes_per_second,
                       eta_seconds
                FROM archive_operations
                WHERE history_visible = 1
                ORDER BY rowid DESC
                """
            ).fetchall()
            source_rows = connection.execute(
                """
                SELECT source_task_id, lifecycle, phase, result, error_code,
                       completed_bytes, total_bytes, speed_bytes_per_second,
                       eta_seconds, operation_id
                FROM source_tasks
                ORDER BY rowid
                """
            ).fetchall()
            work_rows = connection.execute(
                """
                SELECT work_task_id, lifecycle, phase, result, error_code,
                       completed_bytes, total_bytes, speed_bytes_per_second,
                       eta_seconds, source_task_id, aweme_id
                FROM work_tasks
                ORDER BY rowid
                """
            ).fetchall()

        work_by_source: dict[str, list[StoredWorkTask]] = {}
        for row in work_rows:
            source_id = str(row[9])
            work_by_source.setdefault(source_id, []).append(
                StoredWorkTask(
                    task=_stored_task(row),
                    aweme_id=str(row[10]),
                )
            )

        sources_by_operation: dict[str, list[StoredSourceTask]] = {}
        for row in source_rows:
            source_id = str(row[0])
            operation_id = str(row[9])
            sources_by_operation.setdefault(operation_id, []).append(
                StoredSourceTask(
                    task=_stored_task(row),
                    work_tasks=tuple(work_by_source.get(source_id, ())),
                )
            )

        return tuple(
            StoredTaskOperation(
                task=_stored_task(row),
                source_tasks=tuple(sources_by_operation.get(str(row[0]), ())),
            )
            for row in operation_rows
        )

    def create_running(
        self,
        ids: TaskIds,
        aweme_id: str,
        settings: OperationSettingsSnapshot,
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO archive_operations
                    (operation_id, lifecycle, phase, result, root_path,
                     naming_template, profile_audio, profile_description,
                     download_concurrency, retry_limit)
                VALUES (?, 'running', 'resolving', 'none', ?, ?, ?, ?, ?, ?)
                """,
                (
                    ids.operation,
                    str(settings.archive_root),
                    str(settings.naming_template),
                    int(settings.profile.include_audio),
                    int(settings.profile.include_description),
                    settings.download_concurrency,
                    settings.retry_limit,
                ),
            )
            connection.execute(
                "INSERT INTO source_tasks "
                "(source_task_id, operation_id, lifecycle, phase, result, error_code) "
                "VALUES (?, ?, 'running', 'resolving', 'none', NULL)",
                (ids.source, ids.operation),
            )
            connection.execute(
                "INSERT INTO work_tasks "
                "(work_task_id, source_task_id, aweme_id, lifecycle, phase, result, "
                "error_code) VALUES (?, ?, ?, 'running', 'resolving', 'none', NULL)",
                (ids.work, ids.source, aweme_id),
            )

    def clear_task_operation(self, operation_id: str) -> str:
        with self._connection() as connection:
            operation = connection.execute(
                "SELECT lifecycle, history_visible FROM archive_operations "
                "WHERE operation_id=?",
                (operation_id,),
            ).fetchone()
            if operation is None or not bool(operation[1]):
                return "not_found"
            lifecycles = [str(operation[0])]
            lifecycles.extend(
                str(row[0])
                for row in connection.execute(
                    "SELECT lifecycle FROM source_tasks WHERE operation_id=?",
                    (operation_id,),
                )
            )
            lifecycles.extend(
                str(row[0])
                for row in connection.execute(
                    """
                    SELECT w.lifecycle
                    FROM work_tasks AS w
                    JOIN source_tasks AS s
                      ON s.source_task_id = w.source_task_id
                    WHERE s.operation_id=?
                    """,
                    (operation_id,),
                )
            )
            if any(
                lifecycle not in {"finished", "cancelled"}
                for lifecycle in lifecycles
            ):
                return "active"
            connection.execute(
                "UPDATE archive_operations SET history_visible=0 "
                "WHERE operation_id=?",
                (operation_id,),
            )
        return "cleared"

    def interrupt_running_tasks(self) -> None:
        if not self._database_path.is_file():
            return
        with self._connection() as connection:
            connection.executescript(
                """
                UPDATE archive_operations
                SET lifecycle='interrupted', phase='idle',
                    speed_bytes_per_second=NULL, eta_seconds=NULL
                WHERE lifecycle='running';
                UPDATE source_tasks
                SET lifecycle='interrupted', phase='idle',
                    speed_bytes_per_second=NULL, eta_seconds=NULL
                WHERE lifecycle='running';
                UPDATE work_tasks
                SET lifecycle='interrupted', phase='idle',
                    speed_bytes_per_second=NULL, eta_seconds=NULL
                WHERE lifecycle='running';
                """
            )

    def update_progress(
        self,
        ids: TaskIds,
        *,
        phase: str,
        completed_bytes: int,
        total_bytes: int | None,
        speed_bytes_per_second: float | None,
        eta_seconds: int | None,
    ) -> None:
        with self._connection() as connection:
            values = (
                phase,
                completed_bytes,
                total_bytes,
                speed_bytes_per_second,
                eta_seconds,
            )
            connection.execute(
                "UPDATE archive_operations SET phase=?, completed_bytes=?, "
                "total_bytes=?, speed_bytes_per_second=?, eta_seconds=? "
                "WHERE operation_id=?",
                (*values, ids.operation),
            )
            connection.execute(
                "UPDATE source_tasks SET phase=?, completed_bytes=?, total_bytes=?, "
                "speed_bytes_per_second=?, eta_seconds=? "
                "WHERE source_task_id=?",
                (*values, ids.source),
            )
            connection.execute(
                "UPDATE work_tasks SET phase=?, completed_bytes=?, total_bytes=?, "
                "speed_bytes_per_second=?, eta_seconds=? "
                "WHERE work_task_id=?",
                (*values, ids.work),
            )

    def prepare_promotion(
        self,
        ids: TaskIds,
        aweme_id: str,
        root: Path,
        relative_directory: Path,
        artifacts: tuple[ArtifactRecord, ...],
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO archive_items VALUES (?, ?, ?, ?, 'promoting')
                ON CONFLICT(aweme_id) DO UPDATE SET
                    operation_id=excluded.operation_id,
                    root_path=excluded.root_path,
                    relative_directory=excluded.relative_directory,
                    status='promoting'
                """,
                (aweme_id, ids.operation, str(root), str(relative_directory)),
            )
            connection.execute(
                "DELETE FROM archive_artifacts WHERE aweme_id=?",
                (aweme_id,),
            )
            connection.executemany(
                """
                INSERT INTO archive_artifacts
                    (aweme_id, kind, relative_path, part_relative_path,
                     size_bytes, mime_type, sha256, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    (
                        aweme_id,
                        artifact.kind,
                        str(artifact.relative_path),
                        (
                            str(artifact.part_relative_path)
                            if artifact.part_relative_path is not None
                            else None
                        ),
                        artifact.size_bytes,
                        artifact.mime_type,
                        artifact.sha256,
                        artifact.status,
                    )
                    for artifact in artifacts
                ),
            )

    def finish_promotion(
        self,
        ids: TaskIds,
        aweme_id: str,
        result: str = "success",
    ) -> None:
        item_status = "archived" if result == "success" else "needs_repair"
        with self._connection() as connection:
            connection.execute(
                "UPDATE archive_items SET status=? "
                "WHERE aweme_id=? AND operation_id=?",
                (item_status, aweme_id, ids.operation),
            )
            connection.execute(
                "UPDATE archive_artifacts SET status='archived', "
                "part_relative_path=NULL WHERE aweme_id=? AND status='promoting'",
                (aweme_id,),
            )
            _set_task_results(connection, ids, result)

    def fail(self, ids: TaskIds, error_code: str = "ARCHIVE_FAILED") -> None:
        try:
            with self._connection() as connection:
                _set_task_results(connection, ids, "failed", error_code)
        except sqlite3.Error:
            pass

    def pending_promotions(self) -> tuple[PendingPromotion, ...]:
        if not self._database_path.is_file():
            return ()
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT i.aweme_id, i.operation_id, i.root_path,
                       i.relative_directory, s.source_task_id, w.work_task_id
                FROM archive_items AS i
                JOIN source_tasks AS s ON s.operation_id = i.operation_id
                JOIN work_tasks AS w ON w.source_task_id = s.source_task_id
                WHERE i.status = 'promoting'
                """
            ).fetchall()
        return tuple(
            PendingPromotion(
                ids=TaskIds(str(row[1]), str(row[4]), str(row[5])),
                aweme_id=str(row[0]),
                root=Path(str(row[2])),
                relative_directory=Path(str(row[3])),
                artifacts=self._load_artifacts(str(row[0])),
            )
            for row in rows
        )

    def discard_promotion(self, promotion: PendingPromotion) -> None:
        with self._connection() as connection:
            connection.execute(
                "UPDATE archive_items SET status='needs_repair' "
                "WHERE aweme_id=? AND operation_id=?",
                (promotion.aweme_id, promotion.ids.operation),
            )
            connection.execute(
                "UPDATE archive_artifacts SET status='needs_repair', "
                "part_relative_path=NULL WHERE aweme_id=?",
                (promotion.aweme_id,),
            )
            _set_task_results(
                connection,
                promotion.ids,
                "failed",
                "ARCHIVE_FAILED",
            )

    def _connect(self) -> sqlite3.Connection:
        ensure_archive_schema(self._database_path)
        connection = sqlite3.connect(self._database_path)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS archive_operations (
                operation_id TEXT PRIMARY KEY,
                lifecycle TEXT NOT NULL,
                phase TEXT NOT NULL,
                result TEXT NOT NULL,
                root_path TEXT NOT NULL,
                naming_template TEXT NOT NULL DEFAULT '{aweme_id}',
                profile_audio INTEGER NOT NULL DEFAULT 0,
                profile_description INTEGER NOT NULL DEFAULT 0,
                download_concurrency INTEGER NOT NULL DEFAULT 3,
                retry_limit INTEGER NOT NULL DEFAULT 3
                ,history_visible INTEGER NOT NULL DEFAULT 1
                ,error_code TEXT
                ,completed_bytes INTEGER NOT NULL DEFAULT 0
                ,total_bytes INTEGER
                ,speed_bytes_per_second REAL
                ,eta_seconds INTEGER
            )
            """
        )
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS source_tasks (
                source_task_id TEXT PRIMARY KEY,
                operation_id TEXT NOT NULL REFERENCES archive_operations(operation_id),
                lifecycle TEXT NOT NULL,
                phase TEXT NOT NULL,
                result TEXT NOT NULL
                ,error_code TEXT
                ,completed_bytes INTEGER NOT NULL DEFAULT 0
                ,total_bytes INTEGER
                ,speed_bytes_per_second REAL
                ,eta_seconds INTEGER
            );
            CREATE TABLE IF NOT EXISTS work_tasks (
                work_task_id TEXT PRIMARY KEY,
                source_task_id TEXT NOT NULL REFERENCES source_tasks(source_task_id),
                aweme_id TEXT NOT NULL,
                lifecycle TEXT NOT NULL,
                phase TEXT NOT NULL,
                result TEXT NOT NULL
                ,error_code TEXT
                ,completed_bytes INTEGER NOT NULL DEFAULT 0
                ,total_bytes INTEGER
                ,speed_bytes_per_second REAL
                ,eta_seconds INTEGER
            );
            CREATE TABLE IF NOT EXISTS archive_items (
                aweme_id TEXT PRIMARY KEY,
                operation_id TEXT NOT NULL REFERENCES archive_operations(operation_id),
                root_path TEXT NOT NULL,
                relative_directory TEXT NOT NULL,
                status TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS archive_artifacts (
                aweme_id TEXT NOT NULL REFERENCES archive_items(aweme_id)
                    ON DELETE CASCADE,
                kind TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                part_relative_path TEXT,
                size_bytes INTEGER NOT NULL,
                mime_type TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                status TEXT NOT NULL,
                PRIMARY KEY (aweme_id, kind)
            );
            """
        )
        return connection

    def _load_artifacts(self, aweme_id: str) -> tuple[ArtifactRecord, ...]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT kind, relative_path, part_relative_path,
                       size_bytes, mime_type, sha256, status
                FROM archive_artifacts
                WHERE aweme_id=?
                ORDER BY kind
                """,
                (aweme_id,),
            ).fetchall()
        return tuple(
            ArtifactRecord(
                kind=cast(ArtifactKind, str(row[0])),
                relative_path=Path(str(row[1])),
                part_relative_path=Path(str(row[2])) if row[2] is not None else None,
                size_bytes=int(row[3]),
                mime_type=str(row[4]),
                sha256=str(row[5]),
                status=str(row[6]),
            )
            for row in rows
        )

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()


def _stored_task(row: tuple[object, ...]) -> StoredTask:
    return StoredTask(
        task_id=str(row[0]),
        lifecycle=str(row[1]),
        phase=str(row[2]),
        result=str(row[3]),
        error_code=str(row[4]) if row[4] is not None else None,
        completed_bytes=int(str(row[5])),
        total_bytes=int(str(row[6])) if row[6] is not None else None,
        speed_bytes_per_second=float(str(row[7])) if row[7] is not None else None,
        eta_seconds=int(str(row[8])) if row[8] is not None else None,
    )


def _set_task_results(
    connection: sqlite3.Connection,
    ids: TaskIds,
    result: str,
    error_code: str | None = None,
) -> None:
    values = ("finished", "idle", result)
    connection.execute(
        "UPDATE archive_operations SET lifecycle=?, phase=?, result=?, error_code=?, "
        "speed_bytes_per_second=NULL, eta_seconds=NULL "
        "WHERE operation_id=?",
        (*values, error_code, ids.operation),
    )
    connection.execute(
        "UPDATE source_tasks SET lifecycle=?, phase=?, result=?, error_code=?, "
        "speed_bytes_per_second=NULL, eta_seconds=NULL "
        "WHERE source_task_id=?",
        (*values, error_code, ids.source),
    )
    connection.execute(
        "UPDATE work_tasks SET lifecycle=?, phase=?, result=?, error_code=?, "
        "speed_bytes_per_second=NULL, eta_seconds=NULL "
        "WHERE work_task_id=?",
        (*values, error_code, ids.work),
    )
