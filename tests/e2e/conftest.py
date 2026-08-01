import asyncio
import base64
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
import pytest
import uvicorn
from playwright.sync_api import Page, sync_playwright

from douyin_downloader.domain import AppError, ParsedVideo, ResolvedShare
from douyin_downloader.parse_service import ParseService
from douyin_downloader.session import COOKIE_NAME, SessionManager
from douyin_downloader.settings import SettingsModule
from douyin_downloader.store import ParseStore
from douyin_downloader.web.app import create_app
from douyin_downloader.web.routes import AppServices

PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "YAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)


class DeterministicResolver:
    async def resolve(self, share_text: str) -> ResolvedShare:
        await asyncio.sleep(0.15)
        if "unsupported" in share_text:
            raise AppError(
                "UNSUPPORTED_URL",
                "目前只支持抖音公开视频。",
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
                "这是一段用于验证单行隐藏与长内容布局的公开视频描述 "
                "#王者荣耀 #王者荣耀热门"
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


class StaticDirectoryChooser:
    def __init__(self, selected: Path) -> None:
        self._selected = selected

    def choose_directory(self) -> Path:
        return self._selected


@pytest.fixture(scope="session")
def _local_app_server() -> Iterator[tuple[str, SessionManager]]:
    sessions = SessionManager()
    media_client = httpx.AsyncClient(transport=httpx.MockTransport(media_transport))
    runtime_directory = TemporaryDirectory(prefix="douyin-e2e-")
    runtime_path = Path(runtime_directory.name)
    archive_root = runtime_path / "library"
    archive_root.mkdir()
    settings = SettingsModule(runtime_path / "archive.db")

    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind(("127.0.0.1", 0))
    server_socket.listen(128)
    port = int(server_socket.getsockname()[1])

    app = create_app(
        services=AppServices(
            parse_service=ParseService(
                DeterministicResolver(),
                DeterministicParser(),
                ParseStore(),
            ),
            media_client=media_client,
            settings=settings,
            directory_chooser=StaticDirectoryChooser(archive_root),
        ),
        session_manager=sessions,
        expected_port=port,
        testing=True,
    )

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
        runtime_directory.cleanup()
    assert not thread.is_alive(), "local E2E server did not stop"


@pytest.fixture(scope="session")
def local_app_url(_local_app_server: tuple[str, SessionManager]) -> str:
    base_url, _ = _local_app_server
    return f"{base_url}/"


@pytest.fixture
def page(_local_app_server: tuple[str, SessionManager]) -> Iterator[Page]:
    base_url, sessions = _local_app_server
    with sync_playwright() as playwright:
        if Path(playwright.chromium.executable_path).is_file():
            browser = playwright.chromium.launch()
        else:
            browser = playwright.chromium.launch(channel="msedge")
        context = None
        try:
            context = browser.new_context(accept_downloads=True)
            context.add_cookies(
                [
                    {
                        "name": COOKIE_NAME,
                        "value": sessions.cookie_token,
                        "url": f"{base_url}/",
                        "httpOnly": True,
                        "sameSite": "Strict",
                        "secure": False,
                    }
                ]
            )
            page = context.new_page()
            page.set_default_timeout(5_000)
            yield page
        finally:
            if context is not None:
                context.close()
            browser.close()
