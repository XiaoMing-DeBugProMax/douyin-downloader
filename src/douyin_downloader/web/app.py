from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import RequestResponseEndpoint

from douyin_downloader.domain import AppError
from douyin_downloader.f2_adapter import F2VideoParser
from douyin_downloader.parse_service import ParseService
from douyin_downloader.resources import static_directory, static_resource_path
from douyin_downloader.session import COOKIE_NAME, SessionManager
from douyin_downloader.store import ParseStore
from douyin_downloader.url_resolver import ShareResolver
from douyin_downloader.web.routes import AppServices, build_router

STATIC_DIR = static_directory()


@asynccontextmanager
async def _application_lifespan(app: FastAPI) -> AsyncIterator[None]:
    if app.state.services is not None:
        yield
        return

    client = httpx.AsyncClient(timeout=20)
    app.state.http_client = client
    app.state.services = AppServices(
        parse_service=ParseService(
            ShareResolver(client),
            F2VideoParser(),
            ParseStore(),
        ),
        media_client=client,
    )
    try:
        yield
    finally:
        await client.aclose()


def create_app(
    *,
    services: AppServices | None = None,
    session_manager: SessionManager | None = None,
    expected_port: int | None = None,
    testing: bool = False,
) -> FastAPI:
    if expected_port is None and not testing:
        raise ValueError("production applications require an expected loopback port")
    if expected_port is not None and not 1 <= expected_port <= 65535:
        raise ValueError("expected loopback port must be between 1 and 65535")

    app = FastAPI(title="抖音视频下载", docs_url=None, redoc_url=None)
    app.router.lifespan_context = _application_lifespan
    sessions = session_manager if isinstance(session_manager, SessionManager) else SessionManager()
    app.state.services = services
    app.state.session_manager = sessions
    app.state.instance_id = uuid4().hex
    allowed_authorities = (
        {f"127.0.0.1:{expected_port}", f"localhost:{expected_port}"}
        if expected_port is not None
        else set()
    )
    if testing:
        allowed_authorities.add("testserver")

    @app.middleware("http")
    async def require_expected_authority(
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        if request.headers.get("host") not in allowed_authorities:
            return Response(status_code=403)
        return await call_next(request)

    app.mount("/assets", StaticFiles(directory=STATIC_DIR), name="assets")

    @app.exception_handler(AppError)
    async def app_error_handler(_: Request, error: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=error.status_code,
            content={"error": {"code": error.code, "message": error.message}},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_: Request, __: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "code": "INVALID_INPUT",
                    "message": "没有识别到抖音链接，请粘贴完整分享文案。",
                }
            },
        )

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {
            "app": "douyin-local-downloader",
            "status": "ok",
            "instance_id": app.state.instance_id,
        }

    @app.get("/", include_in_schema=False, response_model=None)
    async def home(
        request: Request,
        launch_token: str | None = Query(default=None),
    ) -> FileResponse | RedirectResponse:
        if launch_token is not None:
            if not sessions.consume_launch_token(launch_token):
                raise HTTPException(status_code=403, detail="INVALID_LAUNCH_TOKEN")
            response = RedirectResponse("/", status_code=303)
            response.set_cookie(
                COOKIE_NAME,
                sessions.cookie_token,
                httponly=True,
                samesite="strict",
                secure=False,
                path="/",
            )
            return response
        return FileResponse(static_resource_path("index.html"))

    app.include_router(build_router())

    return app
