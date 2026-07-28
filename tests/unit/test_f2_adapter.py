import os
import subprocess
import sys
from pathlib import Path

import pytest

from douyin_downloader.domain import AppError, TransientUpstreamError
from douyin_downloader.f2_adapter import (
    F2VideoParser,
    _prevent_f2_default_file_logging,
    map_post_detail,
)


class FakeDetail:
    api_status_code = 0
    aweme_id = "7429378937383308594"
    nickname_raw = "钟哥！！"
    desc_raw = "#王者荣耀 #王者荣耀热门"
    duration = 15279
    cover = "https://p3.douyinpic.com/cover.jpeg"
    video_play_addr = [
        "https://v95-web-sz.douyinvod.com/a.mp4",
        "https://v11-web.douyinvod.com/a.mp4",
    ]
    images: list[str] = []


def test_maps_f2_filter_without_exposing_f2_types() -> None:
    result = map_post_detail(FakeDetail())
    assert result.aweme_id == "7429378937383308594"
    assert result.author == "钟哥！！"
    assert result.description == "#王者荣耀 #王者荣耀热门"
    assert result.duration_ms == 15279
    assert result.cover_urls == ("https://p3.douyinpic.com/cover.jpeg",)
    assert len(result.media_urls) == 2


def test_rejects_non_video_filter() -> None:
    detail = FakeDetail()
    detail.images = ["https://example.invalid/image.jpeg"]
    with pytest.raises(AppError) as error:
        map_post_detail(detail)
    assert error.value.code == "UNSUPPORTED_CONTENT"


def test_rejects_detail_without_video_candidates() -> None:
    detail = FakeDetail()
    detail.video_play_addr = []
    with pytest.raises(AppError) as error:
        map_post_detail(detail)
    assert error.value.code == "UNSUPPORTED_CONTENT"


def test_maps_nonzero_douyin_status_to_transient_failure() -> None:
    detail = FakeDetail()
    detail.api_status_code = 4
    with pytest.raises(TransientUpstreamError, match="Douyin status 4"):
        map_post_detail(detail)


def test_low_level_f2_import_does_not_create_workspace_logs(tmp_path: Path) -> None:
    project_root = Path(__file__).parents[2]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(project_root / "src")
    script = "\n".join(
        [
            "from pathlib import Path",
            "from douyin_downloader.f2_adapter import _prevent_f2_default_file_logging",
            "_prevent_f2_default_file_logging()",
            "from f2.apps.douyin.crawler import DouyinCrawler",
            "from f2.apps.douyin.filter import PostDetailFilter",
            "from f2.apps.douyin.model import PostDetail",
            "from f2.apps.douyin.utils import TokenManager, VerifyFpManager",
            "items = (DouyinCrawler, PostDetailFilter, PostDetail, "
            "TokenManager, VerifyFpManager)",
            "assert all(items)",
            "assert not Path('logs').exists()",
        ]
    )

    result = subprocess.run(  # noqa: S603 - fixed interpreter and static test script
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "logs").exists()


@pytest.mark.asyncio
async def test_guest_token_failure_is_mapped_to_transient_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prevent_f2_default_file_logging()
    from f2.apps.douyin.utils import TokenManager

    def fail_token(_: type[TokenManager]) -> str:
        raise RuntimeError("token failed")

    monkeypatch.setattr(TokenManager, "gen_ttwid", classmethod(fail_token))

    with pytest.raises(TransientUpstreamError, match="RuntimeError"):
        await F2VideoParser().parse("7429378937383308594")


@pytest.mark.asyncio
async def test_parser_uses_one_business_attempt_and_no_transport_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prevent_f2_default_file_logging()
    from f2.apps.douyin import crawler, filter, model, utils

    captured: dict[str, object] = {}

    class FakeCrawler:
        def __init__(self, kwargs: dict[str, object]) -> None:
            self._max_retries = int(kwargs["max_retries"])
            captured["initial_attempts"] = self._max_retries

        @property
        def aclient(self) -> object:
            captured["transport_retries"] = self._max_retries
            return object()

        async def __aenter__(self) -> "FakeCrawler":
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def fetch_post_detail(self, _: object) -> dict[str, object]:
            captured["business_attempts"] = self._max_retries
            captured["actual_attempts"] = sum(1 for _ in range(self._max_retries))
            return {}

    monkeypatch.setattr(crawler, "DouyinCrawler", FakeCrawler)
    monkeypatch.setattr(filter, "PostDetailFilter", lambda _: FakeDetail())
    monkeypatch.setattr(model, "PostDetail", lambda **_: object())
    monkeypatch.setattr(utils.TokenManager, "gen_ttwid", classmethod(lambda _: "guest"))
    monkeypatch.setattr(utils.VerifyFpManager, "gen_s_v_web_id", classmethod(lambda _: "verify"))

    result = await F2VideoParser().parse("7429378937383308594")

    assert result.aweme_id == "7429378937383308594"
    assert captured == {
        "initial_attempts": 0,
        "transport_retries": 0,
        "business_attempts": 1,
        "actual_attempts": 1,
    }
