import pytest

from douyin_downloader.domain import AppError, ParsedVideo
from douyin_downloader.store import ParseStore

VIDEO = ParsedVideo(
    aweme_id="7429378937383308594",
    author="钟哥!!",
    description="#王者荣耀 #王者荣耀热门",
    duration_ms=15279,
    cover_urls=("https://p3.douyinpic.com/cover.jpeg",),
    media_urls=("https://v95-web-sz.douyinvod.com/video.mp4",),
)


def test_store_expires_and_caps_entries() -> None:
    now = [100.0]
    store = ParseStore(ttl_seconds=600, max_items=1, clock=lambda: now[0])
    first = store.put(VIDEO)
    second = store.put(VIDEO)
    with pytest.raises(AppError):
        store.get(first)
    assert store.get(second) == VIDEO
    now[0] = 701.0
    with pytest.raises(AppError) as error:
        store.get(second)
    assert error.value.code == "PARSE_EXPIRED"
