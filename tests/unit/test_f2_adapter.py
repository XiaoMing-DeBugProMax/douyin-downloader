import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import httpx
import pytest

from douyin_downloader.domain import AppError
from douyin_downloader.f2_adapter import (
    F2VideoParser,
    map_post_detail,
)
from douyin_downloader.parse_service import ParseService
from douyin_downloader.store import ParseStore


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


def test_maps_known_missing_douyin_status_to_video_not_found() -> None:
    detail = FakeDetail()
    detail.api_status_code = 4
    with pytest.raises(AppError) as error:
        map_post_detail(detail)
    assert error.value.code == "VIDEO_NOT_FOUND"
    assert error.value.status_code == 404


def test_maps_unknown_douyin_status_to_non_retryable_blocked_error() -> None:
    detail = FakeDetail()
    detail.api_status_code = 999
    with pytest.raises(AppError) as error:
        map_post_detail(detail)
    assert error.value.code == "UPSTREAM_BLOCKED"
    assert error.value.status_code == 502


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


def test_cold_f2_runtime_uses_async_zero_retry_token_transports(tmp_path: Path) -> None:
    project_root = Path(__file__).parents[2]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(project_root / "src")
    script = "\n".join(
        [
            "import asyncio",
            "import httpx",
            "import sys",
            "retry_values = []",
            "state = {'guest_requests': 0, 'detail_requests': 0}",
            "class RecordingAsyncTransport(httpx.AsyncBaseTransport):",
            "    def __init__(self, *args, retries=0, **kwargs):",
            "        retry_values.append(retries)",
            "    async def handle_async_request(self, request):",
            "        if request.url.host == 'ttwid.bytedance.com':",
            "            state['guest_requests'] += 1",
            "            return httpx.Response(",
            "                200,",
            "                headers={'set-cookie': 'ttwid=guest-token; Path=/'},",
            "                request=request,",
            "            )",
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
            "httpx.AsyncHTTPTransport = RecordingAsyncTransport",
            "from douyin_downloader.f2_adapter import F2VideoParser",
            "video = asyncio.run(F2VideoParser().parse('7429378937383308594'))",
            "assert video.aweme_id == '7429378937383308594'",
            "assert retry_values and all(value == 0 for value in retry_values), "
            "('retry_values', retry_values)",
            "assert state['guest_requests'] == 1, ('state', state)",
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
        with pytest.raises(AppError) as error:
            await crawler.fetch_post_detail()

    assert error.value.code == "UPSTREAM_BLOCKED"
    assert transport.requests == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "expected_code", "expected_requests"),
    [
        ("redirect", "UPSTREAM_BLOCKED", 1),
        ("not_found", "VIDEO_NOT_FOUND", 1),
        ("rate_limited", "UPSTREAM_BLOCKED", 1),
        ("server_error", "UPSTREAM_BLOCKED", 2),
        ("timeout", "UPSTREAM_TIMEOUT", 2),
        ("connection", "UPSTREAM_BLOCKED", 2),
        ("missing_status", "VIDEO_NOT_FOUND", 1),
        ("blocked_status", "UPSTREAM_BLOCKED", 1),
    ],
)
async def test_parse_service_retries_only_retryable_detail_failures(
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
    expected_code: str,
    expected_requests: int,
) -> None:
    import douyin_downloader.f2_adapter as adapter

    requests = 0

    async def stable_guest(_: str) -> Any:
        return adapter._F2Runtime(  # type: ignore[attr-defined]
            signed_endpoint="https://www.douyin.com/detail",
            cookie="ttwid=guest; s_v_web_id=verify;",
        )

    class ScenarioTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            nonlocal requests
            requests += 1
            if outcome == "redirect":
                return httpx.Response(
                    302,
                    headers={"location": "https://invalid/"},
                    request=request,
                )
            if outcome == "not_found":
                return httpx.Response(404, request=request)
            if outcome == "rate_limited":
                return httpx.Response(429, request=request)
            if outcome == "server_error":
                return httpx.Response(500, request=request)
            if outcome == "timeout":
                raise httpx.ReadTimeout("blocked", request=request)
            if outcome == "connection":
                raise httpx.ConnectError("offline", request=request)
            status = 4 if outcome == "missing_status" else 999
            return httpx.Response(200, json={"status_code": status}, request=request)

    transport = ScenarioTransport()
    monkeypatch.setattr(adapter, "_load_f2_runtime_and_guest", stable_guest)
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **_: transport)
    service = ParseService(
        resolver=_DirectResolver(),
        parser=F2VideoParser(),
        store=ParseStore(),
    )

    with pytest.raises(AppError) as error:
        await service.parse("https://www.douyin.com/video/7429378937383308594")

    assert error.value.code == expected_code
    assert requests == expected_requests


@pytest.mark.asyncio
async def test_malformed_or_signing_failure_is_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import douyin_downloader.f2_adapter as adapter

    attempts = 0

    async def malformed(_: str) -> Any:
        nonlocal attempts
        attempts += 1
        raise ValueError("bad local signing configuration")

    monkeypatch.setattr(adapter, "_load_f2_runtime_and_guest", malformed)
    service = ParseService(_DirectResolver(), F2VideoParser(), ParseStore())

    with pytest.raises(AppError) as error:
        await service.parse("https://www.douyin.com/video/7429378937383308594")

    assert error.value.code == "UPSTREAM_BLOCKED"
    assert attempts == 1


@pytest.mark.asyncio
async def test_guest_registration_404_is_blocked_and_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests = 0

    class MissingGuestTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            nonlocal requests
            requests += 1
            return httpx.Response(404, request=request)

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **_: MissingGuestTransport())
    service = ParseService(_DirectResolver(), F2VideoParser(), ParseStore())

    with pytest.raises(AppError) as error:
        await service.parse("https://www.douyin.com/video/7429378937383308594")

    assert error.value.code == "UPSTREAM_BLOCKED"
    assert requests == 1


class _DirectResolver:
    async def resolve(self, share_text: str) -> Any:
        from douyin_downloader.domain import ResolvedShare

        return ResolvedShare(
            share_text,
            "https://www.douyin.com/video/7429378937383308594",
            "7429378937383308594",
        )


@pytest.mark.asyncio
async def test_cancelling_blocked_guest_registration_leaves_no_worker_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    import douyin_downloader.f2_adapter as adapter

    started = asyncio.Event()
    cancelled = asyncio.Event()
    baseline_threads = {thread.ident for thread in threading.enumerate()}

    class BlockingTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, _: httpx.Request) -> httpx.Response:
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise
            raise AssertionError("blocked request unexpectedly completed")

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **_: BlockingTransport())
    task = asyncio.create_task(adapter.F2VideoParser().parse("7429378937383308594"))
    await asyncio.wait_for(started.wait(), timeout=1)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)

    assert cancelled.is_set()
    assert {thread.ident for thread in threading.enumerate()} == baseline_threads
