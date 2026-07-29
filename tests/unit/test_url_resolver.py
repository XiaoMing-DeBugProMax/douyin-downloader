import httpx
import pytest

from douyin_downloader.domain import AppError, TransientUpstreamError
from douyin_downloader.url_resolver import ShareResolver, extract_share_url


def test_extracts_first_url_from_share_text() -> None:
    text = "复制打开抖音 https://v.douyin.com/96C_V98aPlc/ 06/09"
    assert extract_share_url(text) == "https://v.douyin.com/96C_V98aPlc/"


@pytest.mark.parametrize(
    "value",
    [
        "",
        "x" * 2001,
        "https://example.com/a",
        "https://v.douyin.com.evil.example/a",
        "https://v.douyin.com:80/a",
        "http://v.douyin.com:443/a",
    ],
)
def test_rejects_invalid_input(value: str) -> None:
    with pytest.raises(AppError) as error:
        extract_share_url(value)
    assert error.value.code in {"INVALID_INPUT", "UNSUPPORTED_URL"}


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


@pytest.mark.parametrize(
    "value",
    [
        "https://v.douyin.com/video/7429378937383308594\x00",
        "https://v.douyin.com/video/7429378937383308594\x7f",
        "https://v.douyin.com/video/7429378937383308594\ud800",
    ],
    ids=["nul", "del", "isolated-surrogate"],
)
def test_rejects_transport_invalid_urls_with_stable_message(value: str) -> None:
    with pytest.raises(AppError) as error:
        extract_share_url(value)

    assert error.value.code == "INVALID_INPUT"
    assert error.value.status_code == 400
    assert error.value.message == "链接格式不正确，请粘贴有效的 HTTPS 抖音分享链接。"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value",
    [
        "https://v.douyin.com/video/7429378937383308594\x00",
        "https://v.douyin.com/video/7429378937383308594\x7f",
        "https://v.douyin.com/video/7429378937383308594\ud800",
    ],
    ids=["nul", "del", "isolated-surrogate"],
)
async def test_rejects_transport_invalid_direct_video_urls_before_requests(
    value: str,
) -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(500)

    resolver = ShareResolver(httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    with pytest.raises(AppError) as error:
        await resolver.resolve(value)

    assert error.value.code == "INVALID_INPUT"
    assert error.value.status_code == 400
    assert error.value.message == "链接格式不正确，请粘贴有效的 HTTPS 抖音分享链接。"
    assert requests == []


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


@pytest.mark.asyncio
async def test_rejects_redirect_to_non_allowlisted_host() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://evil.example/video/1"})

    resolver = ShareResolver(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(AppError) as error:
        await resolver.resolve("https://v.douyin.com/96C_V98aPlc/")
    assert error.value.code == "UNSUPPORTED_URL"


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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "location",
    [
        "http:v.douyin.com",
        "https://",
        "https:///video/1",
    ],
    ids=["scheme-without-authority", "empty-https-authority", "https-empty-host"],
)
async def test_rejects_malformed_redirect_location_before_requesting_target(
    location: str,
) -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(302, headers={"location": location})

    resolver = ShareResolver(httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    with pytest.raises(AppError) as error:
        await resolver.resolve("https://v.douyin.com/start/")

    assert error.value.code == "INVALID_INPUT"
    assert error.value.status_code == 400
    assert error.value.message == "链接格式不正确，请粘贴有效的 HTTPS 抖音分享链接。"
    assert requests == ["https://v.douyin.com/start/"]


@pytest.mark.asyncio
async def test_treats_non_location_remote_protocol_error_as_transient() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.RemoteProtocolError("Server disconnected")

    resolver = ShareResolver(httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    with pytest.raises(TransientUpstreamError) as error:
        await resolver.resolve("https://v.douyin.com/start/")

    assert str(error.value) == "RemoteProtocolError"


@pytest.mark.asyncio
async def test_limits_redirect_hops_to_five_and_revalidates_each_hop() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.url.host == "v.douyin.com"
        return httpx.Response(302, headers={"location": "/next"})

    resolver = ShareResolver(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(AppError) as error:
        await resolver.resolve("https://v.douyin.com/start")
    assert error.value.code == "VIDEO_NOT_FOUND"
    assert calls == 5
