from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from dataclasses import dataclass
from typing import Any

import httpx

from douyin_downloader.domain import AppError, ParsedVideo, TransientUpstreamError

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36"
)
POST_DETAIL_ENDPOINT = "https://www.douyin.com/aweme/v1/web/aweme/detail/"
TTWID_REGISTER_ENDPOINT = "https://ttwid.bytedance.com/ttwid/union/register/"
TTWID_REGISTER_PAYLOAD = {
    "region": "cn",
    "aid": 1768,
    "needFid": False,
    "service": "www.ixigua.com",
    "migrate_info": {"ticket": "", "source": "node"},
    "cbUrlProtocol": "https",
    "union": True,
}


def _prevent_f2_default_file_logging() -> None:
    for name in ("f2", "f2-trace"):
        logger = logging.getLogger(name)
        if not logger.handlers:
            logger.addHandler(logging.NullHandler())
        logger.propagate = False


def _build_post_detail_params(aweme_id: str, ms_token: str) -> dict[str, object]:
    # Pinned to f2 0.0.1.7 BaseRequestModel + PostDetail. Importing that model
    # performs an uncontrolled synchronous token request, so this audited shim
    # preserves only the query shape needed by fetch_post_detail.
    return {
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
        "msToken": ms_token,
        "aweme_id": aweme_id,
    }


@dataclass(frozen=True, slots=True)
class _F2Runtime:
    signed_endpoint: str
    cookie: str


@dataclass(frozen=True, slots=True)
class _PostDetailView:
    api_status_code: Any
    aweme_id: Any
    nickname_raw: Any
    desc_raw: Any
    duration: Any
    cover: Any
    video_play_addr: Any
    images: Any


def _nested(value: object, *keys: str | int) -> Any:
    current: Any = value
    for key in keys:
        if isinstance(key, str) and isinstance(current, dict):
            current = current.get(key)
        elif isinstance(key, int) and isinstance(current, list) and len(current) > key:
            current = current[key]
        else:
            return None
    return current


def _post_detail_filter(payload: dict[str, object]) -> _PostDetailView:
    # This is the subset of f2 0.0.1.7 PostDetailFilter consumed by
    # map_post_detail. Importing the upstream filter also imports
    # browser_cookie3/win32com, which is unrelated to guest parsing.
    return _PostDetailView(
        api_status_code=_nested(payload, "status_code"),
        aweme_id=_nested(payload, "aweme_detail", "aweme_id"),
        nickname_raw=_nested(payload, "aweme_detail", "author", "nickname"),
        desc_raw=_nested(payload, "aweme_detail", "desc"),
        duration=_nested(payload, "aweme_detail", "duration"),
        cover=_nested(
            payload,
            "aweme_detail",
            "video",
            "origin_cover",
            "url_list",
            0,
        ),
        video_play_addr=_nested(
            payload,
            "aweme_detail",
            "video",
            "bit_rate",
            0,
            "play_addr",
            "url_list",
        ),
        images=_nested(payload, "aweme_detail", "images") or [],
    )


def _generate_verify_fp() -> str:
    base = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    milliseconds = int(round(time.time() * 1000))
    base36 = ""
    while milliseconds > 0:
        remainder = milliseconds % 36
        base36 = (
            str(remainder) if remainder < 10 else chr(ord("a") + remainder - 10)
        ) + base36
        milliseconds //= 36

    random_part = [""] * 36
    random_part[8] = random_part[13] = random_part[18] = random_part[23] = "_"
    random_part[14] = "4"
    for index, value in enumerate(random_part):
        if value:
            continue
        random_index = secrets.randbelow(len(base))
        if index == 19:
            random_index = 3 & random_index | 8
        random_part[index] = base[random_index]
    return f"verify_{base36}_{''.join(random_part)}"


def _sign_post_detail(params: dict[str, object]) -> str:
    from f2.utils.abogus import (  # type: ignore[import-untyped]
        ABogus,
        BrowserFingerprintGenerator,
    )

    param_str = "&".join(f"{key}={value}" for key, value in params.items())
    browser_fp = BrowserFingerprintGenerator.generate_fingerprint("Edge")
    ab_value = ABogus(fp=browser_fp, user_agent=USER_AGENT).generate_abogus(param_str, "")
    return f"{POST_DETAIL_ENDPOINT}?{param_str}&a_bogus={ab_value[1]}"


def _load_f2_runtime_and_guest(aweme_id: str) -> _F2Runtime:
    _prevent_f2_default_file_logging()

    # f2's TokenManager hard-codes BaseCrawler's default of five retries. Read
    # only the audited guest registration contract and issue it once through
    # an explicit no-retry, no-environment transport. The detail endpoint
    # accepts an empty guest msToken, so no opaque f2 config payload is needed.
    with httpx.Client(
        transport=httpx.HTTPTransport(retries=0),
        timeout=20,
        trust_env=False,
    ) as client:
        ttwid_response = client.post(
            TTWID_REGISTER_ENDPOINT,
            content=json.dumps(TTWID_REGISTER_PAYLOAD, separators=(",", ":")),
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "User-Agent": USER_AGENT,
            },
        )
        ttwid_response.raise_for_status()
        ttwid = ttwid_response.cookies.get("ttwid")
        if ttwid is None:
            raise RuntimeError("invalid guest ttwid")

    params = _build_post_detail_params(aweme_id, "")
    return _F2Runtime(
        signed_endpoint=_sign_post_detail(params),
        cookie=f"ttwid={ttwid}; s_v_web_id={_generate_verify_fp()};",
    )


class _PostDetailCrawler:
    """Audited f2 0.0.1.7 fetch_post_detail shim without model import side effects."""

    def __init__(self, runtime: _F2Runtime) -> None:
        self._runtime = runtime
        self._client = httpx.AsyncClient(
            headers={
                "User-Agent": USER_AGENT,
                "Referer": "https://www.douyin.com/",
                "Cookie": runtime.cookie,
            },
            transport=httpx.AsyncHTTPTransport(retries=0),
            timeout=20,
            trust_env=False,
        )

    async def __aenter__(self) -> _PostDetailCrawler:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self._client.aclose()

    async def fetch_post_detail(self) -> dict[str, object]:
        response = await self._client.get(
            self._runtime.signed_endpoint,
            follow_redirects=False,
        )
        response.raise_for_status()
        payload: dict[str, object] = response.json()
        return payload


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
        try:
            runtime = await asyncio.to_thread(_load_f2_runtime_and_guest, aweme_id)
            async with _PostDetailCrawler(runtime) as crawler:
                payload = await crawler.fetch_post_detail()
            detail = _post_detail_filter(payload)
        except Exception as error:
            raise TransientUpstreamError(type(error).__name__) from error
        return map_post_detail(detail)
