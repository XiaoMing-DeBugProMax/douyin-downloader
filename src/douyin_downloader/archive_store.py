from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


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


@dataclass(frozen=True, slots=True)
class PendingPromotion:
    ids: TaskIds
    aweme_id: str
    root: Path
    relative_directory: Path


class ArchiveStore:
    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path

    def load_completed(self, aweme_id: str) -> StoredArchive | None:
        if not self._database_path.is_file():
            return None
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT o.operation_id, s.source_task_id, w.work_task_id,
                       i.root_path, i.relative_directory
                FROM archive_items AS i
                JOIN archive_operations AS o ON o.operation_id = i.operation_id
                JOIN source_tasks AS s ON s.operation_id = o.operation_id
                JOIN work_tasks AS w ON w.source_task_id = s.source_task_id
                WHERE i.aweme_id = ? AND i.status = 'archived'
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
        )

    def create_running(
        self,
        ids: TaskIds,
        aweme_id: str,
        root: Path,
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO archive_operations VALUES "
                "(?, 'running', 'resolving', 'none', ?)",
                (ids.operation, str(root)),
            )
            connection.execute(
                "INSERT INTO source_tasks VALUES "
                "(?, ?, 'running', 'resolving', 'none')",
                (ids.source, ids.operation),
            )
            connection.execute(
                "INSERT INTO work_tasks VALUES "
                "(?, ?, ?, 'running', 'resolving', 'none')",
                (ids.work, ids.source, aweme_id),
            )

    def prepare_promotion(
        self,
        ids: TaskIds,
        aweme_id: str,
        root: Path,
        relative_directory: Path,
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO archive_items VALUES (?, ?, ?, ?, 'promoting')",
                (aweme_id, ids.operation, str(root), str(relative_directory)),
            )

    def finish_promotion(self, ids: TaskIds, aweme_id: str) -> None:
        with self._connection() as connection:
            connection.execute(
                "UPDATE archive_items SET status='archived' "
                "WHERE aweme_id=? AND operation_id=?",
                (aweme_id, ids.operation),
            )
            _set_task_results(connection, ids, "success")

    def fail(self, ids: TaskIds) -> None:
        try:
            with self._connection() as connection:
                _set_task_results(connection, ids, "failed")
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
            )
            for row in rows
        )

    def discard_promotion(self, promotion: PendingPromotion) -> None:
        with self._connection() as connection:
            connection.execute(
                "DELETE FROM archive_items WHERE aweme_id=? AND operation_id=?",
                (promotion.aweme_id, promotion.ids.operation),
            )
            _set_task_results(connection, promotion.ids, "failed")

    def _connect(self) -> sqlite3.Connection:
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self._database_path)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS archive_operations (
                operation_id TEXT PRIMARY KEY,
                lifecycle TEXT NOT NULL,
                phase TEXT NOT NULL,
                result TEXT NOT NULL,
                root_path TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS source_tasks (
                source_task_id TEXT PRIMARY KEY,
                operation_id TEXT NOT NULL REFERENCES archive_operations(operation_id),
                lifecycle TEXT NOT NULL,
                phase TEXT NOT NULL,
                result TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS work_tasks (
                work_task_id TEXT PRIMARY KEY,
                source_task_id TEXT NOT NULL REFERENCES source_tasks(source_task_id),
                aweme_id TEXT NOT NULL,
                lifecycle TEXT NOT NULL,
                phase TEXT NOT NULL,
                result TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS archive_items (
                aweme_id TEXT PRIMARY KEY,
                operation_id TEXT NOT NULL REFERENCES archive_operations(operation_id),
                root_path TEXT NOT NULL,
                relative_directory TEXT NOT NULL,
                status TEXT NOT NULL
            );
            """
        )
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()


def _set_task_results(
    connection: sqlite3.Connection,
    ids: TaskIds,
    result: str,
) -> None:
    values = ("finished", "idle", result)
    connection.execute(
        "UPDATE archive_operations SET lifecycle=?, phase=?, result=? "
        "WHERE operation_id=?",
        (*values, ids.operation),
    )
    connection.execute(
        "UPDATE source_tasks SET lifecycle=?, phase=?, result=? WHERE source_task_id=?",
        (*values, ids.source),
    )
    connection.execute(
        "UPDATE work_tasks SET lifecycle=?, phase=?, result=? WHERE work_task_id=?",
        (*values, ids.work),
    )
