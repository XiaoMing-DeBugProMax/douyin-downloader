import argparse
import asyncio

import httpx

from douyin_downloader.domain import ParsedVideo
from douyin_downloader.f2_adapter import F2VideoParser
from douyin_downloader.url_resolver import ShareResolver

EXPECTED_AWEME_ID = "7429378937383308594"
EXPECTED_AUTHOR = "钟哥！！"
EXPECTED_DESCRIPTION = "#王者荣耀 #王者荣耀热门"
EXPECTED_DURATION_MS = 15279


def safe_summary(video: ParsedVideo) -> str:
    return (
        f"aweme_id={video.aweme_id} "
        f"author={video.author} "
        f"description={video.description} "
        f"duration_ms={video.duration_ms} "
        f"candidates={len(video.media_urls)}"
    )


def matches_expected_sample(video: ParsedVideo) -> bool:
    return (
        video.aweme_id == EXPECTED_AWEME_ID
        and video.author == EXPECTED_AUTHOR
        and video.description == EXPECTED_DESCRIPTION
        and video.duration_ms == EXPECTED_DURATION_MS
        and len(video.media_urls) == 3
    )


async def verify(url: str) -> int:
    async with httpx.AsyncClient(timeout=20) as client:
        resolved = await ShareResolver(client).resolve(url)
        video = await F2VideoParser().parse(resolved.aweme_id)

    status = "PASS" if matches_expected_sample(video) else "FAIL"
    print(f"{status} {safe_summary(video)}")
    return 0 if status == "PASS" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify approved public Douyin metadata")
    parser.add_argument("--url", required=True)
    args = parser.parse_args()
    try:
        return asyncio.run(verify(args.url))
    except Exception as error:
        print(f"FAIL reason=verification_error type={type(error).__name__}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
