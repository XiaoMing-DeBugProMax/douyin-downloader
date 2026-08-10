from __future__ import annotations

import sqlite3
from pathlib import Path

_OPERATION_SNAPSHOT_MIGRATIONS = {
    "naming_template": (
        "ALTER TABLE archive_operations ADD COLUMN naming_template "
        "TEXT NOT NULL DEFAULT '{aweme_id}'"
    ),
    "profile_audio": (
        "ALTER TABLE archive_operations ADD COLUMN profile_audio "
        "INTEGER NOT NULL DEFAULT 0"
    ),
    "profile_description": (
        "ALTER TABLE archive_operations ADD COLUMN profile_description "
        "INTEGER NOT NULL DEFAULT 0"
    ),
    "download_concurrency": (
        "ALTER TABLE archive_operations ADD COLUMN download_concurrency "
        "INTEGER NOT NULL DEFAULT 3"
    ),
    "retry_limit": (
        "ALTER TABLE archive_operations ADD COLUMN retry_limit "
        "INTEGER NOT NULL DEFAULT 3"
    ),
}

_TASK_CENTER_MIGRATIONS = (
    (
        "archive_operations",
        "history_visible",
        "ALTER TABLE archive_operations ADD COLUMN history_visible "
        "INTEGER NOT NULL DEFAULT 1",
    ),
    (
        "archive_operations",
        "error_code",
        "ALTER TABLE archive_operations ADD COLUMN error_code TEXT",
    ),
    (
        "source_tasks",
        "error_code",
        "ALTER TABLE source_tasks ADD COLUMN error_code TEXT",
    ),
    (
        "work_tasks",
        "error_code",
        "ALTER TABLE work_tasks ADD COLUMN error_code TEXT",
    ),
    *(
        (
            table,
            "completed_bytes",
            f"ALTER TABLE {table} ADD COLUMN completed_bytes INTEGER NOT NULL DEFAULT 0",
        )
        for table in ("archive_operations", "source_tasks", "work_tasks")
    ),
    *(
        (
            table,
            "total_bytes",
            f"ALTER TABLE {table} ADD COLUMN total_bytes INTEGER",
        )
        for table in ("archive_operations", "source_tasks", "work_tasks")
    ),
    *(
        (
            table,
            "speed_bytes_per_second",
            f"ALTER TABLE {table} ADD COLUMN speed_bytes_per_second REAL",
        )
        for table in ("archive_operations", "source_tasks", "work_tasks")
    ),
    *(
        (
            table,
            "eta_seconds",
            f"ALTER TABLE {table} ADD COLUMN eta_seconds INTEGER",
        )
        for table in ("archive_operations", "source_tasks", "work_tasks")
    ),
)


def ensure_archive_schema(database_path: Path) -> None:
    """Back up an existing database, then apply archive migrations atomically."""

    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path)
    try:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        operation_columns = (
            {
                str(row[1])
                for row in connection.execute(
                    "PRAGMA table_info(archive_operations)"
                )
            }
            if "archive_operations" in tables
            else set()
        )
        missing_operation_migrations = tuple(
            statement
            for column, statement in _OPERATION_SNAPSHOT_MIGRATIONS.items()
            if "archive_operations" in tables and column not in operation_columns
        )
        missing_task_migrations = tuple(
            statement
            for table, column, statement in _TASK_CENTER_MIGRATIONS
            if table in tables
            and column
            not in {
                str(row[1])
                for row in connection.execute(f"PRAGMA table_info({table})")
            }
        )
        needs_settings_table = "current_settings" not in tables
        is_existing_database = bool(tables)
        if is_existing_database and (
            needs_settings_table
            or missing_operation_migrations
            or missing_task_migrations
        ):
            backup_label = (
                "settings-snapshot"
                if needs_settings_table or missing_operation_migrations
                else "task-center"
            )
            _backup_before_migration(database_path, connection, backup_label)

        with connection:
            for statement in missing_operation_migrations:
                connection.execute(statement)
            for statement in missing_task_migrations:
                connection.execute(statement)
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
                VALUES (1, NULL, '{aweme_id}', 0, 0, 3, 3)
                """
            )
    finally:
        connection.close()


def _backup_before_migration(
    database_path: Path,
    connection: sqlite3.Connection,
    label: str,
) -> None:
    connection.commit()
    backup_path = database_path.with_name(
        f"{database_path.stem}.pre-{label}.bak"
    )
    part_path = backup_path.with_name(f"{backup_path.name}.part")
    part_path.unlink(missing_ok=True)
    backup = sqlite3.connect(part_path)
    try:
        try:
            connection.backup(backup)
            backup.commit()
        finally:
            backup.close()
        part_path.replace(backup_path)
    except BaseException:
        part_path.unlink(missing_ok=True)
        raise
