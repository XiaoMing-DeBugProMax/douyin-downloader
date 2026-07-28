from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI
from fastapi.responses import FileResponse

STATIC_DIR = Path(__file__).with_name("static")


def create_app(*, services: object | None = None, session_manager: object | None = None) -> FastAPI:
    app = FastAPI(title="鎶栭煶瑙嗛涓嬭浇", docs_url=None, redoc_url=None)
    app.state.services = services
    app.state.session_manager = session_manager
    app.state.instance_id = uuid4().hex

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {
            "app": "douyin-local-downloader",
            "status": "ok",
            "instance_id": app.state.instance_id,
        }

    @app.get("/", include_in_schema=False)
    async def home() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    return app
