import uvicorn

from douyin_downloader.web.app import create_app


def main() -> None:
    uvicorn.run(create_app(), host="127.0.0.1")
