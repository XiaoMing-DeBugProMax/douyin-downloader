import threading
from pathlib import Path

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from douyin_downloader.domain import ParsedVideo, ResolvedShare
from douyin_downloader.parse_service import ParseService
from douyin_downloader.session import SessionManager
from douyin_downloader.settings import SettingsModule
from douyin_downloader.store import ParseStore
from douyin_downloader.web.app import create_app
from douyin_downloader.web.routes import AppServices


class UnusedResolver:
    async def resolve(self, share_text: str) -> ResolvedShare:
        raise AssertionError(share_text)


class UnusedParser:
    async def parse(self, aweme_id: str) -> ParsedVideo:
        raise AssertionError(aweme_id)


class RecordingDirectoryChooser:
    def __init__(self, selected: Path) -> None:
        self.selected = selected
        self.thread_ids: list[int] = []

    def choose_directory(self) -> Path:
        self.thread_ids.append(threading.get_ident())
        return self.selected


@pytest.mark.asyncio
async def test_settings_routes_persist_values_and_select_root_natively(
    tmp_path: Path,
) -> None:
    settings = SettingsModule(tmp_path / "archive.db")
    archive_root = tmp_path / "library"
    archive_root.mkdir()
    chooser = RecordingDirectoryChooser(archive_root)
    sessions = SessionManager()
    event_loop_thread_id = threading.get_ident()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(404))
    ) as media_client:
        app = create_app(
            services=AppServices(
                parse_service=ParseService(
                    UnusedResolver(),
                    UnusedParser(),
                    ParseStore(),
                ),
                media_client=media_client,
                settings=settings,
                directory_chooser=chooser,
            ),
            session_manager=sessions,
            testing=True,
        )
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            client.cookies.set("douyin_local_session", sessions.cookie_token)
            initial = await client.get("/api/settings")
            updated = await client.put(
                "/api/settings",
                headers={"origin": "http://testserver"},
                json={
                    "naming_template": "{date}-{author}-{aweme_id}",
                    "profile": {
                        "include_audio": True,
                        "include_description": False,
                    },
                    "download_concurrency": 5,
                    "retry_limit": 1,
                },
            )
            selected = await client.post(
                "/api/settings/archive-root/select",
                headers={"origin": "http://testserver"},
            )

    assert initial.status_code == 200
    assert initial.json() == {
        "archive_root": None,
        "naming_template": "{aweme_id}",
        "profile": {
            "include_audio": False,
            "include_description": False,
        },
        "download_concurrency": 3,
        "retry_limit": 3,
    }
    assert updated.status_code == 200
    assert updated.json()["naming_template"] == "{date}-{author}-{aweme_id}"
    assert updated.json()["download_concurrency"] == 5
    assert selected.status_code == 200
    assert selected.json()["archive_root"] == str(archive_root.resolve(strict=True))
    assert chooser.thread_ids != [event_loop_thread_id]
    assert SettingsModule(tmp_path / "archive.db").current().retry_limit == 1


@pytest.mark.asyncio
async def test_settings_mutations_reject_invalid_values_paths_and_cross_origin(
    tmp_path: Path,
) -> None:
    settings = SettingsModule(tmp_path / "archive.db")
    archive_root = tmp_path / "library"
    archive_root.mkdir()
    sessions = SessionManager()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(404))
    ) as media_client:
        app = create_app(
            services=AppServices(
                parse_service=ParseService(
                    UnusedResolver(),
                    UnusedParser(),
                    ParseStore(),
                ),
                media_client=media_client,
                settings=settings,
                directory_chooser=RecordingDirectoryChooser(archive_root),
            ),
            session_manager=sessions,
            testing=True,
        )
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            client.cookies.set("douyin_local_session", sessions.cookie_token)
            invalid = await client.put(
                "/api/settings",
                headers={"origin": "http://testserver"},
                json={
                    "archive_root": str(tmp_path / "forged"),
                    "naming_template": "../{aweme_id}",
                    "profile": {
                        "include_audio": False,
                        "include_description": False,
                    },
                    "download_concurrency": 6,
                    "retry_limit": 3,
                },
            )
            cross_origin = await client.post(
                "/api/settings/archive-root/select",
                headers={"origin": "https://example.com"},
            )

    assert invalid.status_code == 400
    assert invalid.json()["error"]["code"] == "INVALID_INPUT"
    assert "设置" in invalid.json()["error"]["message"]
    assert settings.current().archive_root is None
    assert cross_origin.status_code == 403
