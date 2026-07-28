from douyin_downloader.domain import ParsedVideo
from scripts.verify_live import matches_expected_sample, safe_summary


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
