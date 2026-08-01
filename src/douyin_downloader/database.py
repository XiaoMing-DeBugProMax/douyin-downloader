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


def ensure_issue5_schema(database_path: Path) -> None:
    """Back up an existing database, then apply all Issue #5 changes atomically."""

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
        needs_settings_table = "current_settings" not in tables
        is_existing_database = bool(tables)
        if is_existing_database and (
            needs_settings_table or missing_operation_migrations
        ):
            _backup_before_migration(database_path, connection)

        with connection:
            for statement in missing_operation_migrations:
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
) -> None:
    connection.commit()
    backup_path = database_path.with_name(
        f"{database_path.stem}.pre-settings-snapshot.bak"
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
