from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AuthorSnapshot:
    stable_id: str
    nickname: str


@dataclass(frozen=True, slots=True)
class VideoVariant:
    bitrate: int | None
    gear_name: str
    quality_type: int | None
    codec: str
    width: int | None
    height: int | None
    size_bytes: int | None
    media_urls: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MusicSnapshot:
    stable_id: str
    title: str
    author: str
    duration_seconds: int | None


@dataclass(frozen=True, slots=True)
class PublicMetrics:
    likes: int | None
    comments: int | None
    shares: int | None
    collects: int | None


@dataclass(frozen=True, slots=True)
class WorkSnapshot:
    aweme_id: str
    content_type: str
    public_url: str
    description: str
    published_at: int | None
    duration_ms: int
    author: AuthorSnapshot
    cover_urls: tuple[str, ...]
    video_variants: tuple[VideoVariant, ...]
    music: MusicSnapshot | None
    public_metrics: PublicMetrics

    def preferred_video_variant(self) -> VideoVariant:
        if not self.video_variants:
            raise ValueError("video snapshots require at least one video variant")

        def quality_key(variant: VideoVariant) -> tuple[int, int, int]:
            resolution = (variant.width or 0) * (variant.height or 0)
            return (variant.bitrate or -1, resolution, variant.size_bytes or -1)

        return max(self.video_variants, key=quality_key)

    def quick_download_projection(self) -> ParsedVideo:
        cover_urls = self.cover_urls[:1]
        media_urls = self.video_variants[0].media_urls if self.video_variants else ()
        return ParsedVideo(
            aweme_id=self.aweme_id,
            author=self.author.nickname,
            description=self.description,
            duration_ms=self.duration_ms,
            cover_urls=cover_urls,
            media_urls=media_urls,
        )


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
