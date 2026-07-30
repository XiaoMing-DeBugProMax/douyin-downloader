import httpx
import pytest

from douyin_downloader.archive import HttpMediaAccess


@pytest.mark.asyncio
async def test_http_media_access_uses_cdn_mirror_and_reports_reliable_length() -> None:
    requests: list[str] = []
    payload = b"valid-video-bytes"

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if request.url.host == "v95-web.douyinvod.com":
            return httpx.Response(503, request=request)
        return httpx.Response(
            200,
            headers={"content-type": "video/mp4", "content-length": str(len(payload))},
            content=payload,
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        remote = await HttpMediaAccess(client).open_video(
            (
                "https://v95-web.douyinvod.com/video.mp4",
                "https://v11-web.douyinvod.com/video.mp4",
            )
        )
        received = b"".join([chunk async for chunk in remote.chunks])

    assert requests == [
        "https://v95-web.douyinvod.com/video.mp4",
        "https://v11-web.douyinvod.com/video.mp4",
    ]
    assert remote.content_type == "video/mp4"
    assert remote.expected_size == len(payload)
    assert received == payload
