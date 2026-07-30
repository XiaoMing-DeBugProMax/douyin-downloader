from dataclasses import asdict

import pytest

from douyin_downloader.domain import (
    AppError,
    AuthorSnapshot,
    MusicSnapshot,
    ParsedVideo,
    PublicMetrics,
    VideoVariant,
    WorkSnapshot,
)
from douyin_downloader.f2_adapter import F2VideoParser, F2WorkAccess


class StaticPostDetail:
    async def fetch(self, aweme_id: str) -> dict[str, object]:
        assert aweme_id == "7429378937383308594"
        return {
            "status_code": 0,
            "cookie": "sessionid=must-not-survive",
            "authorization": "Bearer must-not-survive",
            "aweme_detail": {
                "aweme_id": aweme_id,
                "aweme_type": 0,
                "create_time": 1_720_000_000,
                "desc": "作品文案 #标签",
                "duration": 15_279,
                "images": [],
                "author": {
                    "sec_uid": "MS4wLjABAAAAstable",
                    "uid": "10001",
                    "nickname": "钟哥！！",
                },
                "video": {
                    "origin_cover": {
                        "url_list": [
                            "https://p3.douyinpic.com/origin.jpeg",
                            "https://p9.douyinpic.com/origin.jpeg",
                        ]
                    },
                    "cover": {
                        "url_list": [
                            "https://p3.douyinpic.com/origin.jpeg",
                            "https://p3.douyinpic.com/cover.jpeg",
                        ]
                    },
                    "bit_rate": [
                        {
                            "bit_rate": 900_000,
                            "gear_name": "normal_720",
                            "quality_type": 20,
                            "is_bytevc1": 0,
                            "play_addr": {
                                "url_list": [
                                    "https://v95-web-sz.douyinvod.com/720-a.mp4",
                                    "https://v11-web.douyinvod.com/720-b.mp4",
                                ],
                                "width": 720,
                                "height": 1280,
                                "data_size": 1_000,
                            },
                        },
                        {
                            "bit_rate": 1_800_000,
                            "gear_name": "normal_1080",
                            "quality_type": 40,
                            "is_bytevc1": 1,
                            "play_addr": {
                                "url_list": [
                                    "https://v95-web-sz.douyinvod.com/1080-a.mp4",
                                ],
                                "width": 1080,
                                "height": 1920,
                                "data_size": 2_000,
                            },
                        },
                    ],
                },
                "music": {
                    "id_str": "music-1",
                    "title": "作品原声",
                    "author": "音乐作者",
                    "duration": 16,
                },
                "statistics": {
                    "digg_count": 11,
                    "comment_count": 12,
                    "share_count": 13,
                    "collect_count": 14,
                },
            },
        }


@pytest.mark.asyncio
async def test_work_access_returns_filtered_stable_snapshot() -> None:
    access = F2WorkAccess(StaticPostDetail())

    snapshot = await access.fetch_work("7429378937383308594")

    assert snapshot == WorkSnapshot(
        aweme_id="7429378937383308594",
        content_type="video",
        public_url="https://www.douyin.com/video/7429378937383308594",
        description="作品文案 #标签",
        published_at=1_720_000_000,
        duration_ms=15_279,
        author=AuthorSnapshot(
            stable_id="MS4wLjABAAAAstable",
            nickname="钟哥！！",
        ),
        cover_urls=(
            "https://p3.douyinpic.com/origin.jpeg",
            "https://p9.douyinpic.com/origin.jpeg",
            "https://p3.douyinpic.com/cover.jpeg",
        ),
        video_variants=(
            VideoVariant(
                bitrate=900_000,
                gear_name="normal_720",
                quality_type=20,
                codec="h264",
                width=720,
                height=1280,
                size_bytes=1_000,
                media_urls=(
                    "https://v95-web-sz.douyinvod.com/720-a.mp4",
                    "https://v11-web.douyinvod.com/720-b.mp4",
                ),
            ),
            VideoVariant(
                bitrate=1_800_000,
                gear_name="normal_1080",
                quality_type=40,
                codec="h265",
                width=1080,
                height=1920,
                size_bytes=2_000,
                media_urls=("https://v95-web-sz.douyinvod.com/1080-a.mp4",),
            ),
        ),
        music=MusicSnapshot(
            stable_id="music-1",
            title="作品原声",
            author="音乐作者",
            duration_seconds=16,
        ),
        public_metrics=PublicMetrics(
            likes=11,
            comments=12,
            shares=13,
            collects=14,
        ),
    )


@pytest.mark.asyncio
async def test_snapshot_selects_highest_reliable_variant_without_collapsing_mirrors() -> None:
    snapshot = await F2WorkAccess(StaticPostDetail()).fetch_work("7429378937383308594")

    preferred = snapshot.preferred_video_variant()

    assert preferred == VideoVariant(
        bitrate=1_800_000,
        gear_name="normal_1080",
        quality_type=40,
        codec="h265",
        width=1080,
        height=1920,
        size_bytes=2_000,
        media_urls=("https://v95-web-sz.douyinvod.com/1080-a.mp4",),
    )


@pytest.mark.asyncio
async def test_work_access_filters_secrets_and_unselected_upstream_fields() -> None:
    snapshot = await F2WorkAccess(StaticPostDetail()).fetch_work("7429378937383308594")

    filtered_snapshot = repr(asdict(snapshot))

    assert "must-not-survive" not in filtered_snapshot
    assert "cookie" not in filtered_snapshot
    assert "authorization" not in filtered_snapshot


@pytest.mark.asyncio
async def test_quick_download_can_use_a_replaceable_work_access_without_behavior_change() -> None:
    snapshot = await F2WorkAccess(StaticPostDetail()).fetch_work("7429378937383308594")

    class DeterministicWorkAccess:
        async def fetch_work(self, aweme_id: str) -> WorkSnapshot:
            assert aweme_id == snapshot.aweme_id
            return snapshot

    parsed = await F2VideoParser(DeterministicWorkAccess()).parse(snapshot.aweme_id)

    assert parsed == ParsedVideo(
        aweme_id="7429378937383308594",
        author="钟哥！！",
        description="作品文案 #标签",
        duration_ms=15_279,
        cover_urls=("https://p3.douyinpic.com/origin.jpeg",),
        media_urls=(
            "https://v95-web-sz.douyinvod.com/720-a.mp4",
            "https://v11-web.douyinvod.com/720-b.mp4",
        ),
    )


class StatusPostDetail:
    def __init__(self, status_code: int) -> None:
        self._status_code = status_code

    async def fetch(self, _: str) -> dict[str, object]:
        return {"status_code": self._status_code}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "expected_code", "expected_http_status"),
    [
        (4, "VIDEO_NOT_FOUND", 404),
        (999, "UPSTREAM_BLOCKED", 502),
    ],
)
async def test_work_access_maps_upstream_errors_at_its_public_interface(
    status_code: int,
    expected_code: str,
    expected_http_status: int,
) -> None:
    access = F2WorkAccess(StatusPostDetail(status_code))

    with pytest.raises(AppError) as error:
        await access.fetch_work("7429378937383308594")

    assert error.value.code == expected_code
    assert error.value.status_code == expected_http_status
