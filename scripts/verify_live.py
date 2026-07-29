import argparse
import asyncio
import hashlib
from dataclasses import dataclass
from pathlib import Path

import httpx

from douyin_downloader.domain import ParsedVideo
from douyin_downloader.f2_adapter import F2VideoParser
from douyin_downloader.media import open_first_available
from douyin_downloader.url_resolver import ShareResolver

EXPECTED_AWEME_ID = "7429378937383308594"
EXPECTED_AUTHOR = "钟哥！！"
EXPECTED_DESCRIPTION = "#王者荣耀 #王者荣耀热门"
EXPECTED_DURATION_MS = 15279
EXPECTED_VIDEO_SHA256 = "B594D1DF250D2450266C1D4714BE9C300FEFFCFEB18E1E6B2928823AEFC02093"
DEFAULT_DOWNLOAD_PATH = (
    Path(__file__).resolve().parent.parent
    / ".superpowers"
    / "sdd"
    / "task-8-uat"
    / "source-verified.mp4"
)


@dataclass(frozen=True, slots=True)
class DownloadEvidence:
    content_type: str
    bytes_streamed: int
    sha256: str


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


def safe_download_summary(evidence: DownloadEvidence) -> str:
    return (
        f"content_type={evidence.content_type} "
        f"bytes={evidence.bytes_streamed} "
        f"sha256={evidence.sha256}"
    )


async def download_video(
    video: ParsedVideo,
    output_path: Path,
    client: httpx.AsyncClient,
) -> DownloadEvidence:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_suffix(f"{output_path.suffix}.part")
    digest = hashlib.sha256()
    byte_count = 0
    header = bytearray()
    upstream = await open_first_available(client, video.media_urls, "video")
    try:
        with temporary_path.open("wb") as destination:
            async for chunk in upstream.iter_bytes():
                destination.write(chunk)
                digest.update(chunk)
                byte_count += len(chunk)
                if len(header) < 12:
                    header.extend(chunk[: 12 - len(header)])
        if byte_count < 12 or bytes(header[4:8]) != b"ftyp":
            raise RuntimeError("download did not contain a valid MP4 header")
        temporary_path.replace(output_path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise
    return DownloadEvidence(
        content_type=upstream.content_type,
        bytes_streamed=byte_count,
        sha256=digest.hexdigest().upper(),
    )


async def verify(url: str, *, download: bool = False) -> int:
    timeout = httpx.Timeout(20, read=60)
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        resolved = await ShareResolver(client).resolve(url)
        video = await F2VideoParser().parse(resolved.aweme_id)
        if not matches_expected_sample(video):
            print(f"FAIL {safe_summary(video)}")
            return 1
        print(f"PASS {safe_summary(video)}")
        if download:
            evidence = await download_video(video, DEFAULT_DOWNLOAD_PATH, client)
            if evidence.sha256 != EXPECTED_VIDEO_SHA256:
                print(f"DOWNLOAD REVIEW {safe_download_summary(evidence)} baseline_match=false")
                return 1
            print(f"DOWNLOAD PASS {safe_download_summary(evidence)} baseline_match=true")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify approved public Douyin metadata")
    parser.add_argument("--url", required=True)
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args()
    try:
        return asyncio.run(verify(args.url, download=args.download))
    except Exception as error:
        print(f"FAIL reason=verification_error type={type(error).__name__}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
