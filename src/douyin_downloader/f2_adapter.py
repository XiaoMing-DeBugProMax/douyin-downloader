from __future__ import annotations

import json
import logging
import secrets
import time
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from douyin_downloader.domain import (
    AppError,
    AuthorSnapshot,
    MusicSnapshot,
    ParsedVideo,
    PublicMetrics,
    TransientUpstreamError,
    VideoVariant,
    WorkSnapshot,
)

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
_MISSING_APPLICATION_STATUSES = frozenset({4, 10204})
_BLOCKED_MESSAGE = "解析服务暂时不可用，请稍后重试。"
_NOT_FOUND_MESSAGE = "没有找到公开视频，请检查作品是否存在。"


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
    aweme_type: Any
    create_time: Any
    author_sec_uid: Any
    author_uid: Any
    nickname_raw: Any
    desc_raw: Any
    duration: Any
    cover: Any
    cover_urls: Any
    video_play_addr: Any
    video_bit_rate: Any
    music: Any
    statistics: Any
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


def _urls(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str) and item)


def _dedupe_urls(*groups: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(url for group in groups for url in group))


def _post_detail_filter(payload: dict[str, object]) -> _PostDetailView:
    # This is the subset of f2 0.0.1.7 PostDetailFilter consumed by
    # map_post_detail. Importing the upstream filter also imports
    # browser_cookie3/win32com, which is unrelated to guest parsing.
    return _PostDetailView(
        api_status_code=_nested(payload, "status_code"),
        aweme_id=_nested(payload, "aweme_detail", "aweme_id"),
        aweme_type=_nested(payload, "aweme_detail", "aweme_type"),
        create_time=_nested(payload, "aweme_detail", "create_time"),
        author_sec_uid=_nested(payload, "aweme_detail", "author", "sec_uid"),
        author_uid=_nested(payload, "aweme_detail", "author", "uid"),
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
        cover_urls=_dedupe_urls(
            _urls(_nested(payload, "aweme_detail", "video", "origin_cover", "url_list")),
            _urls(_nested(payload, "aweme_detail", "video", "cover", "url_list")),
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
        video_bit_rate=_nested(payload, "aweme_detail", "video", "bit_rate"),
        music=_nested(payload, "aweme_detail", "music"),
        statistics=_nested(payload, "aweme_detail", "statistics"),
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


def _raise_for_upstream_status(
    response: httpx.Response,
    *,
    not_found_is_video: bool,
) -> None:
    status = response.status_code
    if status == 404 and not_found_is_video:
        raise AppError("VIDEO_NOT_FOUND", _NOT_FOUND_MESSAGE, 404)
    if 500 <= status <= 599:
        raise TransientUpstreamError(f"HTTP {status}")
    if status < 200 or status >= 300:
        raise AppError("UPSTREAM_BLOCKED", _BLOCKED_MESSAGE, 502)


async def _load_f2_runtime_and_guest(aweme_id: str) -> _F2Runtime:
    _prevent_f2_default_file_logging()

    # f2's TokenManager hard-codes BaseCrawler's default of five retries. Read
    # only the audited guest registration contract and issue it once through
    # an explicit no-retry, no-environment transport. The detail endpoint
    # accepts an empty guest msToken, so no opaque f2 config payload is needed.
    async with httpx.AsyncClient(
        transport=httpx.AsyncHTTPTransport(retries=0),
        timeout=20,
        trust_env=False,
        follow_redirects=False,
    ) as client:
        try:
            ttwid_response = await client.post(
                TTWID_REGISTER_ENDPOINT,
                content=json.dumps(TTWID_REGISTER_PAYLOAD, separators=(",", ":")),
                headers={
                    "Content-Type": "application/json; charset=utf-8",
                    "User-Agent": USER_AGENT,
                },
            )
        except httpx.TimeoutException as error:
            raise TimeoutError from error
        except httpx.NetworkError as error:
            raise TransientUpstreamError(type(error).__name__) from error
        _raise_for_upstream_status(ttwid_response, not_found_is_video=False)
        ttwid = ttwid_response.cookies.get("ttwid")
        if ttwid is None:
            raise AppError("UPSTREAM_BLOCKED", _BLOCKED_MESSAGE, 502)

    try:
        params = _build_post_detail_params(aweme_id, "")
        signed_endpoint = _sign_post_detail(params)
        verify_fp = _generate_verify_fp()
    except Exception as error:
        raise AppError("UPSTREAM_BLOCKED", _BLOCKED_MESSAGE, 502) from error
    return _F2Runtime(signed_endpoint, f"ttwid={ttwid}; s_v_web_id={verify_fp};")


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
        try:
            response = await self._client.get(
                self._runtime.signed_endpoint,
                follow_redirects=False,
            )
        except httpx.TimeoutException as error:
            raise TimeoutError from error
        except httpx.NetworkError as error:
            raise TransientUpstreamError(type(error).__name__) from error
        _raise_for_upstream_status(response, not_found_is_video=True)
        try:
            payload: dict[str, object] = response.json()
        except (TypeError, ValueError) as error:
            raise AppError("UPSTREAM_BLOCKED", _BLOCKED_MESSAGE, 502) from error
        if not isinstance(payload, dict):
            raise AppError("UPSTREAM_BLOCKED", _BLOCKED_MESSAGE, 502)
        return payload


class WorkAccess(Protocol):
    async def fetch_work(self, aweme_id: str) -> WorkSnapshot: ...


class _PostDetailSource(Protocol):
    async def fetch(self, aweme_id: str) -> dict[str, object]: ...


class _RemotePostDetailSource:
    async def fetch(self, aweme_id: str) -> dict[str, object]:
        runtime = await _load_f2_runtime_and_guest(aweme_id)
        async with _PostDetailCrawler(runtime) as crawler:
            return await crawler.fetch_post_detail()


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _optional_text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _video_variants(detail: Any) -> tuple[VideoVariant, ...]:
    variants: list[VideoVariant] = []
    raw_variants = getattr(detail, "video_bit_rate", None)
    if isinstance(raw_variants, list):
        for raw_variant in raw_variants:
            if not isinstance(raw_variant, dict):
                continue
            play_addr = raw_variant.get("play_addr")
            if not isinstance(play_addr, dict):
                continue
            media_urls = _urls(play_addr.get("url_list"))
            if not media_urls:
                continue
            is_bytevc1 = _optional_int(raw_variant.get("is_bytevc1"))
            codec = "h265" if is_bytevc1 == 1 else "h264" if is_bytevc1 == 0 else ""
            variants.append(
                VideoVariant(
                    bitrate=_optional_int(raw_variant.get("bit_rate")),
                    gear_name=_optional_text(raw_variant.get("gear_name")),
                    quality_type=_optional_int(raw_variant.get("quality_type")),
                    codec=codec,
                    width=_optional_int(play_addr.get("width")),
                    height=_optional_int(play_addr.get("height")),
                    size_bytes=_optional_int(play_addr.get("data_size")),
                    media_urls=media_urls,
                )
            )
    if variants:
        return tuple(variants)

    media_urls = tuple(getattr(detail, "video_play_addr", None) or ())
    if not media_urls:
        return ()
    return (
        VideoVariant(
            bitrate=None,
            gear_name="",
            quality_type=None,
            codec="",
            width=None,
            height=None,
            size_bytes=None,
            media_urls=media_urls,
        ),
    )


def _music_snapshot(raw_music: object) -> MusicSnapshot | None:
    if not isinstance(raw_music, dict):
        return None
    stable_id = raw_music.get("id_str") or raw_music.get("mid") or raw_music.get("id")
    if stable_id is None:
        return None
    return MusicSnapshot(
        stable_id=str(stable_id),
        title=_optional_text(raw_music.get("title")),
        author=_optional_text(raw_music.get("author")),
        duration_seconds=_optional_int(raw_music.get("duration")),
    )


def _public_metrics(raw_statistics: object) -> PublicMetrics:
    statistics = raw_statistics if isinstance(raw_statistics, dict) else {}
    return PublicMetrics(
        likes=_optional_int(statistics.get("digg_count")),
        comments=_optional_int(statistics.get("comment_count")),
        shares=_optional_int(statistics.get("share_count")),
        collects=_optional_int(statistics.get("collect_count")),
    )


def _map_work_snapshot(detail: Any) -> WorkSnapshot:
    if detail.api_status_code != 0:
        if detail.api_status_code in _MISSING_APPLICATION_STATUSES:
            raise AppError("VIDEO_NOT_FOUND", _NOT_FOUND_MESSAGE, 404)
        raise AppError("UPSTREAM_BLOCKED", _BLOCKED_MESSAGE, 502)
    if detail.images:
        raise AppError("UNSUPPORTED_CONTENT", "当前版本不支持图集或直播内容。", 422)

    aweme_id = str(detail.aweme_id or "")
    variants = _video_variants(detail)
    if not aweme_id or not variants:
        raise AppError("UNSUPPORTED_CONTENT", "当前版本只支持公开视频。", 422)

    cover_urls = tuple(getattr(detail, "cover_urls", None) or ())
    if not cover_urls and detail.cover:
        cover_urls = (str(detail.cover),)
    stable_author_id = (
        getattr(detail, "author_sec_uid", None)
        or getattr(detail, "author_uid", None)
        or ""
    )
    return WorkSnapshot(
        aweme_id=aweme_id,
        content_type="video",
        public_url=f"https://www.douyin.com/video/{aweme_id}",
        description=str(detail.desc_raw or ""),
        published_at=_optional_int(getattr(detail, "create_time", None)),
        duration_ms=int(detail.duration or 0),
        author=AuthorSnapshot(
            stable_id=str(stable_author_id),
            nickname=str(detail.nickname_raw or ""),
        ),
        cover_urls=cover_urls,
        video_variants=variants,
        music=_music_snapshot(getattr(detail, "music", None)),
        public_metrics=_public_metrics(getattr(detail, "statistics", None)),
    )


class F2WorkAccess:
    def __init__(self, source: _PostDetailSource | None = None) -> None:
        self._source = source if source is not None else _RemotePostDetailSource()

    async def fetch_work(self, aweme_id: str) -> WorkSnapshot:
        try:
            payload = await self._source.fetch(aweme_id)
            detail = _post_detail_filter(payload)
            return _map_work_snapshot(detail)
        except (AppError, TimeoutError, TransientUpstreamError):
            raise
        except Exception as error:
            raise AppError("UPSTREAM_BLOCKED", _BLOCKED_MESSAGE, 502) from error


def map_post_detail(detail: Any) -> ParsedVideo:
    if detail.api_status_code != 0:
        if detail.api_status_code in _MISSING_APPLICATION_STATUSES:
            raise AppError("VIDEO_NOT_FOUND", _NOT_FOUND_MESSAGE, 404)
        raise AppError("UPSTREAM_BLOCKED", _BLOCKED_MESSAGE, 502)
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
    def __init__(self, work_access: WorkAccess | None = None) -> None:
        self._work_access = work_access if work_access is not None else F2WorkAccess()

    async def parse(self, aweme_id: str) -> ParsedVideo:
        snapshot = await self._work_access.fetch_work(aweme_id)
        return snapshot.quick_download_projection()
