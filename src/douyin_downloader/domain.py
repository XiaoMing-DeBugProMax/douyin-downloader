from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AuthorSnapshot:
    stable_id: str
    nickname: str


@dataclass(frozen=True, slots=True)
class PlaybackSource:
    bitrate: int | None
    gear_name: str
    quality_type: int | None
    codec: str
    width: int | None
    height: int | None
    size_bytes: int | None
    cdn_mirror_urls: tuple[str, ...]


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
    tags: tuple[str, ...]
    published_at: int | None
    duration_ms: int
    author: AuthorSnapshot
    music: MusicSnapshot | None
    public_metrics: PublicMetrics


@dataclass(frozen=True, slots=True)
class ResolvedWork:
    snapshot: WorkSnapshot
    cover_urls: tuple[str, ...]
    playback_sources: tuple[PlaybackSource, ...]

    def preferred_playback_source(self) -> PlaybackSource:
        if not self.playback_sources:
            raise ValueError("resolved videos require at least one playback source")

        def quality_key(source: PlaybackSource) -> tuple[int, int, int]:
            resolution = (source.width or 0) * (source.height or 0)
            return (source.bitrate or -1, resolution, source.size_bytes or -1)

        return max(self.playback_sources, key=quality_key)

    def quick_download_projection(self) -> ParsedVideo:
        cover_urls = self.cover_urls[:1]
        media_urls = (
            self.playback_sources[0].cdn_mirror_urls if self.playback_sources else ()
        )
        return ParsedVideo(
            aweme_id=self.snapshot.aweme_id,
            author=self.snapshot.author.nickname,
            description=self.snapshot.description,
            duration_ms=self.snapshot.duration_ms,
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
