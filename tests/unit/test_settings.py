from pathlib import Path

import pytest

from douyin_downloader.domain import (
    AppError,
    AuthorSnapshot,
    PlaybackSource,
    PublicMetrics,
    ResolvedWork,
    WorkSnapshot,
)
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


def test_snapshot_renders_only_whitelisted_work_fields_into_one_safe_base_name(
    tmp_path: Path,
) -> None:
    resolved = resolved_work(
        author='钟哥<>:"/\\|?* ',
        description=" 一段\x01文案... ",
        published_at=1_720_000_000,
    )
    snapshot = OperationSettingsSnapshot(
        archive_root=tmp_path,
        naming_template="{date}-{author}-{description}-{aweme_id}",
        profile=ArchiveProfile(),
        download_concurrency=3,
        retry_limit=3,
    )

    base_name = snapshot.artifact_base_name(resolved)

    assert base_name.startswith("2024-07-03-")
    assert base_name.endswith("-7429378937383308594")
    assert not set('<>:"/\\|?*').intersection(base_name)
    assert all(ord(character) >= 32 for character in base_name)
    assert base_name == base_name.rstrip(" .")
    assert Path(base_name).parent == Path(".")


@pytest.mark.parametrize(
    ("author", "expected"),
    [
        ("CON", "_CON"),
        ("prn.txt", "_prn.txt"),
        ("COM1", "_COM1"),
        ("Lpt9...", "_Lpt9"),
    ],
)
def test_snapshot_protects_windows_device_names(
    tmp_path: Path,
    author: str,
    expected: str,
) -> None:
    snapshot = OperationSettingsSnapshot(
        archive_root=tmp_path,
        naming_template="{author}",
        profile=ArchiveProfile(),
        download_concurrency=3,
        retry_limit=3,
    )

    assert snapshot.artifact_base_name(resolved_work(author=author)) == expected


def test_snapshot_bounds_dangerous_lengths_and_falls_back_when_render_is_empty(
    tmp_path: Path,
) -> None:
    description_snapshot = OperationSettingsSnapshot(
        archive_root=tmp_path,
        naming_template="{description}",
        profile=ArchiveProfile(),
        download_concurrency=3,
        retry_limit=3,
    )

    long_name = description_snapshot.artifact_base_name(
        resolved_work(description="汉" * 300)
    )
    empty_name = description_snapshot.artifact_base_name(
        resolved_work(description="...   ")
    )

    assert len(long_name) == 120
    assert long_name == "汉" * 120
    assert empty_name == "7429378937383308594"


def resolved_work(
    *,
    author: str = "测试作者",
    description: str = "归档测试作品",
    published_at: int | None = 1_720_000_000,
) -> ResolvedWork:
    return ResolvedWork(
        snapshot=WorkSnapshot(
            aweme_id="7429378937383308594",
            content_type="video",
            public_url="https://www.douyin.com/video/7429378937383308594",
            description=description,
            tags=(),
            published_at=published_at,
            duration_ms=15_000,
            author=AuthorSnapshot("stable-author-id", author),
            music=None,
            public_metrics=PublicMetrics(None, None, None, None),
        ),
        cover_urls=("https://p3.douyinpic.com/cover.png",),
        playback_sources=(
            PlaybackSource(
                bitrate=1,
                gear_name="normal",
                quality_type=1,
                codec="h264",
                width=720,
                height=1280,
                size_bytes=1,
                cdn_mirror_urls=("https://v95-web.douyinvod.com/video.mp4",),
            ),
        ),
    )
