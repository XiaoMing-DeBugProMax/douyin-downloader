import httpx
import pytest

from douyin_downloader.domain import AppError
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
