from pathlib import Path

import pytest

from douyin_downloader.domain import AppError
from douyin_downloader.settings import (
    ArchiveProfile,
    CurrentSettings,
    OperationSettingsSnapshot,
    SettingsModule,
    SettingsUpdate,
)


def test_defaults_are_stable_and_persist_updates(tmp_path: Path) -> None:
    database_path = tmp_path / "archive.db"
    settings = SettingsModule(database_path)

    assert settings.current() == CurrentSettings(
        archive_root=None,
        naming_template="{aweme_id}",
        profile=ArchiveProfile(),
        download_concurrency=3,
        retry_limit=3,
    )

    updated = settings.update(
        SettingsUpdate(
            naming_template="{date}-{author}-{description}-{aweme_id}",
            profile=ArchiveProfile(include_audio=True),
            download_concurrency=5,
            retry_limit=2,
        )
    )

    assert updated == CurrentSettings(
        archive_root=None,
        naming_template="{date}-{author}-{description}-{aweme_id}",
        profile=ArchiveProfile(include_audio=True),
        download_concurrency=5,
        retry_limit=2,
    )
    assert SettingsModule(database_path).current() == updated


@pytest.mark.parametrize("value", [0, 6, -1, 100])
def test_download_concurrency_outside_one_to_five_is_rejected(
    tmp_path: Path,
    value: int,
) -> None:
    settings = SettingsModule(tmp_path / "archive.db")

    with pytest.raises(AppError) as raised:
        settings.update(
            SettingsUpdate(
                naming_template="{aweme_id}",
                profile=ArchiveProfile(),
                download_concurrency=value,
                retry_limit=3,
            )
        )

    assert raised.value.code == "SETTINGS_INVALID"
    assert "并发" in raised.value.message


@pytest.mark.parametrize("value", [-1, 4, 100])
def test_retry_limit_outside_zero_to_three_is_rejected(
    tmp_path: Path,
    value: int,
) -> None:
    settings = SettingsModule(tmp_path / "archive.db")

    with pytest.raises(AppError) as raised:
        settings.update(
            SettingsUpdate(
                naming_template="{aweme_id}",
                profile=ArchiveProfile(),
                download_concurrency=3,
                retry_limit=value,
            )
        )

    assert raised.value.code == "SETTINGS_INVALID"
    assert "重试" in raised.value.message


@pytest.mark.parametrize(
    "template",
    [
        "{unknown}",
        "{aweme_id!r}",
        "{aweme_id:>10}",
        "../{aweme_id}",
        "folder/{aweme_id}",
        r"folder\{aweme_id}",
        "bad\x00name",
        "x" * 201,
    ],
)
def test_unsafe_or_unsupported_templates_are_rejected(
    tmp_path: Path,
    template: str,
) -> None:
    settings = SettingsModule(tmp_path / "archive.db")

    with pytest.raises(AppError) as raised:
        settings.update(
            SettingsUpdate(
                naming_template=template,
                profile=ArchiveProfile(),
                download_concurrency=3,
                retry_limit=3,
            )
        )

    assert raised.value.code == "SETTINGS_INVALID"
    assert "模板" in raised.value.message


def test_archive_root_must_be_an_existing_absolute_directory(tmp_path: Path) -> None:
    settings = SettingsModule(tmp_path / "archive.db")

    for invalid in (Path("relative"), tmp_path / "missing"):
        with pytest.raises(AppError) as raised:
            settings.set_archive_root(invalid)
        assert raised.value.code == "ARCHIVE_ROOT_INVALID"

    file_path = tmp_path / "file.txt"
    file_path.write_text("not a directory", encoding="utf-8")
    with pytest.raises(AppError) as raised:
        settings.set_archive_root(file_path)
    assert raised.value.code == "ARCHIVE_ROOT_INVALID"


def test_capture_requires_a_root_and_freezes_all_current_values(tmp_path: Path) -> None:
    settings = SettingsModule(tmp_path / "archive.db")

    with pytest.raises(AppError) as raised:
        settings.capture()
    assert raised.value.code == "ARCHIVE_ROOT_REQUIRED"

    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    settings.set_archive_root(archive_root)
    settings.update(
        SettingsUpdate(
            naming_template="{author}-{aweme_id}",
            profile=ArchiveProfile(include_description=True),
            download_concurrency=2,
            retry_limit=1,
        )
    )

    snapshot = settings.capture()
    assert snapshot == OperationSettingsSnapshot(
        archive_root=archive_root.resolve(strict=True),
        naming_template="{author}-{aweme_id}",
        profile=ArchiveProfile(include_description=True),
        download_concurrency=2,
        retry_limit=1,
    )

    settings.update(
        SettingsUpdate(
            naming_template="{aweme_id}",
            profile=ArchiveProfile(),
            download_concurrency=3,
            retry_limit=3,
        )
    )
    assert snapshot.naming_template == "{author}-{aweme_id}"
    assert snapshot.profile.include_description is True
