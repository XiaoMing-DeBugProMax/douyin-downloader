import hashlib
import json
import sqlite3
from datetime import date, datetime
from pathlib import Path

import pytest

from douyin_downloader import archive_paths
from douyin_downloader.archive_store import ArchiveStore
from douyin_downloader.database_recovery import DatabaseRecovery
from douyin_downloader.domain import AppError


def create_database(path: Path, value: str = "safe") -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE sample (value TEXT NOT NULL)")
        connection.execute("INSERT INTO sample VALUES (?)", (value,))
        connection.commit()
    finally:
        connection.close()


def test_daily_backup_runs_once_and_keeps_seven_verified_database_files(
    tmp_path: Path,
) -> None:
    database = tmp_path / "archive.db"
    create_database(database)

    for day in range(1, 9):
        recovery = DatabaseRecovery(database, today=lambda day=day: date(2026, 8, day))
        recovery.prepare_startup()
        recovery.prepare_startup()

    backups = DatabaseRecovery(database).list_valid_backups()

    assert len(backups) == 7
    assert [item.local_date.isoformat() for item in backups] == [
        f"2026-08-{day:02d}" for day in range(8, 1, -1)
    ]
    assert all(item.path.suffix == ".bak" for item in backups)


def test_daily_retention_also_removes_old_invalid_named_backups(tmp_path: Path) -> None:
    database = tmp_path / "archive.db"
    create_database(database)
    recovery = DatabaseRecovery(database, today=lambda: date(2026, 8, 13))
    recovery.backup_directory.mkdir()
    for day in range(1, 8):
        (recovery.backup_directory / f"archive.daily-2026-08-{day:02d}.bak").write_bytes(
            b"invalid"
        )

    recovery.prepare_startup()

    names = sorted(path.name for path in recovery.backup_directory.iterdir())
    assert len(names) == 7
    assert "archive.daily-2026-08-01.bak" not in names


def test_invalid_or_sensitive_backup_never_enters_recovery_list(tmp_path: Path) -> None:
    database = tmp_path / "archive.db"
    create_database(database)
    recovery = DatabaseRecovery(database, today=lambda: date(2026, 8, 13))
    recovery.prepare_startup()
    (recovery.backup_directory / "archive.daily-2026-08-12.bak").write_bytes(b"broken")
    sensitive = recovery.backup_directory / "archive.daily-2026-08-11.bak"
    create_database(sensitive, "authorization: secret")

    backups = recovery.list_valid_backups()

    assert [item.local_date for item in backups] == [date(2026, 8, 13)]


def test_verified_pre_migration_backup_is_identifiable_and_recoverable(
    tmp_path: Path,
) -> None:
    database = tmp_path / "archive.db"
    connection = sqlite3.connect(database)
    try:
        connection.execute("CREATE TABLE legacy (value TEXT NOT NULL)")
        connection.execute("INSERT INTO legacy VALUES ('before migration')")
        connection.commit()
    finally:
        connection.close()
    from douyin_downloader.database import _backup_before_migration

    connection = sqlite3.connect(database)
    try:
        _backup_before_migration(database, connection, "task-center")
    finally:
        connection.close()

    backups = DatabaseRecovery(database).list_valid_backups()

    assert [(item.name, item.kind) for item in backups] == [
        ("archive.pre-task-center.bak", "migration")
    ]


def test_sensitive_pre_migration_backup_is_never_published(tmp_path: Path) -> None:
    database = tmp_path / "archive.db"
    create_database(database, "cookie: do-not-persist")
    from douyin_downloader.database import _backup_before_migration

    connection = sqlite3.connect(database)
    try:
        with pytest.raises(sqlite3.DatabaseError):
            _backup_before_migration(database, connection, "task-center")
    finally:
        connection.close()

    assert not (tmp_path / "archive.pre-task-center.bak").exists()
    assert not (tmp_path / "archive.pre-task-center.bak.part").exists()


def test_corrupt_database_is_quarantined_without_creating_an_empty_database(
    tmp_path: Path,
) -> None:
    database = tmp_path / "archive.db"
    database.write_bytes(b"not sqlite")
    recovery = DatabaseRecovery(
        database,
        now=lambda: datetime(2026, 8, 13, 9, 30, 0),
    )

    status = recovery.prepare_startup()

    assert status.state == "recovery_required"
    assert not database.exists()
    assert status.quarantined_path == tmp_path / "archive.corrupt-20260813-093000.db"
    assert status.quarantined_path.read_bytes() == b"not sqlite"

    restarted = DatabaseRecovery(database).prepare_startup()
    assert restarted.state == "recovery_required"
    assert restarted.quarantined_path == status.quarantined_path
    assert not database.exists()


def test_corrupt_database_quarantine_never_overwrites_existing_evidence(
    tmp_path: Path,
) -> None:
    database = tmp_path / "archive.db"
    database.write_bytes(b"new evidence")
    existing = tmp_path / "archive.corrupt-20260813-093000.db"
    existing.write_bytes(b"old evidence")

    status = DatabaseRecovery(
        database,
        now=lambda: datetime(2026, 8, 13, 9, 30, 0),
    ).prepare_startup()

    assert existing.read_bytes() == b"old evidence"
    assert status.quarantined_path == tmp_path / "archive.corrupt-20260813-093000-1.db"
    assert status.quarantined_path.read_bytes() == b"new evidence"


def test_restore_accepts_only_verified_backup_and_rechecks_restored_database(
    tmp_path: Path,
) -> None:
    database = tmp_path / "archive.db"
    create_database(database, "original")
    recovery = DatabaseRecovery(database, today=lambda: date(2026, 8, 13))
    backup = recovery.prepare_startup().backups[0]
    database.unlink()

    restored = recovery.restore(backup.name)

    assert restored.state == "healthy"
    connection = sqlite3.connect(database)
    try:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("SELECT value FROM sample").fetchone() == ("original",)
    finally:
        connection.close()
    with pytest.raises(AppError) as raised:
        recovery.restore("..\\outside.bak")
    assert raised.value.code == "DATABASE_BACKUP_INVALID"


def test_missing_database_with_valid_backup_enters_recovery_instead_of_bootstrapping(
    tmp_path: Path,
) -> None:
    database = tmp_path / "archive.db"
    create_database(database)
    recovery = DatabaseRecovery(database, today=lambda: date(2026, 8, 13))
    recovery.prepare_startup()
    database.unlink()

    restarted = DatabaseRecovery(database).prepare_startup()

    assert restarted.state == "recovery_required"
    assert [backup.name for backup in restarted.backups] == [
        "archive.daily-2026-08-13.bak"
    ]
    assert not database.exists()


def test_restore_refuses_to_overwrite_a_live_database(tmp_path: Path) -> None:
    database = tmp_path / "archive.db"
    create_database(database)
    recovery = DatabaseRecovery(database, today=lambda: date(2026, 8, 13))
    backup = recovery.prepare_startup().backups[0]

    with pytest.raises(AppError) as raised:
        recovery.restore(backup.name)

    assert raised.value.code == "DATABASE_RESTORE_TARGET_EXISTS"


def test_rebuild_is_available_only_when_no_valid_backup_exists(tmp_path: Path) -> None:
    database = tmp_path / "archive.db"
    create_database(database)
    recovery = DatabaseRecovery(database, today=lambda: date(2026, 8, 13))
    recovery.prepare_startup()
    database.unlink()
    root = tmp_path / "library"
    root.mkdir()
    write_rebuildable_archive(root)

    with pytest.raises(AppError) as raised:
        recovery.rebuild_from_metadata(root)

    assert raised.value.code == "DATABASE_REBUILD_BACKUP_AVAILABLE"
    assert not database.exists()


def write_rebuildable_archive(
    root: Path,
    *,
    corrupt_video: bool = False,
    description: str = "safe archive",
    nested_video: bool = False,
) -> Path:
    aweme_id = "7429378937383308594"
    work_directory = root / "author-stable" / "2024" / f"work-{aweme_id}"
    work_directory.mkdir(parents=True)
    video = (
        work_directory / "nested" / f"{aweme_id}.mp4"
        if nested_video
        else work_directory / f"{aweme_id}.mp4"
    )
    cover = work_directory / f"{aweme_id}.cover.png"
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"video-bytes")
    cover.write_bytes(b"cover-bytes")

    def artifact(kind: str, path: Path, mime_type: str) -> dict[str, object]:
        payload = path.read_bytes()
        return {
            "kind": kind,
            "path": path.relative_to(work_directory).as_posix(),
            "size_bytes": len(payload),
            "mime_type": mime_type,
            "sha256": hashlib.sha256(payload).hexdigest(),
        }

    metadata = work_directory / f"{aweme_id}.metadata.json"
    metadata.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_at": "2026-08-13T01:00:00Z",
                "work": {
                    "aweme_id": aweme_id,
                    "content_type": "video",
                    "public_url": f"https://www.douyin.com/video/{aweme_id}",
                    "description": description,
                    "tags": ["archive"],
                    "published_at": 1720000000,
                    "duration_ms": 15000,
                    "author": {"stable_id": "author-stable", "nickname": "Tester"},
                    "public_metrics": {
                        "captured_at": "2026-08-13T01:00:00Z",
                        "likes": 1,
                        "comments": 2,
                        "shares": 3,
                        "collects": 4,
                    },
                    "music": None,
                },
                "discovery": {
                    "source_type": "single_work",
                    "source_id": aweme_id,
                    "operation_id": "original-operation",
                },
                "artifacts": [
                    artifact("video", video, "video/mp4"),
                    artifact("cover", cover, "image/png"),
                ],
            }
        ),
        encoding="utf-8",
    )
    if corrupt_video:
        video.write_bytes(b"changed")
    return metadata


def test_rebuild_recovers_core_library_but_not_task_history(tmp_path: Path) -> None:
    database = tmp_path / "archive.db"
    root = tmp_path / "library"
    root.mkdir()
    write_rebuildable_archive(root)
    recovery = DatabaseRecovery(database)

    result = recovery.rebuild_from_metadata(root)

    assert result.state == "healthy"
    assert result.rebuilt_archives == 1
    assert result.history_recovery == "incomplete"
    stored = ArchiveStore(database).load_archive("7429378937383308594")
    assert stored is not None
    assert stored.root == root.resolve()
    assert stored.relative_directory == Path(
        "author-stable/2024/work-7429378937383308594"
    )
    assert {artifact.kind for artifact in stored.artifacts} == {
        "video",
        "cover",
        "metadata",
    }
    assert ArchiveStore(database).list_task_operations() == ()


def test_rebuild_skips_corrupt_metadata_archives_and_never_publishes_empty_db(
    tmp_path: Path,
) -> None:
    database = tmp_path / "archive.db"
    root = tmp_path / "library"
    root.mkdir()
    write_rebuildable_archive(root, corrupt_video=True)

    with pytest.raises(AppError) as raised:
        DatabaseRecovery(database).rebuild_from_metadata(root)

    assert raised.value.code == "DATABASE_REBUILD_EMPTY"
    assert not database.exists()


@pytest.mark.parametrize(
    "sensitive_value",
    (
        "ttwid=ABCDEFGHIJKLMNOPQRSTUVWXYZ123456",
        "s_v_web_id=ABCDEFGHIJKLMNOPQRSTUVWXYZ123456",
        "launch_token=ABCDEFGHIJKLMNOPQRSTUVWXYZ123456",
        "https://v95-web.douyinvod.com/video.mp4?signature=secret",
        "https://v95-web.bytevcloud.com/video.mp4?unknown=secret",
        "https://v95-web.bytecdn.cn/video.mp4?unknown=secret",
        "https://p3.byteimg.com/cover.png?unknown=secret",
        "https://p3.byteimg.cn/cover.png?unknown=secret",
    ),
)
def test_rebuild_rejects_sensitive_metadata_values(
    tmp_path: Path,
    sensitive_value: str,
) -> None:
    database = tmp_path / "archive.db"
    root = tmp_path / "library"
    root.mkdir()
    write_rebuildable_archive(root, description=sensitive_value)

    with pytest.raises(AppError) as raised:
        DatabaseRecovery(database).rebuild_from_metadata(root)

    assert raised.value.code == "DATABASE_REBUILD_EMPTY"
    assert not database.exists()


def test_rebuild_rejects_an_intermediate_reparse_artifact_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "archive.db"
    root = tmp_path / "library"
    root.mkdir()
    metadata = write_rebuildable_archive(root, nested_video=True)
    nested = metadata.parent / "nested"
    original = archive_paths.is_reparse_point
    monkeypatch.setattr(
        archive_paths,
        "is_reparse_point",
        lambda path: path == nested or original(path),
    )

    with pytest.raises(AppError) as raised:
        DatabaseRecovery(database).rebuild_from_metadata(root)

    assert raised.value.code == "DATABASE_REBUILD_EMPTY"
    assert not database.exists()


def test_rebuild_never_allows_delete_sharing_for_pinned_read_directories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "archive.db"
    root = tmp_path / "library"
    root.mkdir()
    write_rebuildable_archive(root, nested_video=True)
    calls: list[bool] = []
    original = archive_paths.pin_work_directory

    def recording_pin(
        pin_root: Path,
        relative: Path,
        *,
        create: bool,
        share_delete: bool = False,
    ) -> archive_paths.PinnedWorkDirectory:
        calls.append(share_delete)
        return original(
            pin_root,
            relative,
            create=create,
            share_delete=share_delete,
        )

    monkeypatch.setattr(
        "douyin_downloader.database_recovery.pin_work_directory",
        recording_pin,
    )

    result = DatabaseRecovery(database).rebuild_from_metadata(root)

    assert result.rebuilt_archives == 1
    assert calls
    assert not any(calls)


def test_sensitive_session_values_never_enter_backup_list(tmp_path: Path) -> None:
    database = tmp_path / "archive.db"
    create_database(database, "launch_token=ABCDEFGHIJKLMNOPQRSTUVWXYZ123456")
    recovery = DatabaseRecovery(database, today=lambda: date(2026, 8, 13))

    status = recovery.prepare_startup()

    assert status.state == "recovery_required"
    assert status.backups == ()
    assert not database.exists()
