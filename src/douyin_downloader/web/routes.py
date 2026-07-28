from dataclasses import dataclass

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from douyin_downloader.domain import AppError
from douyin_downloader.parse_service import ParseService
from douyin_downloader.session import require_local_session, require_same_origin


@dataclass(slots=True)
class AppServices:
    parse_service: ParseService


class ParseRequest(BaseModel):
    share_text: str = Field(min_length=1, max_length=2000)


class VideoResponse(BaseModel):
    aweme_id: str
    author: str
    description: str
    duration_ms: int
    cover_url: str


class ParseResponse(BaseModel):
    parse_token: str
    video: VideoResponse


def build_router() -> APIRouter:
    router = APIRouter()

    @router.post(
        "/api/parse",
        response_model=ParseResponse,
        dependencies=[Depends(require_local_session), Depends(require_same_origin)],
    )
    async def parse_video(payload: ParseRequest, request: Request) -> ParseResponse:
        services: AppServices | None = request.app.state.services
        if services is None:
            raise AppError("UPSTREAM_BLOCKED", "解析服务暂时不可用，请稍后重试。", 502)
        result = await services.parse_service.parse(payload.share_text)
        return ParseResponse(
            parse_token=result.parse_token,
            video=VideoResponse(
                aweme_id=result.video.aweme_id,
                author=result.video.author,
                description=result.video.description,
                duration_ms=result.video.duration_ms,
                cover_url=f"/api/cover/{result.parse_token}",
            ),
        )

    return router
