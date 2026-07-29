# HTTPS URL Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the local downloader accept only HTTPS Douyin entry links and convert malformed URL parser failures into stable, actionable client errors.

**Architecture:** Keep all entry and redirect URL policy in `url_resolver.py`. Add one malformed-input error factory and one HTTPS-only error factory, wrap URL decomposition in a single safe parsing boundary, and reuse the same validator for the initial URL and every redirect hop. The API and browser continue using the existing `AppError` response contract.

**Tech Stack:** Python 3.12, `urllib.parse`, FastAPI, HTTPX, pytest, Ruff, Mypy, PyInstaller, PowerShell

## Global Constraints

- Accept only `https://` entry and redirect URLs on the existing Douyin host allowlist.
- Do not add a third-party dependency.
- Keep the server bound to `127.0.0.1`; never add LAN or public binding.
- Never read browser/Douyin login cookies or log share text, secrets, or media URLs.
- Preserve `1.txt`, `2.txt`, and `AI项目开发总提示词.md`.
- Do not add content-moderation, multi-platform, batch, private-video, or login behavior.
- Keep the media CDN validator and its HTTPS-only allowlist unchanged.

---

## File Map

- Modify `src/douyin_downloader/url_resolver.py`: own all entry URL extraction, structural parsing, host, scheme, credential, port, and redirect validation.
- Modify `tests/unit/test_url_resolver.py`: prove error codes/messages and prove rejected redirect targets are never requested.
- Modify `README.md`: tell users that pasted Douyin links must use HTTPS.
- Modify `docs/superpowers/specs/2026-07-28-douyin-local-downloader-design.md`: update the original product contract from HTTP(S) to HTTPS.
- Rebuild `dist/抖音视频下载.exe`: deliver the hardened behavior in the double-click application.

### Task 1: Harden URL validation with TDD

**Files:**
- Modify: `tests/unit/test_url_resolver.py`
- Modify: `src/douyin_downloader/url_resolver.py`

**Interfaces:**
- Consumes: `extract_share_url(text: str) -> str`, `ShareResolver.resolve(share_text: str) -> ResolvedShare`, `AppError(code, message, status_code)`.
- Produces: the same public interfaces, with deterministic `INVALID_INPUT` and `UNSUPPORTED_URL` results for unsafe input.

- [ ] **Step 1: Create the Python 3.12 development environment if the isolated workspace does not already have one**

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

Expected: installation exits `0`, and `.\.venv\Scripts\python.exe -m pip check` prints `No broken requirements found.`

- [ ] **Step 2: Add failing tests for HTTPS-only and malformed input**

Add these focused cases to `tests/unit/test_url_resolver.py`, retaining the existing successful HTTPS and redirect tests:

```python
@pytest.mark.parametrize(
    "host",
    [
        "douyin.com",
        "www.douyin.com",
        "v.douyin.com",
        "iesdouyin.com",
        "www.iesdouyin.com",
    ],
)
def test_rejects_http_douyin_links_with_https_only_message(host: str) -> None:
    with pytest.raises(AppError) as error:
        extract_share_url(f"http://{host}/video/7429378937383308594")

    assert error.value.code == "UNSUPPORTED_URL"
    assert error.value.status_code == 400
    assert error.value.message == "仅支持 HTTPS 抖音公开视频链接。"


@pytest.mark.parametrize(
    "value",
    [
        "https://",
        "https:///video/7429378937383308594",
        "https://[::1",
        "https://v.douyin.com:99999/video/7429378937383308594",
        "https://v.douyin.com：443/video/7429378937383308594",
    ],
)
def test_rejects_malformed_urls_with_stable_message(value: str) -> None:
    with pytest.raises(AppError) as error:
        extract_share_url(value)

    assert error.value.code == "INVALID_INPUT"
    assert error.value.status_code == 400
    assert error.value.message == "链接格式不正确，请粘贴有效的 HTTPS 抖音分享链接。"


def test_rejects_https_non_douyin_url_with_platform_message() -> None:
    with pytest.raises(AppError) as error:
        extract_share_url("https://www.kuaishou.com/short-video/example")

    assert error.value.code == "UNSUPPORTED_URL"
    assert error.value.message == "目前只支持抖音公开视频。"


def test_text_without_url_keeps_missing_link_message() -> None:
    with pytest.raises(AppError) as error:
        extract_share_url("这段文本里没有链接")

    assert error.value.code == "INVALID_INPUT"
    assert error.value.message == "没有识别到抖音链接，请粘贴完整分享文案。"
```

Add redirect rejection coverage:

```python
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("location", "expected_code", "expected_message"),
    [
        (
            "http://www.douyin.com/video/7429378937383308594",
            "UNSUPPORTED_URL",
            "仅支持 HTTPS 抖音公开视频链接。",
        ),
        (
            "https://[::1",
            "INVALID_INPUT",
            "链接格式不正确，请粘贴有效的 HTTPS 抖音分享链接。",
        ),
    ],
)
async def test_rejects_unsafe_redirect_before_requesting_target(
    location: str,
    expected_code: str,
    expected_message: str,
) -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        assert request.url.host == "v.douyin.com"
        return httpx.Response(302, headers={"location": location})

    resolver = ShareResolver(httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    with pytest.raises(AppError) as error:
        await resolver.resolve("https://v.douyin.com/start/")

    assert error.value.code == expected_code
    assert error.value.message == expected_message
    assert requests == ["https://v.douyin.com/start/"]
```

- [ ] **Step 3: Run the focused tests and verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit\test_url_resolver.py -q
```

Expected: the new HTTP test fails because HTTP is currently accepted; malformed cases fail because they either use the old message or raise `ValueError`; existing HTTPS tests remain green.

- [ ] **Step 4: Implement the minimal centralized validator**

In `src/douyin_downloader/url_resolver.py`, add the error messages and safe parsing boundary:

```python
HTTP_URL_MARKER = re.compile(r"https?://", re.IGNORECASE)
URL_PATTERN = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)

_MISSING_LINK_MESSAGE = "没有识别到抖音链接，请粘贴完整分享文案。"
_MALFORMED_URL_MESSAGE = "链接格式不正确，请粘贴有效的 HTTPS 抖音分享链接。"
_UNSUPPORTED_URL_MESSAGE = "目前只支持抖音公开视频。"
_HTTPS_ONLY_MESSAGE = "仅支持 HTTPS 抖音公开视频链接。"


def _invalid_input(message: str) -> AppError:
    return AppError("INVALID_INPUT", message, 400)


def _unsupported_url(message: str = _UNSUPPORTED_URL_MESSAGE) -> AppError:
    return AppError("UNSUPPORTED_URL", message, 400)


def _url_parts(url: str) -> tuple[str, str, int | None, bool]:
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
        port = parsed.port
        has_credentials = parsed.username is not None or parsed.password is not None
    except ValueError as error:
        raise _invalid_input(_MALFORMED_URL_MESSAGE) from error
    if not parsed.netloc or hostname is None:
        raise _invalid_input(_MALFORMED_URL_MESSAGE)
    return parsed.scheme.lower(), hostname.lower(), port, has_credentials


def _validated_url(url: str) -> str:
    scheme, hostname, port, has_credentials = _url_parts(url)
    if hostname not in ENTRY_HOSTS:
        raise _unsupported_url()
    if scheme != "https":
        if scheme == "http":
            raise _unsupported_url(_HTTPS_ONLY_MESSAGE)
        raise _unsupported_url()
    if has_credentials or port not in {None, 443}:
        raise _unsupported_url()
    return url
```

Update `extract_share_url` so a URL marker without an extractable URL gets the malformed message:

```python
def extract_share_url(text: str) -> str:
    if not 1 <= len(text) <= 2000:
        raise _invalid_input(_MISSING_LINK_MESSAGE)
    match = URL_PATTERN.search(text)
    if match is None:
        message = (
            _MALFORMED_URL_MESSAGE
            if HTTP_URL_MARKER.search(text)
            else _MISSING_LINK_MESSAGE
        )
        raise _invalid_input(message)
    return _validated_url(match.group(0).rstrip(TRAILING_PUNCTUATION))
```

- [ ] **Step 5: Run the focused tests and verify GREEN**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit\test_url_resolver.py -q
```

Expected: all URL resolver tests pass, including the new message and no-second-request assertions.

- [ ] **Step 6: Run nearby API and parser regression tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\integration\test_parse_api.py tests\unit\test_f2_adapter.py -q
```

Expected: all selected tests pass.

- [ ] **Step 7: Commit Task 1**

```powershell
git add -- src/douyin_downloader/url_resolver.py tests/unit/test_url_resolver.py
git commit -m "fix: require HTTPS Douyin entry URLs"
```

### Task 2: Synchronize the user and product contracts

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-07-28-douyin-local-downloader-design.md`

**Interfaces:**
- Consumes: the Task 1 behavior and exact user-facing messages.
- Produces: documentation that states HTTPS-only entry and redirect validation without changing runtime behavior.

- [ ] **Step 1: Update the README usage rule**

Replace README step 3 with:

```markdown
3. 粘贴 HTTPS 抖音分享文案或链接，点击“开始解析”；HTTP、非抖音或格式异常的链接会被安全拒绝并显示修改提示。
```

- [ ] **Step 2: Update the original FR-002 and security contract**

In `docs/superpowers/specs/2026-07-28-douyin-local-downloader-design.md`:

```markdown
- 从分享文案中提取第一个 HTTPS 链接；检测到 HTTP 抖音链接时返回专用升级提示。
```

Replace the security statement with:

```markdown
- 页面输入 URL 只允许 FR-002 中列出的 HTTPS 主机；重定向逐跳校验，禁止访问
  HTTP、localhost、私有 IP、文件协议和非受信任目标；畸形 URL 统一返回可读的
  `INVALID_INPUT`，不允许解析异常冒泡为 HTTP 500。
```

- [ ] **Step 3: Check documentation consistency**

```powershell
Select-String -Path README.md,docs\superpowers\specs\2026-07-28-douyin-local-downloader-design.md -Pattern 'HTTP/HTTPS|HTTP\\(S\\)'
git diff --check
```

Expected: the search finds no stale entry-link promise that permits HTTP; `git diff --check` exits `0`.

- [ ] **Step 4: Commit Task 2**

```powershell
git add -- README.md docs/superpowers/specs/2026-07-28-douyin-local-downloader-design.md
git commit -m "docs: require HTTPS Douyin share links"
```

### Task 3: Run the full gate and rebuild the Windows executable

**Files:**
- Verify: `src/`, `tests/`, `scripts/`
- Rebuild ignored artifact: `dist/抖音视频下载.exe`

**Interfaces:**
- Consumes: the committed Task 1 and Task 2 source state.
- Produces: a verified single-file Windows executable containing the HTTPS-only validator.

- [ ] **Step 1: Run the full source gate**

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src tests scripts
$env:MYPYPATH = (Resolve-Path src).Path
.\.venv\Scripts\python.exe -m mypy src
Remove-Item Env:MYPYPATH -ErrorAction SilentlyContinue
node --check src\douyin_downloader\web\static\app.js
git diff --check
```

Expected: all tests pass, Ruff reports `All checks passed!`, Mypy reports no issues, JavaScript syntax exits `0`, and the diff check is clean.

- [ ] **Step 2: Build the executable through the only supported build entry**

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build.ps1
```

Expected: the script reruns the full quality gate, creates `dist\抖音视频下载.exe`, scans the repository and archive with `findings=0`, and prints a SHA-256 hash.

- [ ] **Step 3: Run packaged-resource and sensitive-artifact regression tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\integration\test_packaged_resources.py tests\unit\test_sensitive_gate.py -q
.\.venv\Scripts\python.exe scripts\check_sensitive.py --artifact "dist\抖音视频下载.exe"
Get-FileHash -LiteralPath "dist\抖音视频下载.exe" -Algorithm SHA256
```

Expected: all selected tests pass, the artifact scan reports `findings=0`, and the final hash is recorded for handoff.

- [ ] **Step 4: Verify repository scope**

```powershell
git status --short
git log -3 --oneline
```

Expected: only the pre-existing user-owned untracked prompt document remains; source and documentation changes are committed; ignored build artifacts do not appear.
