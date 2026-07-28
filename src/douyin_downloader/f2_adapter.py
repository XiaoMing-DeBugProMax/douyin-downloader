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
        try:
            from f2.apps.douyin.crawler import DouyinCrawler  # type: ignore[import-untyped]
            from f2.apps.douyin.filter import PostDetailFilter  # type: ignore[import-untyped]
            from f2.apps.douyin.model import PostDetail  # type: ignore[import-untyped]
            from f2.apps.douyin.utils import (  # type: ignore[import-untyped]
                TokenManager,
                VerifyFpManager,
            )

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
                "headers": {
                    "User-Agent": user_agent,
                    "Referer": "https://www.douyin.com/",
                },
                "cookie": cookie,
                "proxies": {"http://": None, "https://": None},
                "timeout": 20,
                "max_retries": 0,
            }
            async with DouyinCrawler(kwargs) as crawler:
                # f2 0.0.1.7 overloads this value for both transport retries and
                # total business-loop attempts. Materialize the no-retry transport
                # before enabling exactly one detail request.
                _ = crawler.aclient
                crawler._max_retries = 1
                payload = await crawler.fetch_post_detail(PostDetail(aweme_id=aweme_id))
            detail = PostDetailFilter(payload)
        except Exception as error:
            raise TransientUpstreamError(type(error).__name__) from error
        return map_post_detail(detail)
