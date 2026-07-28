from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from douyin_downloader.domain import AppError
from douyin_downloader.session import COOKIE_NAME, SessionManager
from douyin_downloader.web.routes import AppServices, build_router

STATIC_DIR = Path(__file__).with_name("static")


def create_app(
    *,
    services: AppServices | None = None,
    session_manager: SessionManager | None = None,
    testing: bool = False,
) -> FastAPI:
    app = FastAPI(title="抖音视频下载", docs_url=None, redoc_url=None)
    sessions = session_manager if isinstance(session_manager, SessionManager) else SessionManager()
    app.state.services = services
    app.state.session_manager = sessions
    app.state.instance_id = uuid4().hex
    allowed_hosts = ["127.0.0.1", "localhost"]
    if testing:
        allowed_hosts.append("testserver")
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=allowed_hosts,
    )
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
        return FileResponse(STATIC_DIR / "index.html")

    app.include_router(build_router())

    return app
