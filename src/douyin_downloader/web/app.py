from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, RedirectResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from douyin_downloader.session import COOKIE_NAME, SessionManager

STATIC_DIR = Path(__file__).with_name("static")


def create_app(*, services: object | None = None, session_manager: object | None = None) -> FastAPI:
    app = FastAPI(title="抖音视频下载", docs_url=None, redoc_url=None)
    sessions = session_manager if isinstance(session_manager, SessionManager) else SessionManager()
    app.state.services = services
    app.state.session_manager = sessions
    app.state.instance_id = uuid4().hex
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["127.0.0.1", "localhost", "testserver"],
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

    return app
