from __future__ import annotations

import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import httpx

from douyin_downloader.media import open_first_available


@dataclass(frozen=True, slots=True)
class RemoteVideo:
    content_type: str
    expected_size: int | None
    chunks: AsyncIterator[bytes]


class MediaAccess(Protocol):
    async def open_video(self, cdn_mirror_urls: tuple[str, ...]) -> RemoteVideo: ...


class HttpMediaAccess:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def open_video(self, cdn_mirror_urls: tuple[str, ...]) -> RemoteVideo:
        upstream = await open_first_available(
            self._client,
            cdn_mirror_urls,
            "video",
        )
        raw_length = upstream.response.headers.get("content-length")
        expected_size: int | None = None
        if raw_length is not None:
            try:
                parsed_length = int(raw_length)
            except ValueError:
                parsed_length = 0
            if parsed_length > 0:
                expected_size = parsed_length
        return RemoteVideo(
            content_type=upstream.content_type,
            expected_size=expected_size,
            chunks=upstream.iter_bytes(),
        )


class FolderOpener(Protocol):
    def open_folder(self, path: Path) -> None: ...


class WindowsFolderOpener:
    def open_folder(self, path: Path) -> None:
        if os.name != "nt":
            raise OSError("opening archive folders requires Windows")
        os.startfile(path)  # noqa: S606 - validated registered local path


class FilePromoter(Protocol):
    def promote(self, part_path: Path, final_path: Path) -> None: ...


class AtomicFilePromoter:
    def promote(self, part_path: Path, final_path: Path) -> None:
        part_path.replace(final_path)


class WindowsDirectoryChooser:
    def choose_directory(self) -> Path | None:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        try:
            selected = filedialog.askdirectory(
                parent=root,
                title="选择本地归档目录",
                mustexist=True,
            )
        finally:
            root.destroy()
        return Path(selected) if selected else None
