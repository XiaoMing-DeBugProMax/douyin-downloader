from dataclasses import replace

import pytest

from douyin_downloader.domain import AppError, ParsedVideo
from douyin_downloader.media import safe_video_filename, validate_media_url
from douyin_downloader.web.routes import content_disposition_filename

VIDEO = ParsedVideo(
    aweme_id="7429378937383308594",
    author="钟哥!!",
    description="#王者荣耀 #王者荣耀热门",
    duration_ms=15279,
    cover_urls=("https://p3.douyinpic.com/cover.jpeg",),
    media_urls=("https://v95-web-sz.douyinvod.com/video.mp4",),
)


def test_filename_removes_windows_illegal_characters() -> None:
    video = replace(VIDEO, description='a/b:c*?"<d>|. ')

    name = safe_video_filename(video)

    assert name.endswith(".mp4")
    assert not any(char in name for char in '<>:"/\\|?*')
    assert len(name.removesuffix(".mp4")) <= 120


def test_content_disposition_uses_meaningful_ascii_fallback() -> None:
    assert 'filename="video.mp4"' in content_disposition_filename("钟哥 - 王者荣耀.mp4")


@pytest.mark.parametrize(
    ("url", "kind"),
    [
        ("https://v95-web-sz.douyinvod.com/video.mp4", "video"),
        ("https://p3.douyinpic.com/cover.jpeg", "cover"),
    ],
)
def test_media_host_allowlist_accepts_trusted_subdomains(url: str, kind: str) -> None:
    assert validate_media_url(url, kind) == url  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "url",
    [
        "http://v95-web-sz.douyinvod.com/video.mp4",
        "https://douyinvod.com/video.mp4",
        "https://evil.douyinvod.com.attacker.test/video.mp4",
        "https://127.0.0.1/secret",
        "https://user@v95-web-sz.douyinvod.com/video.mp4",
        "https://v95-web-sz.douyinvod.com:444/video.mp4",
    ],
)
def test_video_host_allowlist_rejects_untrusted_or_unsafe_urls(url: str) -> None:
    with pytest.raises(AppError):
        validate_media_url(url, "video")
