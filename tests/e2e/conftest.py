import asyncio
import base64
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import uvicorn
from playwright.sync_api import Page, sync_playwright

from douyin_downloader.domain import AppError, ParsedVideo, ResolvedShare
from douyin_downloader.parse_service import ParseService
from douyin_downloader.session import SessionManager
from douyin_downloader.store import ParseStore
from douyin_downloader.web.app import create_app
from douyin_downloader.web.routes import AppServices

PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "YAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)


class RedactedLaunchURL(str):
    def __repr__(self) -> str:
        return "'<local-app-launch-url>'"


class DeterministicResolver:
    async def resolve(self, share_text: str) -> ResolvedShare:
        await asyncio.sleep(0.15)
        if "unsupported" in share_text:
            raise AppError(
                "UNSUPPORTED_URL",
                "仅支持抖音公开视频链接，请检查后重试。",
                400,
            )
        return ResolvedShare(
            share_text,
            "https://www.douyin.com/video/7429378937383308594",
            "7429378937383308594",
        )


class DeterministicParser:
    async def parse(self, aweme_id: str) -> ParsedVideo:
        return ParsedVideo(
            aweme_id=aweme_id,
            author="钟哥！！",
            description=(
                "#王者荣耀 #王者荣耀热门 "
                "这是一段用于验证窄屏自动换行与长内容布局的公开视频描述"
            ),
            duration_ms=15279,
            cover_urls=("https://p3.douyinpic.com/cover.png",),
            media_urls=("https://v95-web-sz.douyinvod.com/video.mp4",),
        )


def media_transport(request: httpx.Request) -> httpx.Response:
    if request.url.host == "p3.douyinpic.com":
        return httpx.Response(200, headers={"content-type": "image/png"}, content=PNG_BYTES)
    if request.url.host == "v95-web-sz.douyinvod.com":
        return httpx.Response(200, headers={"content-type": "video/mp4"}, content=b"mp4-data")
    return httpx.Response(404)


@pytest.fixture(scope="session")
def _local_app_server() -> Iterator[tuple[str, SessionManager]]:
    sessions = SessionManager()
    media_client = httpx.AsyncClient(transport=httpx.MockTransport(media_transport))
    app = create_app(
        services=AppServices(
            parse_service=ParseService(
                DeterministicResolver(),
                DeterministicParser(),
                ParseStore(),
            ),
            media_client=media_client,
        ),
        session_manager=sessions,
        testing=True,
    )

    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind(("127.0.0.1", 0))
    server_socket.listen(128)
    port = int(server_socket.getsockname()[1])

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None
    thread = threading.Thread(
        target=server.run,
        kwargs={"sockets": [server_socket]},
        name="task-6-e2e-server",
        daemon=True,
    )
    thread_started = False
    try:
        thread.start()
        thread_started = True
        deadline = time.monotonic() + 10
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started, "local E2E server did not start"
        yield f"http://127.0.0.1:{port}", sessions
    finally:
        server.should_exit = True
        if thread_started:
            thread.join(timeout=10)
        server_socket.close()
        asyncio.run(media_client.aclose())
    assert not thread.is_alive(), "local E2E server did not stop"


@pytest.fixture
def local_app_url(_local_app_server: tuple[str, SessionManager]) -> str:
    base_url, sessions = _local_app_server
    launch_token = sessions.issue_launch_token()
    return RedactedLaunchURL(f"{base_url}/?launch_token={launch_token}")


@pytest.fixture
def page() -> Iterator[Page]:
    with sync_playwright() as playwright:
        if Path(playwright.chromium.executable_path).is_file():
            browser = playwright.chromium.launch()
        else:
            browser = playwright.chromium.launch(channel="msedge")
        try:
            page = browser.new_page(accept_downloads=True)
            page.set_default_timeout(5_000)
            yield page
        finally:
            browser.close()
