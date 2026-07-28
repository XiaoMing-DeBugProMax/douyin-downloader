from dataclasses import dataclass


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
