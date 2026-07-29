import hashlib
from pathlib import Path

import httpx
import pytest

from douyin_downloader.domain import ParsedVideo
from scripts.verify_live import (
    download_video,
    matches_expected_sample,
    safe_download_summary,
    safe_summary,
)


def sample_video() -> ParsedVideo:
    return ParsedVideo(
        aweme_id="7429378937383308594",
        author="钟哥！！",
        description="#王者荣耀 #王者荣耀热门",
        duration_ms=15279,
        cover_urls=("https://p3.douyinpic.com/secret-cover.jpeg",),
        media_urls=(
            "https://v95-web-sz.douyinvod.com/secret-video.mp4",
            "https://v11-web.douyinvod.com/secret-video.mp4",
            "https://v3-web.douyinvod.com/secret-video.mp4",
        ),
    )


def test_safe_summary_omits_cookie_and_all_urls() -> None:
    summary = safe_summary(sample_video())

    assert summary == (
        "aweme_id=7429378937383308594 "
        "author=钟哥！！ "
        "description=#王者荣耀 #王者荣耀热门 "
        "duration_ms=15279 "
        "candidates=3"
    )
    assert "https://" not in summary
    assert "cookie" not in summary.lower()
    assert "douyinvod" not in summary


def test_expected_sample_check_rejects_metadata_drift() -> None:
    assert matches_expected_sample(sample_video())

    changed = sample_video()
    changed = ParsedVideo(
        aweme_id=changed.aweme_id,
        author=changed.author,
        description=changed.description,
        duration_ms=changed.duration_ms + 1,
        cover_urls=changed.cover_urls,
        media_urls=changed.media_urls,
    )
    assert not matches_expected_sample(changed)


def test_expected_sample_check_rejects_candidate_count_drift() -> None:
    changed = sample_video()
    changed = ParsedVideo(
        aweme_id=changed.aweme_id,
        author=changed.author,
        description=changed.description,
        duration_ms=changed.duration_ms,
        cover_urls=changed.cover_urls,
        media_urls=changed.media_urls[:2],
    )

    assert not matches_expected_sample(changed)


@pytest.mark.asyncio
async def test_download_video_streams_to_disk_and_reports_no_url(
    tmp_path: Path,
) -> None:
    body = b"\x00\x00\x00\x18ftypmp42" + b"video-data"

    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "video/mp4"}, content=body)

    output_path = tmp_path / "sample.mp4"
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        evidence = await download_video(sample_video(), output_path, client)

    assert output_path.read_bytes() == body
    assert evidence.bytes_streamed == len(body)
    assert evidence.sha256 == hashlib.sha256(body).hexdigest().upper()
    summary = safe_download_summary(evidence)
    assert summary == (
        f"content_type=video/mp4 bytes={len(body)} "
        f"sha256={hashlib.sha256(body).hexdigest().upper()}"
    )
    assert "https://" not in summary
    assert "douyinvod" not in summary


@pytest.mark.asyncio
async def test_download_video_rejects_empty_mp4_response(tmp_path: Path) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "video/mp4"}, content=b"")

    output_path = tmp_path / "empty.mp4"
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError, match="MP4"):
            await download_video(sample_video(), output_path, client)

    assert not output_path.exists()
    assert not output_path.with_suffix(".mp4.part").exists()


@pytest.mark.asyncio
async def test_download_video_rejects_non_mp4_payload_with_video_content_type(
    tmp_path: Path,
) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "video/mp4"},
            content=b"this is not an mp4 file",
        )

    output_path = tmp_path / "fake.mp4"
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError, match="MP4"):
            await download_video(sample_video(), output_path, client)

    assert not output_path.exists()
