# Douyin Local Downloader Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Windows 10/11 x64 application that starts by double-click, opens a localhost FastAPI website, parses one authorized public Douyin video, previews its metadata, and streams a watermark-free MP4 to the default or user-selected download location.

**Architecture:** A Tkinter launcher owns a uvicorn server bound only to `127.0.0.1`. The FastAPI modular monolith serves native HTML/CSS/JavaScript, delegates Douyin access to an isolated f2 adapter, keeps parse results in a 10-minute in-memory store, and proxies trusted cover/video CDN responses without exposing raw media URLs.

**Tech Stack:** CPython 3.12, FastAPI 0.140.7, uvicorn 0.51.0, httpx 0.28.1, Pydantic 2.13.4, f2 0.0.1.7, Tkinter, pytest, pytest-asyncio, Playwright, Ruff, mypy, PyInstaller.

## Global Constraints

- The approved specification is `docs/superpowers/specs/2026-07-28-douyin-local-downloader-design.md`; it is the functional source of truth.
- Support Windows 10/11 x64 and current stable Microsoft Edge/Google Chrome.
- Bind only to `127.0.0.1`; never bind to `0.0.0.0`.
- Accept only one public Douyin video at a time; do not add batch, account, history, database, cloud, LAN, or public-server features.
- Do not read browser cookies or Douyin login state. Generate guest identifiers in memory.
- Use f2 0.0.1.7 lower-level `DouyinCrawler`, `PostDetail`, and `PostDetailFilter`; do not invoke `DouyinHandler` or Bark notification behavior.
- Do not return raw cover/video CDN URLs to the browser. Keep them behind random parse tokens with 600-second TTL and a 20-entry maximum.
- Stream media with application chunks no larger than 1 MiB; do not buffer a complete video in Python or a browser Blob.
- A parse attempt times out after 20 seconds. Retry only transient connection, timeout, and upstream 5xx failures once; cap total parse wait at 45 seconds.
- Store writable runtime data only under `%LOCALAPPDATA%\DouyinLocalDownloader`.
- Keep logs free of share text, guest identifiers, launch/session tokens, parse tokens, query strings, and raw media URLs.
- Use Chinese UI/error copy exactly as defined in the approved specification.
- Every task must preserve the user's untracked `1.txt`, `2.txt`, and `AI项目开发总提示词.md`.

## File Map

```text
pyproject.toml                              Build metadata, direct dependencies, tool configuration
README.md                                   Setup, run, test, build, usage, troubleshooting
AGENTS.md                                   Compact project rules and verification commands
src/douyin_downloader/__init__.py           Package version
src/douyin_downloader/__main__.py           `python -m douyin_downloader` entry point
src/douyin_downloader/domain.py             Application data models and typed error contract
src/douyin_downloader/url_resolver.py       Share text extraction and safe redirect resolution
src/douyin_downloader/store.py              10-minute/20-entry in-memory parse store
src/douyin_downloader/f2_adapter.py         Isolated f2 low-level integration
src/douyin_downloader/parse_service.py       Parse orchestration, timeout, retry, response projection
src/douyin_downloader/media.py              CDN validation, safe filenames, streaming generators
src/douyin_downloader/session.py            Launch/session token and same-origin enforcement
src/douyin_downloader/logging_config.py     Redacted rotating application logging
src/douyin_downloader/web/app.py             FastAPI application factory and exception mapping
src/douyin_downloader/web/routes.py          Health, parse, cover, download, launch routes
src/douyin_downloader/web/static/index.html  Confirmed single-page layout
src/douyin_downloader/web/static/styles.css  Three confirmed themes and responsive/accessibility CSS
src/douyin_downloader/web/static/app.js      Parse UI, themes, default/custom downloads, errors
src/douyin_downloader/runtime.py             Runtime file and existing-instance client
src/douyin_downloader/launcher.py            Tkinter window and uvicorn lifecycle
assets/app-icon.svg                          Code-native application/web icon source
scripts/build_icon.py                        Deterministic SVG-equivalent ICO/PNG generator
scripts/build.ps1                            Clean, test, and PyInstaller build entry point
scripts/verify_live.py                       Explicit real-Douyin acceptance check
douyin_downloader.spec                       PyInstaller resource/hidden-import manifest
tests/unit/                                  Deterministic domain/service/security tests
tests/integration/                           ASGI and streaming contract tests
tests/e2e/                                   Browser tests with deterministic parser/media doubles
tests/conftest.py                            Shared fake parser/client/session fixtures
```

---

### Task 1: Runnable Local Web Baseline

**Slice:** Slice 1 — a runnable localhost page and health contract.

**Requirements:** FR-001 startup foundation; NFR Python 3.12, localhost-only application structure.

**Goal:** Establish an installable package with one FastAPI factory, a static page, a health endpoint, and deterministic quality commands.

**Non-goals:** No Douyin requests, session cookie, launcher GUI, parsing, or downloading.

**Dependencies:** None.

**Allowed changes:** `pyproject.toml`, `AGENTS.md`, package skeleton, `tests/unit/test_app_baseline.py`.

**Forbidden changes:** The approved spec and user source documents.

**Interfaces:**

- Produces `create_app(*, services: AppServices | None = None, session_manager: SessionManager | None = None) -> FastAPI`.
- Produces `GET /api/health -> {"app": "douyin-local-downloader", "status": "ok", "instance_id": str}`.
- Later tasks may extend `AppServices`; they must not replace the factory.

- [ ] **Step 1: Write the failing health and home tests**

Create `tests/unit/test_app_baseline.py`:

```python
from httpx import ASGITransport, AsyncClient
import pytest

from douyin_downloader.web.app import create_app


@pytest.mark.asyncio
async def test_health_contract() -> None:
    app = create_app()
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["app"] == "douyin-local-downloader"
    assert response.json()["status"] == "ok"
    assert response.json()["instance_id"]


@pytest.mark.asyncio
async def test_home_serves_local_static_page() -> None:
    app = create_app()
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/")

    assert response.status_code == 200
    assert "抖音视频下载" in response.text
    assert "http://" not in response.text
    assert "https://" not in response.text
```

- [ ] **Step 2: Run the focused test and confirm RED**

Run:

```powershell
python -m pytest tests/unit/test_app_baseline.py -q
```

Expected: collection fails with `ModuleNotFoundError: No module named 'douyin_downloader'`.

- [ ] **Step 3: Add package metadata and the minimal app**

Create `pyproject.toml` with these direct pins and tool settings:

```toml
[build-system]
requires = ["hatchling>=1.27,<2"]
build-backend = "hatchling.build"

[project]
name = "douyin-local-downloader"
version = "0.1.0"
description = "Windows-only localhost downloader for one authorized public Douyin video"
requires-python = ">=3.12,<3.13"
dependencies = [
  "fastapi==0.140.7",
  "uvicorn==0.51.0",
  "httpx==0.28.1",
  "pydantic==2.13.4",
  "f2==0.0.1.7",
]

[project.optional-dependencies]
dev = [
  "pytest>=9.1,<10",
  "pytest-asyncio>=1.2,<2",
  "playwright>=1.55,<2",
  "ruff>=0.14,<1",
  "mypy>=1.18,<2",
  "pyinstaller>=6.16,<7",
  "pillow>=12,<13",
]

[project.scripts]
douyin-local-downloader = "douyin_downloader.launcher:main"

[tool.hatch.build.targets.wheel]
packages = ["src/douyin_downloader"]

[tool.hatch.build]
include = ["src/douyin_downloader/web/static/**"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
markers = ["live: explicit real-network Douyin verification"]

[tool.ruff]
target-version = "py312"
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "ASYNC", "S"]
ignore = ["S101"]

[tool.mypy]
python_version = "3.12"
strict = true
packages = ["douyin_downloader"]
```

Create `src/douyin_downloader/__init__.py`:

```python
__version__ = "0.1.0"
```

Create `src/douyin_downloader/web/app.py`:

```python
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI
from fastapi.responses import FileResponse


STATIC_DIR = Path(__file__).with_name("static")


def create_app(*, services: object | None = None, session_manager: object | None = None) -> FastAPI:
    app = FastAPI(title="抖音视频下载", docs_url=None, redoc_url=None)
    app.state.services = services
    app.state.session_manager = session_manager
    app.state.instance_id = uuid4().hex

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {
            "app": "douyin-local-downloader",
            "status": "ok",
            "instance_id": app.state.instance_id,
        }

    @app.get("/", include_in_schema=False)
    async def home() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    return app
```

Create `src/douyin_downloader/web/static/index.html`:

```html
<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>抖音视频下载</title>
  </head>
  <body>
    <main>
      <h1>抖音视频下载</h1>
      <p>本地运行，仅支持你有权下载的单个公开视频。</p>
    </main>
  </body>
</html>
```

Create `AGENTS.md`:

```markdown
# Project Rules

- Product scope is defined by `docs/superpowers/specs/2026-07-28-douyin-local-downloader-design.md`.
- Keep the server bound to `127.0.0.1`; never add LAN/public binding.
- Never read browser/Douyin login cookies or log secrets/media URLs.
- Preserve `1.txt`, `2.txt`, and `AI项目开发总提示词.md`.
- Focused test: `python -m pytest <test-file> -q`
- Full gate: `python -m pytest -q && python -m ruff check . && python -m mypy src`
- Build: `powershell -ExecutionPolicy Bypass -File scripts/build.ps1`
```

- [ ] **Step 4: Install editable dependencies and confirm GREEN**

Run:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest tests/unit/test_app_baseline.py -q
```

Expected: `2 passed`.

- [ ] **Step 5: Run baseline quality checks**

Run:

```powershell
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m mypy src
git diff --check
```

Expected: all commands exit 0.

- [ ] **Step 6: Commit the baseline**

```powershell
git add pyproject.toml AGENTS.md src tests/unit/test_app_baseline.py
git commit -m "feat: add runnable localhost web baseline"
```

**Done when:** The editable package installs, both tests pass, and the health/static contracts are stable.

**Risk and rollback:** Dependency incompatibility is the only material risk. Roll back this single commit; no persistent data exists.

---

### Task 2: Protected Local Browser Session

**Slice:** Slice 1 — secure localhost entry before adding valuable endpoints.

**Requirements:** FR-008 and FR-001 launch-link portion.

**Goal:** Prevent arbitrary webpages or unauthenticated local callers from using parse/media endpoints.

**Non-goals:** No Douyin parsing, media, runtime file, or duplicate-process handling.

**Dependencies:** Task 1.

**Allowed changes:** `session.py`, `web/app.py`, `web/routes.py`, session tests.

**Forbidden changes:** Open CORS, public binding, reusable query-string session tokens.

**Interfaces:**

- Produces `SessionManager.issue_launch_token() -> str`.
- Produces `require_local_session(request: Request) -> None`.
- Produces `require_same_origin(request: Request) -> None`.
- Sets cookie name `douyin_local_session`.

- [ ] **Step 1: Write failing session tests**

Create `tests/unit/test_session.py`:

```python
from douyin_downloader.session import SessionManager


def test_launch_token_is_single_use() -> None:
    manager = SessionManager()
    token = manager.issue_launch_token()
    assert manager.consume_launch_token(token) is True
    assert manager.consume_launch_token(token) is False


def test_cookie_comparison_accepts_only_current_session() -> None:
    manager = SessionManager()
    assert manager.valid_cookie(manager.cookie_token)
    assert not manager.valid_cookie("wrong")
```

Create `tests/integration/test_session_contract.py`:

```python
from httpx import ASGITransport, AsyncClient
import pytest

from douyin_downloader.session import SessionManager
from douyin_downloader.web.app import create_app


@pytest.mark.asyncio
async def test_launch_sets_http_only_cookie_and_redirects_clean_url() -> None:
    sessions = SessionManager()
    token = sessions.issue_launch_token()
    app = create_app(session_manager=sessions)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        follow_redirects=False,
    ) as client:
        response = await client.get("/", params={"launch_token": token})

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "SameSite=strict" in cookie
    assert token not in response.headers["location"]
```

- [ ] **Step 2: Run and confirm RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_session.py tests/integration/test_session_contract.py -q
```

Expected: import failure for `douyin_downloader.session`.

- [ ] **Step 3: Implement the session manager and dependencies**

Create `src/douyin_downloader/session.py`:

```python
from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from fastapi import HTTPException, Request


COOKIE_NAME = "douyin_local_session"


@dataclass(slots=True)
class SessionManager:
    cookie_token: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    management_token: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    _launch_tokens: set[str] = field(default_factory=set)

    def issue_launch_token(self) -> str:
        token = secrets.token_urlsafe(32)
        self._launch_tokens.add(token)
        return token

    def consume_launch_token(self, token: str) -> bool:
        for pending in tuple(self._launch_tokens):
            if secrets.compare_digest(pending, token):
                self._launch_tokens.remove(pending)
                return True
        return False

    def valid_cookie(self, candidate: str | None) -> bool:
        return candidate is not None and secrets.compare_digest(candidate, self.cookie_token)


def require_local_session(request: Request) -> None:
    manager: SessionManager = request.app.state.session_manager
    if not manager.valid_cookie(request.cookies.get(COOKIE_NAME)):
        raise HTTPException(status_code=403, detail="LOCAL_SESSION_REQUIRED")


def require_same_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin is None:
        raise HTTPException(status_code=403, detail="SAME_ORIGIN_REQUIRED")
    parsed = urlsplit(origin)
    if parsed.scheme != request.url.scheme or parsed.netloc != request.url.netloc:
        raise HTTPException(status_code=403, detail="SAME_ORIGIN_REQUIRED")
```

Modify the root route in `web/app.py` so a valid launch token sets the cookie:

```python
from fastapi import Query, Request
from fastapi.responses import FileResponse, RedirectResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from douyin_downloader.session import COOKIE_NAME, SessionManager

# Inside create_app:
sessions = session_manager if isinstance(session_manager, SessionManager) else SessionManager()
app.state.session_manager = sessions
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["127.0.0.1", "localhost", "testserver"],
)

@app.get("/", include_in_schema=False)
async def home(
    request: Request,
    launch_token: str | None = Query(default=None),
):
    if launch_token is not None:
        if not sessions.consume_launch_token(launch_token):
            raise HTTPException(status_code=403, detail="INVALID_LAUNCH_TOKEN")
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(
            COOKIE_NAME,
            sessions.cookie_token,
            httponly=True,
            samesite="strict",
            secure=False,
            path="/",
        )
        return response
    return FileResponse(STATIC_DIR / "index.html")
```

- [ ] **Step 4: Verify session behavior**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_session.py tests/integration/test_session_contract.py -q
.\.venv\Scripts\python.exe -m pytest tests/unit/test_app_baseline.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```powershell
git add src/douyin_downloader/session.py src/douyin_downloader/web/app.py tests
git commit -m "feat: protect localhost browser sessions"
```

**Done when:** Launch tokens are single-use, cookie attributes are correct, and invalid tokens cannot establish a session.

**Risk and rollback:** Cookie mistakes can lock out the owner. Roll back this commit; the health endpoint remains available for diagnosis.

---

### Task 3: Parse-and-Preview Vertical Slice

**Slice:** Slice 2 — paste share text and receive a preview through a deterministic parser.

**Requirements:** FR-002, FR-003, FR-004, parse error contract, TTL/capacity requirements.

**Goal:** Deliver the full browser-to-service parse contract with safe URL resolution, memory storage, retry semantics, preview JSON, and a minimal page wired to it.

**Non-goals:** The production f2 adapter, cover bytes, MP4 download, final colors, launcher, or packaging.

**Dependencies:** Tasks 1–2.

**Allowed changes:** Domain, resolver, store, parse service, routes, static page/JS, parse tests.

**Forbidden changes:** Returning raw media URLs, following redirects automatically, reading browser cookies.

**Interfaces:**

- `ShareResolver.resolve(share_text: str) -> ResolvedShare`
- `VideoParser.parse(aweme_id: str) -> ParsedVideo`
- `ParseStore.put(video: ParsedVideo) -> str`
- `ParseStore.get(parse_token: str) -> ParsedVideo`
- `ParseService.parse(share_text: str) -> ParseResult`
- `POST /api/parse` request `{"share_text": str}` and approved response contract.

- [ ] **Step 1: Write failing domain, resolver, store, and API tests**

Create `tests/unit/test_url_resolver.py`:

```python
import httpx
import pytest

from douyin_downloader.domain import AppError
from douyin_downloader.url_resolver import ShareResolver, extract_share_url


def test_extracts_first_url_from_share_text() -> None:
    text = "复制打开抖音 https://v.douyin.com/96C_V98aPlc/ 06/09"
    assert extract_share_url(text) == "https://v.douyin.com/96C_V98aPlc/"


@pytest.mark.parametrize("value", ["", "x" * 2001, "https://example.com/a"])
def test_rejects_invalid_input(value: str) -> None:
    with pytest.raises(AppError) as error:
        extract_share_url(value)
    assert error.value.code in {"INVALID_INPUT", "UNSUPPORTED_URL"}


@pytest.mark.asyncio
async def test_resolves_short_link_without_automatic_redirects() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "v.douyin.com"
        return httpx.Response(
            302,
            headers={
                "location": "https://www.iesdouyin.com/share/video/7429378937383308594/"
            },
        )

    resolver = ShareResolver(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    result = await resolver.resolve("https://v.douyin.com/96C_V98aPlc/")
    assert result.aweme_id == "7429378937383308594"
```

Create `tests/unit/test_store.py`:

```python
import pytest

from douyin_downloader.domain import AppError, ParsedVideo
from douyin_downloader.store import ParseStore


VIDEO = ParsedVideo(
    aweme_id="7429378937383308594",
    author="钟哥！！",
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
```

Create `tests/integration/test_parse_api.py` with a fake resolver/parser:

```python
from httpx import ASGITransport, AsyncClient
import pytest

from douyin_downloader.domain import ParsedVideo, ResolvedShare
from douyin_downloader.parse_service import ParseService
from douyin_downloader.session import SessionManager
from douyin_downloader.store import ParseStore
from douyin_downloader.web.app import create_app
from douyin_downloader.web.routes import AppServices


class FakeResolver:
    async def resolve(self, share_text: str) -> ResolvedShare:
        return ResolvedShare(share_text, "https://www.douyin.com/video/7429378937383308594", "7429378937383308594")


class FakeParser:
    async def parse(self, aweme_id: str) -> ParsedVideo:
        return ParsedVideo(
            aweme_id=aweme_id,
            author="钟哥！！",
            description="#王者荣耀 #王者荣耀热门",
            duration_ms=15279,
            cover_urls=("https://p3.douyinpic.com/cover.jpeg",),
            media_urls=("https://v95-web-sz.douyinvod.com/video.mp4",),
        )


@pytest.mark.asyncio
async def test_parse_returns_public_projection_not_media_urls() -> None:
    sessions = SessionManager()
    app = create_app(
        services=AppServices(
            parse_service=ParseService(FakeResolver(), FakeParser(), ParseStore())
        ),
        session_manager=sessions,
    )
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        client.cookies.set("douyin_local_session", sessions.cookie_token)
        response = await client.post(
            "/api/parse",
            headers={"origin": "http://testserver"},
            json={"share_text": "https://v.douyin.com/example/"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["video"]["author"] == "钟哥！！"
    assert payload["video"]["cover_url"].startswith("/api/cover/")
    assert "media_urls" not in response.text
    assert "douyinvod.com" not in response.text
```

- [ ] **Step 2: Run the tests and confirm RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_url_resolver.py tests/unit/test_store.py tests/integration/test_parse_api.py -q
```

Expected: imports fail for the new modules and interfaces.

- [ ] **Step 3: Implement typed domain and error contracts**

Create `src/douyin_downloader/domain.py`:

```python
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ResolvedShare:
    source_url: str
    final_url: str
    aweme_id: str


@dataclass(frozen=True, slots=True)
class ParsedVideo:
    aweme_id: str
    author: str
    description: str
    duration_ms: int
    cover_urls: tuple[str, ...]
    media_urls: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ParseResult:
    parse_token: str
    video: ParsedVideo


class AppError(Exception):
    def __init__(self, code: str, message: str, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class TransientUpstreamError(Exception):
    pass
```

- [ ] **Step 4: Implement safe URL resolution**

Create `src/douyin_downloader/url_resolver.py`:

```python
import re
from urllib.parse import urljoin, urlsplit

import httpx

from douyin_downloader.domain import AppError, ResolvedShare, TransientUpstreamError


ENTRY_HOSTS = frozenset({
    "douyin.com",
    "www.douyin.com",
    "v.douyin.com",
    "iesdouyin.com",
    "www.iesdouyin.com",
})
URL_PATTERN = re.compile(r"https?://[^\s<>\"']+")
VIDEO_PATTERN = re.compile(r"/(?:share/)?video/(\d+)")
TRAILING_PUNCTUATION = ".,;:!?)]}，。！？；：、）】》〉」』”’"


def _validated_url(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in ENTRY_HOSTS:
        raise AppError("UNSUPPORTED_URL", "目前只支持抖音公开视频。", 400)
    if parsed.username or parsed.password:
        raise AppError("UNSUPPORTED_URL", "目前只支持抖音公开视频。", 400)
    try:
        port = parsed.port
    except ValueError as error:
        raise AppError("UNSUPPORTED_URL", "目前只支持抖音公开视频。", 400) from error
    if port not in {None, 80, 443}:
        raise AppError("UNSUPPORTED_URL", "目前只支持抖音公开视频。", 400)
    return url


def extract_share_url(text: str) -> str:
    if not 1 <= len(text) <= 2000:
        raise AppError("INVALID_INPUT", "没有识别到抖音链接，请粘贴完整分享文案。", 400)
    match = URL_PATTERN.search(text)
    if match is None:
        raise AppError("INVALID_INPUT", "没有识别到抖音链接，请粘贴完整分享文案。", 400)
    return _validated_url(match.group(0).rstrip(TRAILING_PUNCTUATION))


class ShareResolver:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def resolve(self, share_text: str) -> ResolvedShare:
        source = extract_share_url(share_text)
        current = source
        for _ in range(5):
            match = VIDEO_PATTERN.search(urlsplit(current).path)
            if match:
                return ResolvedShare(source, current, match.group(1))
            try:
                response = await self._client.get(
                    current,
                    follow_redirects=False,
                    headers={"User-Agent": "Mozilla/5.0", "Accept": "text/html,*/*"},
                )
            except httpx.HTTPError as error:
                raise TransientUpstreamError(type(error).__name__) from error
            if response.status_code >= 500:
                raise TransientUpstreamError(f"HTTP {response.status_code}")
            if response.status_code == 429:
                raise AppError(
                    "UPSTREAM_BLOCKED",
                    "解析服务暂时不可用，请稍后重试。",
                    502,
                )
            if response.status_code not in {301, 302, 303, 307, 308}:
                break
            location = response.headers.get("location")
            if not location:
                break
            current = _validated_url(urljoin(current, location))
        match = VIDEO_PATTERN.search(urlsplit(current).path)
        if match:
            return ResolvedShare(source, current, match.group(1))
        raise AppError("VIDEO_NOT_FOUND", "没有找到公开视频，请检查作品是否存在。", 404)
```

- [ ] **Step 5: Implement store and parse orchestration**

Create `src/douyin_downloader/store.py`:

```python
import secrets
import time
from collections import OrderedDict
from collections.abc import Callable

from douyin_downloader.domain import AppError, ParsedVideo


class ParseStore:
    def __init__(
        self,
        ttl_seconds: int = 600,
        max_items: int = 20,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl = ttl_seconds
        self._max_items = max_items
        self._clock = clock
        self._items: OrderedDict[str, tuple[float, ParsedVideo]] = OrderedDict()

    def put(self, video: ParsedVideo) -> str:
        self._purge()
        while len(self._items) >= self._max_items:
            self._items.popitem(last=False)
        token = secrets.token_urlsafe(32)
        self._items[token] = (self._clock() + self._ttl, video)
        return token

    def get(self, token: str) -> ParsedVideo:
        item = self._items.get(token)
        if item is None or item[0] <= self._clock():
            self._items.pop(token, None)
            raise AppError("PARSE_EXPIRED", "解析结果已过期，请重新解析。", 410)
        return item[1]

    def _purge(self) -> None:
        now = self._clock()
        for token, (expires_at, _) in tuple(self._items.items()):
            if expires_at <= now:
                del self._items[token]
```

Create `src/douyin_downloader/parse_service.py`:

```python
import asyncio
from typing import Protocol

from douyin_downloader.domain import (
    AppError,
    ParseResult,
    ParsedVideo,
    ResolvedShare,
    TransientUpstreamError,
)
from douyin_downloader.store import ParseStore


class Resolver(Protocol):
    async def resolve(self, share_text: str) -> ResolvedShare: ...


class VideoParser(Protocol):
    async def parse(self, aweme_id: str) -> ParsedVideo: ...


class ParseService:
    def __init__(self, resolver: Resolver, parser: VideoParser, store: ParseStore) -> None:
        self._resolver = resolver
        self._parser = parser
        self.store = store

    async def parse(self, share_text: str) -> ParseResult:
        for attempt in range(2):
            try:
                async with asyncio.timeout(20):
                    resolved = await self._resolver.resolve(share_text)
                    video = await self._parser.parse(resolved.aweme_id)
                return ParseResult(self.store.put(video), video)
            except (TimeoutError, TransientUpstreamError) as error:
                if attempt == 1:
                    code = "UPSTREAM_TIMEOUT" if isinstance(error, TimeoutError) else "UPSTREAM_BLOCKED"
                    status = 504 if code == "UPSTREAM_TIMEOUT" else 502
                    raise AppError(code, "解析服务暂时不可用，请稍后重试。", status) from error
                await asyncio.sleep(0.25)
        raise AssertionError("retry loop must return or raise")
```

- [ ] **Step 6: Add the parse API and minimal browser interaction**

Create `src/douyin_downloader/web/routes.py` with Pydantic request/response schemas and route factory:

```python
from dataclasses import dataclass

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from douyin_downloader.parse_service import ParseService
from douyin_downloader.session import require_local_session, require_same_origin


@dataclass(slots=True)
class AppServices:
    parse_service: ParseService


class ParseRequest(BaseModel):
    share_text: str = Field(min_length=1, max_length=2000)


class VideoResponse(BaseModel):
    aweme_id: str
    author: str
    description: str
    duration_ms: int
    cover_url: str


class ParseResponse(BaseModel):
    parse_token: str
    video: VideoResponse


def build_router() -> APIRouter:
    router = APIRouter()

    @router.post(
        "/api/parse",
        response_model=ParseResponse,
        dependencies=[Depends(require_local_session), Depends(require_same_origin)],
    )
    async def parse_video(payload: ParseRequest, request: Request) -> ParseResponse:
        services: AppServices = request.app.state.services
        result = await services.parse_service.parse(payload.share_text)
        return ParseResponse(
            parse_token=result.parse_token,
            video=VideoResponse(
                aweme_id=result.video.aweme_id,
                author=result.video.author,
                description=result.video.description,
                duration_ms=result.video.duration_ms,
                cover_url=f"/api/cover/{result.parse_token}",
            ),
        )

    return router
```

Move `AppServices` import into `web/app.py`, include the router, mount `/assets`, and add an `AppError` handler returning:

```python
return JSONResponse(
    status_code=error.status_code,
    content={"error": {"code": error.code, "message": error.message}},
)
```

Add a `RequestValidationError` handler that maps invalid `ParseRequest` payloads to
HTTP 400 with error code `INVALID_INPUT` and message
`没有识别到抖音链接，请粘贴完整分享文案。`. Extend the API integration test with
empty and 2001-character bodies so FastAPI's default 422 response cannot regress the
approved contract.

Replace `index.html` with a form containing `#share-text`, `#parse-button`, `#status`,
`#error`, and hidden `#result`; create `static/app.js` that posts JSON to `/api/parse`,
renders text with `textContent`, never `innerHTML`, and disables the button while the
request is active. Define these shared helpers before later download code uses them:

```javascript
function showNotice(message) {
  document.querySelector("#status").textContent = message;
  document.querySelector("#error").textContent = "";
}

function showError(message) {
  document.querySelector("#error").textContent = message;
  document.querySelector("#status").textContent = "";
}

async function toAppError(response) {
  let payload = null;
  try { payload = await response.json(); } catch (_) { payload = null; }
  const error = new Error(
    payload?.error?.message || "解析服务暂时不可用，请稍后重试。"
  );
  error.code = payload?.error?.code || "UNKNOWN";
  return error;
}

function normalizeError(error) {
  return error instanceof Error && error.message
    ? error.message
    : "解析服务暂时不可用，请稍后重试。";
}
```

- [ ] **Step 7: Verify the slice**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_url_resolver.py tests/unit/test_store.py tests/integration/test_parse_api.py -q
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m mypy src
```

Expected: focused tests pass and quality checks exit 0.

- [ ] **Step 8: Commit**

```powershell
git add src tests
git commit -m "feat: add protected parse preview slice"
```

**Done when:** A deterministic fake parser can drive the real page/API to a safe preview, with no media URL exposure.

**Risk and rollback:** Redirect handling is security-sensitive. Any host-validation test failure blocks progress; rollback the task commit.

---

### Task 4: Real Self-Hosted f2 Adapter

**Slice:** Slice 2 — replace the deterministic parser with the validated real parser.

**Requirements:** FR-003; no login Cookie; no Bark; no f2-created workspace logs.

**Goal:** Parse the approved public sample using f2 lower-level APIs and map it into `ParsedVideo`.

**Non-goals:** Download bytes or use any public third-party parsing service.

**Dependencies:** Task 3.

**Allowed changes:** `f2_adapter.py`, application composition, f2 unit tests, explicit metadata live test.

**Forbidden changes:** `DouyinHandler`, browser-cookie libraries, Bark endpoints, logging guest/media secrets.

**Interfaces:**

- Produces `F2VideoParser.parse(aweme_id: str) -> ParsedVideo`.
- Keeps `VideoParser` protocol unchanged.

- [ ] **Step 1: Write failing adapter mapping and no-log tests**

Create `tests/unit/test_f2_adapter.py`:

```python
import pytest

from douyin_downloader.f2_adapter import map_post_detail


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
    assert result.duration_ms == 15279
    assert len(result.media_urls) == 2


def test_rejects_non_video_filter() -> None:
    detail = FakeDetail()
    detail.images = ["https://example.invalid/image.jpeg"]
    with pytest.raises(Exception) as error:
        map_post_detail(detail)
    assert getattr(error.value, "code") == "UNSUPPORTED_CONTENT"
```

- [ ] **Step 2: Run and confirm RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_f2_adapter.py -q
```

Expected: import failure for `f2_adapter`.

- [ ] **Step 3: Implement lazy, low-level f2 integration**

Create `src/douyin_downloader/f2_adapter.py`:

```python
from __future__ import annotations

import logging
from typing import Any

from douyin_downloader.domain import AppError, ParsedVideo, TransientUpstreamError


def _prevent_f2_default_file_logging() -> None:
    for name in ("f2", "f2-trace"):
        logger = logging.getLogger(name)
        if not logger.handlers:
            logger.addHandler(logging.NullHandler())
        logger.propagate = False


def map_post_detail(detail: Any) -> ParsedVideo:
    if detail.api_status_code != 0:
        raise TransientUpstreamError(f"Douyin status {detail.api_status_code}")
    if detail.images:
        raise AppError("UNSUPPORTED_CONTENT", "当前版本不支持图集或直播内容。", 422)
    media_urls = tuple(detail.video_play_addr or ())
    if not media_urls:
        raise AppError("UNSUPPORTED_CONTENT", "当前版本只支持公开视频。", 422)
    cover_urls = (detail.cover,) if detail.cover else ()
    return ParsedVideo(
        aweme_id=str(detail.aweme_id),
        author=str(detail.nickname_raw or ""),
        description=str(detail.desc_raw or ""),
        duration_ms=int(detail.duration or 0),
        cover_urls=cover_urls,
        media_urls=media_urls,
    )


class F2VideoParser:
    async def parse(self, aweme_id: str) -> ParsedVideo:
        _prevent_f2_default_file_logging()
        from f2.apps.douyin.crawler import DouyinCrawler
        from f2.apps.douyin.filter import PostDetailFilter
        from f2.apps.douyin.model import PostDetail
        from f2.apps.douyin.utils import TokenManager, VerifyFpManager

        user_agent = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/130.0.0.0 Safari/537.36"
        )
        cookie = (
            f"ttwid={TokenManager.gen_ttwid()}; "
            f"s_v_web_id={VerifyFpManager.gen_s_v_web_id()};"
        )
        kwargs = {
            "headers": {"User-Agent": user_agent, "Referer": "https://www.douyin.com/"},
            "cookie": cookie,
            "proxies": {"http://": None, "https://": None},
            "timeout": 20,
            "max_retries": 0,
        }
        try:
            async with DouyinCrawler(kwargs) as crawler:
                payload = await crawler.fetch_post_detail(PostDetail(aweme_id=aweme_id))
        except Exception as error:
            raise TransientUpstreamError(type(error).__name__) from error
        return map_post_detail(PostDetailFilter(payload))
```

Compose `F2VideoParser`, one shared `httpx.AsyncClient`, `ShareResolver`, and `ParseStore` in an application lifespan. Close the shared client on shutdown.

- [ ] **Step 4: Add an explicit live metadata verifier**

Create `scripts/verify_live.py` with a required `--url` argument. It must call `ShareResolver` and `F2VideoParser`, print only safe fields (`aweme_id`, author, description, duration, candidate count), and exit nonzero if the expected sample metadata differs. It must not print cookies or URLs.

Run:

```powershell
.\.venv\Scripts\python.exe scripts/verify_live.py --url "https://v.douyin.com/96C_V98aPlc/"
```

Expected safe output includes:

```text
PASS aweme_id=7429378937383308594 author=钟哥！！ duration_ms=15279 candidates=3
```

- [ ] **Step 5: Run focused and regression tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_f2_adapter.py tests/integration/test_parse_api.py -q
.\.venv\Scripts\python.exe -m ruff check src tests scripts
.\.venv\Scripts\python.exe -m mypy src
git diff --check
```

Expected: all deterministic tests pass; live verification is recorded separately because external state can change.

- [ ] **Step 6: Commit**

```powershell
git add src/douyin_downloader/f2_adapter.py src/douyin_downloader/web scripts/verify_live.py tests
git commit -m "feat: integrate self-hosted Douyin parser"
```

**Done when:** The real sample metadata passes without public parser APIs, browser cookies, Bark calls, or new `./logs` files.

**Risk and rollback:** f2 is the highest-volatility dependency. Keep the protocol and revert only this adapter commit if upstream behavior changes.

---

### Task 5: Trusted Cover and MP4 Streaming Downloads

**Slice:** Slice 3 — preview cover, default download, and custom save location.

**Requirements:** FR-005, FR-006, cover proxy, CDN allowlist, streaming/resource rules.

**Goal:** Stream trusted CDN content through protected local routes with safe filenames and browser save options.

**Non-goals:** Resume/range orchestration, transcoding, media cache, or download history.

**Dependencies:** Tasks 3–4.

**Allowed changes:** `media.py`, routes, parse store access, download UI, streaming tests.

**Forbidden changes:** Full-body buffering, arbitrary URL proxying, raw URL responses.

**Interfaces:**

- `validate_media_url(url: str, kind: Literal["cover", "video"]) -> str`
- `safe_video_filename(video: ParsedVideo) -> str`
- `open_upstream(client, url, kind) -> UpstreamStream`
- `open_first_available(client, urls, kind) -> UpstreamStream`
- `UpstreamStream.iter_bytes() -> AsyncIterator[bytes]`
- Protected `GET /api/cover/{parse_token}` and `GET /api/download/{parse_token}`.

- [ ] **Step 1: Write failing filename, CDN, and streaming tests**

Create `tests/unit/test_media.py`:

```python
from dataclasses import replace

import pytest

from douyin_downloader.domain import AppError
from douyin_downloader.media import safe_video_filename, validate_media_url
from douyin_downloader.domain import ParsedVideo


VIDEO = ParsedVideo(
    aweme_id="7429378937383308594",
    author="钟哥！！",
    description="#王者荣耀 #王者荣耀热门",
    duration_ms=15279,
    cover_urls=("https://p3.douyinpic.com/cover.jpeg",),
    media_urls=("https://v95-web-sz.douyinvod.com/video.mp4",),
)


def test_filename_removes_windows_illegal_characters() -> None:
    video = replace(VIDEO, description='a/b:c*?"<d>|. ')
    name = safe_video_filename(video)
    assert name.endswith(".mp4")
    assert not any(char in name for char in '<>:"/\\|?*')
    assert len(name.removesuffix(".mp4")) <= 120


def test_video_host_allowlist() -> None:
    assert validate_media_url(
        "https://v95-web-sz.douyinvod.com/video.mp4", "video"
    ).startswith("https://")
    with pytest.raises(AppError):
        validate_media_url("https://127.0.0.1/secret", "video")
    with pytest.raises(AppError):
        validate_media_url("https://evil.example/video.mp4", "video")
```

Create `tests/integration/test_download_api.py` using `httpx.MockTransport` to return a small `video/mp4` body. Assert 200, `Content-Disposition`, exact bytes, protected session, expired-token 410, and that a disconnected iterator closes the upstream response.

- [ ] **Step 2: Run and confirm RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_media.py tests/integration/test_download_api.py -q
```

Expected: import/route failures.

- [ ] **Step 3: Implement media validation, naming, and streaming**

Create `src/douyin_downloader/media.py`:

```python
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

import httpx

from douyin_downloader.domain import AppError, ParsedVideo


VIDEO_SUFFIXES = (".douyinvod.com", ".bytevcloud.com", ".bytecdn.cn")
COVER_SUFFIXES = (".douyinpic.com", ".byteimg.com", ".byteimg.cn")
INVALID_WINDOWS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _host_matches(host: str, suffixes: tuple[str, ...]) -> bool:
    return any(host.endswith(suffix) and host != suffix[1:] for suffix in suffixes)


def validate_media_url(url: str, kind: Literal["cover", "video"]) -> str:
    parsed = urlsplit(url)
    suffixes = VIDEO_SUFFIXES if kind == "video" else COVER_SUFFIXES
    if parsed.scheme != "https" or parsed.hostname is None or not _host_matches(parsed.hostname, suffixes):
        raise AppError("DOWNLOAD_FAILED", "视频地址无效，请重新解析。", 502)
    try:
        port = parsed.port
    except ValueError as error:
        raise AppError("DOWNLOAD_FAILED", "视频地址无效，请重新解析。", 502) from error
    if parsed.username or parsed.password or port not in {None, 443}:
        raise AppError("DOWNLOAD_FAILED", "视频地址无效，请重新解析。", 502)
    return url


def safe_video_filename(video: ParsedVideo) -> str:
    base = f"{video.author} - {video.description or video.aweme_id}"
    base = INVALID_WINDOWS.sub("", base).strip(" .")[:120].rstrip(" .")
    return f"{base or video.aweme_id}.mp4"


@dataclass(slots=True)
class UpstreamStream:
    response: httpx.Response
    chunk_size: int = 256 * 1024

    async def iter_bytes(self) -> AsyncIterator[bytes]:
        try:
            async for chunk in self.response.aiter_bytes(self.chunk_size):
                if chunk:
                    yield chunk
        finally:
            await self.response.aclose()


async def open_upstream(
    client: httpx.AsyncClient,
    url: str,
    kind: Literal["cover", "video"],
) -> UpstreamStream:
    request = client.build_request(
        "GET",
        validate_media_url(url, kind),
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.douyin.com/"},
    )
    try:
        response = await client.send(request, stream=True, follow_redirects=False)
    except httpx.HTTPError as error:
        raise AppError("DOWNLOAD_FAILED", "下载中断，请重试。", 502) from error
    expected = "video/mp4" if kind == "video" else "image/"
    content_type = response.headers.get("content-type", "").lower()
    if not response.is_success or not content_type.startswith(expected):
        await response.aclose()
        raise AppError("DOWNLOAD_FAILED", "下载地址已失效，请重新解析。", 502)
    return UpstreamStream(response)


async def open_first_available(
    client: httpx.AsyncClient,
    urls: tuple[str, ...],
    kind: Literal["cover", "video"],
) -> UpstreamStream:
    last_error: AppError | None = None
    for url in urls:
        try:
            return await open_upstream(client, url, kind)
        except AppError as error:
            last_error = error
    if last_error is not None:
        raise last_error
    raise AppError("DOWNLOAD_FAILED", "下载地址已失效，请重新解析。", 502)
```

- [ ] **Step 4: Add protected cover/download routes**

Use the parse service store to fetch the token. Validate the selected URL immediately
before opening it, then try candidate URLs in order with `open_upstream`. Close every
failed response before trying the next candidate; raise `DOWNLOAD_FAILED` only after all
candidates fail. Add
`open_first_available(client, urls, kind) -> UpstreamStream` and a test where the first
CDN candidate fails but the second succeeds. Return:

```python
upstream = await open_first_available(
    services.media_client,
    video.media_urls,
    "video",
)
StreamingResponse(
    upstream.iter_bytes(),
    media_type="video/mp4",
    headers={
        "Content-Disposition": content_disposition_filename(
            safe_video_filename(video)
        ),
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
    },
)
```

Implement RFC 5987 `filename*` encoding with `urllib.parse.quote`, plus an ASCII fallback. Cover responses use the first validated cover candidate and `Cache-Control: private, max-age=300`.

Extend `AppServices` to:

```python
@dataclass(slots=True)
class AppServices:
    parse_service: ParseService
    media_client: httpx.AsyncClient
```

Update the earlier parse API fixture to pass its deterministic `httpx.AsyncClient`; close
that client in fixture teardown.

- [ ] **Step 5: Wire both download buttons without Blob buffering**

In `static/app.js`, default download navigates to `/api/download/{token}`. Custom save uses:

```javascript
async function saveToChosenLocation(token, suggestedName) {
  if (!window.showSaveFilePicker) {
    showNotice("当前浏览器不支持选择保存位置，将使用默认下载方式。");
    window.location.assign(`/api/download/${encodeURIComponent(token)}`);
    return;
  }
  try {
    const handle = await window.showSaveFilePicker({
      suggestedName,
      types: [{description: "MP4 视频", accept: {"video/mp4": [".mp4"]}}],
    });
    const response = await fetch(`/api/download/${encodeURIComponent(token)}`);
    if (!response.ok || !response.body) throw await toAppError(response);
    const writable = await handle.createWritable();
    await response.body.pipeTo(writable);
    showNotice("视频已保存。");
  } catch (error) {
    if (error && error.name === "AbortError") return;
    showError(normalizeError(error));
  }
}
```

The server must include the sanitized suggested filename in parse response as `suggested_filename`; update the response schema and contract test.

- [ ] **Step 6: Verify streaming and regression**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_media.py tests/integration/test_download_api.py tests/integration/test_parse_api.py -q
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m mypy src
```

Expected: all tests pass. Inspect the test to ensure it would fail if the route returned an arbitrary host or omitted attachment headers.

- [ ] **Step 7: Commit**

```powershell
git add src tests
git commit -m "feat: stream trusted cover and video downloads"
```

**Done when:** Both download paths use the protected stream and no code calls `.read()`/`.content` for full video bodies.

**Risk and rollback:** CDN allowlists may be too narrow. Add hosts only from observed valid Douyin responses and with focused tests; revert this commit if proxy safety cannot be proven.

---

### Task 6: Confirmed UI, Themes, Errors, and Browser E2E

**Slice:** Slice 4 — polished, accessible user journey.

**Requirements:** FR-004, FR-006, FR-007, approved A layout, all Chinese error states.

**Goal:** Reproduce the approved single-page design with three persistent themes, responsive behavior, accessible interactions, and deterministic browser tests.

**Non-goals:** UI frameworks, analytics, remote assets, history, or extra pages.

**Dependencies:** Tasks 3 and 5.

**Allowed changes:** Static HTML/CSS/JS and E2E tests.

**Forbidden changes:** React/Vue, Node build tooling, remote fonts/icons/scripts, unsafe `innerHTML`.

**Interfaces:**

- DOM IDs: `share-text`, `parse-button`, `status`, `result`, `cover`, `author`, `description`, `duration`, `download-default`, `download-custom`, `parse-another`, `theme-button`, `theme-menu`.
- Theme storage key: `douyin-local-theme`.
- Theme values: `light`, `dark`, `calm`.

- [ ] **Step 1: Write failing Playwright tests**

Create `tests/e2e/conftest.py` with a session-scoped `local_app_url` fixture. The fixture
starts uvicorn on a pre-bound `127.0.0.1` socket, injects deterministic resolver/parser and
`httpx.MockTransport` media services, issues a launch token, and yields the complete launch
URL. In teardown it sets `server.should_exit`, joins the thread, closes the socket/client,
and asserts the thread stopped.

Create `tests/e2e/test_user_journey.py` and assert:

```python
def test_parse_preview_theme_and_download(page, local_app_url):
    page.goto(local_app_url)
    page.locator("#share-text").fill("https://v.douyin.com/example/")
    page.locator("#parse-button").click()
    page.locator("#result").wait_for(state="visible")
    assert page.locator("#author").inner_text() == "钟哥！！"
    page.locator("#theme-button").click()
    page.locator('[data-theme="dark"]').click()
    assert page.locator("html").get_attribute("data-theme") == "dark"
    page.reload()
    assert page.locator("html").get_attribute("data-theme") == "dark"


def test_duplicate_parse_click_has_one_active_request(page, local_app_url):
    page.goto(local_app_url)
    page.locator("#share-text").fill("https://v.douyin.com/example/")
    page.locator("#parse-button").dblclick()
    assert page.locator("#parse-button").is_disabled()
    page.locator("#result").wait_for(state="visible")
    assert page.locator("#result").count() == 1
```

Add tests for empty/unsupported input copy, custom picker cancellation by stubbing `showSaveFilePicker`, fallback when the API is absent, keyboard focus visibility, and a 390×844 viewport.

- [ ] **Step 2: Run and confirm RED**

```powershell
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe -m pytest tests/e2e/test_user_journey.py -q
```

Expected: selectors/themes are missing.

- [ ] **Step 3: Implement the approved semantic HTML**

`index.html` must contain a skip link, header with local-only label and theme menu, one `<form>`, live status (`role="status"`), alert area (`role="alert"`), and result card. The legal-use note must read:

```html
<p class="usage-note">仅下载你拥有权利或已获授权的公开视频。</p>
```

Every button must have visible Chinese text or an `aria-label`. The theme button label is `切换颜色主题`.

- [ ] **Step 4: Implement the three design-token themes**

In `styles.css`, define shared variables and exactly three theme overrides:

```css
:root,
:root[data-theme="light"] {
  --page: #f4f6f9;
  --panel: #ffffff;
  --panel-soft: #ffffff;
  --line: #e7e9ee;
  --text: #16181d;
  --muted: #6f7682;
  --accent: #ff2c55;
  --accent-text: #ffffff;
}
:root[data-theme="dark"] {
  --page: #080a0f;
  --panel: #11141b;
  --panel-soft: #151922;
  --line: #2f3541;
  --text: #f7f8fb;
  --muted: #a8afbc;
  --accent: #25f4ee;
  --accent-text: #071012;
}
:root[data-theme="calm"] {
  --page: #eef2f7;
  --panel: #f9fbfd;
  --panel-soft: #ffffff;
  --line: #d5dde8;
  --text: #243047;
  --muted: #667286;
  --accent: #3667d6;
  --accent-text: #ffffff;
}
```

Add a maximum content width of 760px, single-column input-first hierarchy, responsive result stacking below 620px, `:focus-visible` outlines, reduced-motion support, and WCAG AA contrast.

- [ ] **Step 5: Implement safe state/error/theme behavior**

Use `textContent` for all server data. Store only the theme string in `localStorage`; never store share text, metadata, or parse tokens. Map known server errors to their returned Chinese messages, and use `解析服务暂时不可用，请稍后重试。` for unknown failures. Keep the valid parse token in a module-scoped variable only.

- [ ] **Step 6: Verify screenshots and E2E**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/e2e/test_user_journey.py -q
```

Then capture screenshots for 1440×900 and 390×844 in all three themes. Manually inspect:

- no horizontal scroll;
- menu stays within viewport;
- focus indicator is visible;
- result controls remain reachable;
- author/description overflow wraps without breaking layout.

Expected: E2E passes and six screenshots have no layout defects.

- [ ] **Step 7: Commit**

```powershell
git add src/douyin_downloader/web/static tests/e2e
git commit -m "feat: add confirmed themes and accessible download UI"
```

**Done when:** All three visual themes, persistence, error states, responsive layout, and browser journey pass.

**Risk and rollback:** Theme polish can hide contrast or focus regressions. Block completion on visual/accessibility failures; revert the UI commit without changing APIs.

---

### Task 7: Windows Control Window and Single-Instance Lifecycle

**Slice:** Slice 5 — double-click lifecycle.

**Requirements:** FR-001, runtime file, reopen, graceful stop, duplicate-start behavior.

**Goal:** Start uvicorn on a pre-bound loopback socket, display the confirmed small Tkinter control window, reopen the browser, and stop cleanly.

**Non-goals:** Tray icon, installer, auto-update, background service, or registry writes.

**Dependencies:** Tasks 1–6.

**Allowed changes:** `runtime.py`, `launcher.py`, `__main__.py`, internal launch-token route, lifecycle tests.

**Forbidden changes:** Hidden orphan process, public binding, killing unrelated processes, broad temp-directory cleanup.

**Interfaces:**

- `RuntimeInfo(instance_id, base_url, management_token, pid)`.
- `RuntimeStore.read()`, `write(info)`, `remove_if_owned(instance_id)`.
- `LocalServer.start() -> RunningServer`; `RunningServer.stop()`.
- Internal `POST /api/internal/launch-token` requires exact management token.

- [ ] **Step 1: Write failing runtime and lifecycle tests**

Create `tests/unit/test_runtime.py` to verify atomic JSON write/read, malformed/stale file handling, current-user local app-data path, and owned removal.

Create `tests/integration/test_launcher_lifecycle.py`:

```python
def test_server_binds_loopback_and_stops_cleanly(running_server):
    assert running_server.host == "127.0.0.1"
    response = httpx.get(f"{running_server.base_url}/api/health")
    assert response.status_code == 200
    running_server.stop()
    with pytest.raises(httpx.ConnectError):
        httpx.get(f"{running_server.base_url}/api/health", timeout=0.5)
```

Add a fake-browser test asserting a second invocation requests a new launch token from the existing instance, opens one URL, and does not start another uvicorn thread.

- [ ] **Step 2: Run and confirm RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_runtime.py tests/integration/test_launcher_lifecycle.py -q
```

Expected: runtime/launcher imports fail.

- [ ] **Step 3: Implement runtime state and internal token issuance**

`runtime.py` uses `%LOCALAPPDATA%\DouyinLocalDownloader\runtime.json`, writes to a sibling `.tmp`, then `Path.replace()` for atomic replacement. It catches JSON/OS errors as “no active instance” and never deletes paths outside the exact application directory.

Add:

```python
@router.post("/api/internal/launch-token")
async def issue_launch_token(request: Request) -> dict[str, str]:
    supplied = request.headers.get("x-management-token")
    sessions: SessionManager = request.app.state.session_manager
    if supplied is None or not secrets.compare_digest(supplied, sessions.management_token):
        raise HTTPException(status_code=403)
    return {"launch_token": sessions.issue_launch_token()}
```

Do not enable CORS for this endpoint.

- [ ] **Step 4: Implement pre-bound server lifecycle**

In `launcher.py`, bind the socket before starting the thread to avoid a free-port race:

```python
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.bind(("127.0.0.1", 0))
sock.listen(2048)
port = int(sock.getsockname()[1])
config = uvicorn.Config(app, log_config=None, access_log=False)
server = uvicorn.Server(config)
thread = threading.Thread(
    target=server.run,
    kwargs={"sockets": [sock]},
    name="local-fastapi",
    daemon=False,
)
thread.start()
```

Wait at most 5 seconds for `server.started`. On failure, close the socket, stop the thread, show a Chinese error dialog, and exit nonzero. `stop()` sets `server.should_exit = True`, joins for 5 seconds, closes resources, and removes only the owned runtime file.

- [ ] **Step 5: Implement the control window**

Create a fixed-size Tkinter window with:

- title `抖音视频下载`;
- status text `本地服务运行中`;
- local address displayed read-only;
- `重新打开网页` calling `SessionManager.issue_launch_token()` and `webbrowser.open()`;
- `停止并退出` calling the same idempotent shutdown as `WM_DELETE_WINDOW`.

Create `src/douyin_downloader/__main__.py`:

```python
from douyin_downloader.launcher import main

raise SystemExit(main())
```

- [ ] **Step 6: Verify lifecycle**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_runtime.py tests/integration/test_launcher_lifecycle.py -q
.\.venv\Scripts\python.exe -m douyin_downloader
```

Manual check: browser opens, control window buttons work, closing the window makes `/api/health` unreachable, and Task Manager shows no leftover process.

- [ ] **Step 7: Commit**

```powershell
git add src tests
git commit -m "feat: add Windows control window lifecycle"
```

**Done when:** One visible control window owns one localhost server, repeat launch reopens the existing page, and exit leaves no process/port.

**Risk and rollback:** Thread/socket shutdown can leak resources. A lifecycle test failure blocks packaging; revert the launcher commit.

---

### Task 8: Redacted Logging, Packaging, Real Acceptance, and Handoff

**Slice:** Slice 5 — production engineering for the personal MVP.

**Requirements:** Logging/privacy, PyInstaller packaging, executable UAT, full quality gate.

**Goal:** Produce a reproducible `dist\抖音视频下载.exe`, documentation, and evidence for the real user journey.

**Non-goals:** Installer, code signing, auto-update, CI service, GitHub publishing, or public release.

**Dependencies:** Tasks 1–7.

**Allowed changes:** Logging config, build assets/scripts/spec, README, verification reports.

**Forbidden changes:** Embedding secrets/sample media, committing generated `dist/`, modifying user research inputs.

**Interfaces:**

- `configure_logging() -> logging.Logger` writes sanitized rotating logs.
- `scripts/build.ps1` is the single build entry point.
- `scripts/verify_live.py --url ... --download` verifies the approved sample without printing URLs/tokens.

- [ ] **Step 1: Write failing redaction and packaged-resource tests**

Create `tests/unit/test_logging_config.py` asserting that a log call containing fields named `share_text`, `cookie`, `launch_token`, `parse_token`, and `media_url` emits `[REDACTED]` values and rotates at 1 MiB with five backups.

Create `tests/integration/test_packaged_resources.py` asserting `index.html`, `styles.css`, `app.js`, and icon-resource resolution works both from source and when `sys._MEIPASS` is monkeypatched.

- [ ] **Step 2: Run and confirm RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_logging_config.py tests/integration/test_packaged_resources.py -q
```

Expected: logging/resource modules are absent.

- [ ] **Step 3: Implement redacted rotating logs**

Create `logging_config.py` with a `RotatingFileHandler(maxBytes=1_048_576, backupCount=5, encoding="utf-8")`. Apply a filter that replaces values for the exact sensitive field names with `[REDACTED]`. Disable uvicorn access logs and preinstall `NullHandler` for `f2`/`f2-trace` before importing f2. Log only operation, anonymous error code, elapsed milliseconds, and streamed byte count.

- [ ] **Step 4: Add deterministic icons and PyInstaller manifest**

Create `assets/app-icon.svg` as a simple rounded-square download-arrow mark using only vector paths. `scripts/build_icon.py` draws the same geometry with Pillow and emits `assets/app-icon.ico` plus `src/douyin_downloader/web/static/app-icon.png`.

Create `douyin_downloader.spec` that:

- uses `src/douyin_downloader/__main__.py`;
- includes the entire `web/static` directory and generated icons;
- collects f2 package data and required hidden imports;
- uses `console=False`;
- names the output `抖音视频下载`;
- excludes tests, `.superpowers`, docs, sample text files, and logs.

- [ ] **Step 5: Create the single build script**

Create `scripts/build.ps1`:

```powershell
$ErrorActionPreference = 'Stop'
$python = Join-Path $PSScriptRoot '..\\.venv\\Scripts\\python.exe'
& $python -m pytest -q
& $python -m ruff check src tests scripts
& $python -m mypy src
& $python (Join-Path $PSScriptRoot 'build_icon.py')
& $python -m PyInstaller --clean --noconfirm (Join-Path $PSScriptRoot '..\\douyin_downloader.spec')
Get-FileHash -Algorithm SHA256 (Join-Path $PSScriptRoot '..\\dist\\抖音视频下载.exe')
```

Any failing command must stop the build.

- [ ] **Step 6: Write user documentation**

Create `README.md` with:

- one-sentence scope and authorization notice;
- double-click usage;
- default browser download behavior and “选择保存位置”;
- control-window buttons and exit behavior;
- source setup/run commands;
- focused/full test commands;
- build command;
- log/runtime locations;
- troubleshooting for parser changes, browser picker support, and antivirus false positives;
- explicit non-goals.

- [ ] **Step 7: Run the full automated gate**

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src tests scripts
.\.venv\Scripts\python.exe -m mypy src
powershell -ExecutionPolicy Bypass -File scripts/build.ps1
git diff --check
```

Expected: tests, lint, typing, build, and diff check pass; an EXE and SHA-256 are printed.

- [ ] **Step 8: Run packaged executable UAT**

On `dist\抖音视频下载.exe`:

1. Double-click and confirm control window appears within 5 seconds.
2. Confirm the browser URL is loopback-only.
3. Paste the supplied share text and parse three consecutive times.
4. Confirm author, description, and 15279 ms duration.
5. Download once through the browser default path.
6. Download once through “选择保存位置” to a user-selected test folder.
7. Verify each file is a playable `video/mp4`.
8. Compute SHA-256 and compare to the baseline; if different, inspect beginning/middle/end frames for absence of a Douyin watermark.
9. Switch each theme, restart, and confirm persistence.
10. Double-click the EXE while running and confirm no second service.
11. Exit through the control window and confirm no process/port remains.

Record the commands, hashes, screenshots, and manual outcomes in
`docs/test-reports/2026-07-28-mvp-uat.md`; do not commit downloaded MP4 files.

- [ ] **Step 9: Inspect the final diff and sensitive-data boundary**

Run:

```powershell
git status --short
git diff --stat
$matches = git grep -n -I -E "ttwid=|s_v_web_id=|launch_token.*[A-Za-z0-9_-]{20}|douyinvod\\.com/.+\\?"
if ($LASTEXITCODE -eq 0) { $matches; throw "Sensitive value pattern found" }
if ($LASTEXITCODE -ne 1) { throw "git grep failed" }
```

Expected: only intended source/docs are changed; grep returns no embedded real token, cookie, or full media URL.

- [ ] **Step 10: Commit the release candidate**

```powershell
git add README.md AGENTS.md assets scripts douyin_downloader.spec src tests docs/test-reports
git commit -m "build: package verified Windows MVP"
```

Do not add `build/`, `dist/`, logs, `.superpowers/`, or downloaded MP4 files.

**Done when:** The packaged EXE completes the real user journey, all automated gates pass, the UAT report contains evidence, and sensitive-data grep is clean.

**Risk and rollback:** PyInstaller false positives and upstream parsing changes remain external risks. The rollback is to the previous verified commit or source-mode launch; no user data migration exists.

---

## Requirement-to-Task Trace

| Requirement | Implemented by | Verified by |
| --- | --- | --- |
| FR-001 Start/control/single instance | Tasks 1, 7 | baseline, runtime, lifecycle, packaged UAT |
| FR-002 Input/link validation | Task 3 | resolver unit tests and API errors |
| FR-003 Self-hosted parse | Tasks 3, 4 | fake contract, f2 mapping, explicit live check |
| FR-004 Result UI | Tasks 3, 6 | parse API and browser E2E |
| FR-005 Default streaming download | Task 5 | media unit/integration and UAT |
| FR-006 Choose save location | Tasks 5, 6 | picker stub/fallback E2E and Windows UAT |
| FR-007 Three themes | Task 6 | theme persistence E2E and screenshots |
| FR-008 Local session protection | Tasks 2, 7 | session contract and duplicate-launch tests |
| Logging/privacy | Tasks 4, 8 | log redaction and sensitive-data grep |
| Packaging/compatibility | Task 8 | clean build and packaged executable UAT |

## Execution Order and Checkpoints

Execute tasks strictly in order. After Tasks 3, 5, 7, and 8, pause for a user-visible checkpoint:

1. **Task 3:** deterministic paste-to-preview path;
2. **Task 5:** deterministic preview-to-download path;
3. **Task 7:** real double-click-style lifecycle from source;
4. **Task 8:** packaged executable and real Douyin UAT.

If the real f2 check in Task 4 fails because upstream behavior changed, stop and run systematic debugging on the adapter. Do not compensate by adding a public parsing API or reading browser cookies without a new approved design change.
