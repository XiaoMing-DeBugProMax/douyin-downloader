import os
import subprocess
import sys
import threading
from pathlib import Path

import httpx
import pytest

from douyin_downloader.domain import AppError, TransientUpstreamError
from douyin_downloader.f2_adapter import (
    F2VideoParser,
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
            "import sys",
            "from pathlib import Path",
            "from douyin_downloader.f2_adapter import _prevent_f2_default_file_logging",
            "_prevent_f2_default_file_logging()",
            "from f2.utils.abogus import ABogus, BrowserFingerprintGenerator",
            "items = (ABogus, BrowserFingerprintGenerator)",
            "assert all(items)",
            "assert 'browser_cookie3' not in sys.modules",
            "assert 'pythoncom' not in sys.modules",
            "assert 'f2.apps.douyin.model' not in sys.modules",
            "assert 'f2.apps.douyin.crawler' not in sys.modules",
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
    assert "IUnknown" not in result.stderr, result.stderr
    assert not (tmp_path / "logs").exists()


def test_cold_f2_runtime_uses_zero_retry_token_transports(tmp_path: Path) -> None:
    project_root = Path(__file__).parents[2]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(project_root / "src")
    script = "\n".join(
        [
            "import asyncio",
            "import httpx",
            "import sys",
            "import threading",
            "retry_values = []",
            "sync_threads = []",
            "state = {'detail_requests': 0}",
            "main_thread = threading.get_ident()",
            "class RecordingSyncTransport(httpx.BaseTransport):",
            "    def __init__(self, *args, retries=0, **kwargs):",
            "        retry_values.append(retries)",
            "    def handle_request(self, request):",
            "        sync_threads.append(threading.get_ident())",
            "        headers = [",
            "            ('set-cookie', 'msToken=' + 'm' * 120 + '; Path=/'),",
            "            ('set-cookie', 'ttwid=guest-token; Path=/'),",
            "        ]",
            "        return httpx.Response(200, headers=headers, request=request)",
            "class RecordingAsyncTransport(httpx.AsyncBaseTransport):",
            "    def __init__(self, *args, retries=0, **kwargs):",
            "        retry_values.append(retries)",
            "    async def handle_async_request(self, request):",
            "        state['detail_requests'] += 1",
            "        payload = {",
            "            'status_code': 0,",
            "            'aweme_detail': {",
            "                'aweme_id': '7429378937383308594',",
            "                'author': {'nickname': '钟哥！！'},",
            "                'desc': '#王者荣耀 #王者荣耀热门',",
            "                'duration': 15279,",
            "                'images': [],",
            "                'video': {",
            "                    'origin_cover': {'url_list': ['https://safe.invalid/cover']},",
            "                    'bit_rate': [{'play_addr': {'url_list': [",
            "                        'https://safe.invalid/one',",
            "                        'https://safe.invalid/two',",
            "                        'https://safe.invalid/three',",
            "                    ]}}],",
            "                },",
            "            },",
            "        }",
            "        return httpx.Response(200, json=payload, request=request)",
            "httpx.HTTPTransport = RecordingSyncTransport",
            "httpx.AsyncHTTPTransport = RecordingAsyncTransport",
            "from douyin_downloader.f2_adapter import F2VideoParser",
            "video = asyncio.run(F2VideoParser().parse('7429378937383308594'))",
            "assert video.aweme_id == '7429378937383308594'",
            "assert retry_values and all(value == 0 for value in retry_values), "
            "('retry_values', retry_values)",
            "assert sync_threads and all(value != main_thread for value in sync_threads), "
            "('sync_threads', sync_threads)",
            "assert state['detail_requests'] == 1, ('state', state)",
            "assert 'browser_cookie3' not in sys.modules",
            "assert 'pythoncom' not in sys.modules",
            "assert 'f2.apps.douyin.model' not in sys.modules",
            "assert 'f2.apps.douyin.crawler' not in sys.modules",
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
    assert "IUnknown" not in result.stderr, result.stderr
    assert not (tmp_path / "logs").exists()


def test_local_post_detail_request_matches_f2_0017_query_shape() -> None:
    import douyin_downloader.f2_adapter as adapter

    builder = getattr(adapter, "_build_post_detail_params", None)
    assert callable(builder)

    params = builder("7429378937383308594", "guest-ms-token")

    assert params == {
        "device_platform": "webapp",
        "aid": "6383",
        "channel": "channel_pc_web",
        "pc_client_type": 1,
        "publish_video_strategy_type": 2,
        "pc_libra_divert": "Windows",
        "version_code": "290100",
        "version_name": "29.1.0",
        "cookie_enabled": "true",
        "screen_width": 1920,
        "screen_height": 1080,
        "browser_language": "zh-CN",
        "browser_platform": "Win32",
        "browser_name": "Edge",
        "browser_version": "130.0.0.0",
        "browser_online": "true",
        "engine_name": "Blink",
        "engine_version": "130.0.0.0",
        "os_name": "Windows",
        "os_version": "10",
        "cpu_core_num": 12,
        "device_memory": 8,
        "platform": "PC",
        "downlink": 10,
        "effective_type": "4g",
        "round_trip_time": 100,
        "msToken": "guest-ms-token",
        "aweme_id": "7429378937383308594",
    }


@pytest.mark.asyncio
async def test_detail_redirect_is_not_followed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import douyin_downloader.f2_adapter as adapter

    class RedirectTransport(httpx.AsyncBaseTransport):
        def __init__(self) -> None:
            self.requests = 0

        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            self.requests += 1
            return httpx.Response(
                302,
                headers={"Location": "https://redirect.invalid/detail"},
                request=request,
            )

    transport = RedirectTransport()
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **_: transport)
    runtime = adapter._F2Runtime(  # type: ignore[attr-defined]
        signed_endpoint="https://safe.invalid/detail",
        cookie="ttwid=guest; s_v_web_id=verify;",
    )

    async with adapter._PostDetailCrawler(runtime) as crawler:  # type: ignore[attr-defined]
        with pytest.raises(httpx.HTTPStatusError):
            await crawler.fetch_post_detail()

    assert transport.requests == 1


@pytest.mark.asyncio
async def test_guest_token_failure_is_mapped_to_transient_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import douyin_downloader.f2_adapter as adapter

    main_thread = threading.get_ident()
    captured: dict[str, int] = {}

    def fail_token(_: str) -> None:
        captured["thread"] = threading.get_ident()
        raise RuntimeError("token failed")

    monkeypatch.setattr(adapter, "_load_f2_runtime_and_guest", fail_token)

    with pytest.raises(TransientUpstreamError, match="RuntimeError"):
        await F2VideoParser().parse("7429378937383308594")
    assert captured["thread"] != main_thread
