from __future__ import annotations

import ctypes
import os
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

import httpx

from douyin_downloader.media import open_first_available


@dataclass(frozen=True, slots=True)
class RemoteArtifact:
    content_type: str
    expected_size: int | None
    chunks: AsyncIterator[bytes]
    resume_offset: int = 0
    resume_validator: str | None = None


@dataclass(frozen=True, slots=True)
class RemoteResumeRequest:
    offset: int
    total_size: int
    validator: str


class MediaAccess(Protocol):
    async def open_video(
        self,
        cdn_mirror_urls: tuple[str, ...],
        *,
        resume: RemoteResumeRequest | None = None,
    ) -> RemoteArtifact: ...

    async def open_cover(self, cdn_mirror_urls: tuple[str, ...]) -> RemoteArtifact: ...


class HttpMediaAccess:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def open_video(
        self,
        cdn_mirror_urls: tuple[str, ...],
        *,
        resume: RemoteResumeRequest | None = None,
    ) -> RemoteArtifact:
        return await self._open(cdn_mirror_urls, "video", resume=resume)

    async def open_cover(self, cdn_mirror_urls: tuple[str, ...]) -> RemoteArtifact:
        return await self._open(cdn_mirror_urls, "cover")

    async def _open(
        self,
        cdn_mirror_urls: tuple[str, ...],
        kind: Literal["cover", "video"],
        *,
        resume: RemoteResumeRequest | None = None,
    ) -> RemoteArtifact:
        upstream = None
        if kind == "video" and resume is not None:
            try:
                upstream = await open_first_available(
                    self._client,
                    cdn_mirror_urls,
                    kind,
                    request_headers={
                        "Range": f"bytes={resume.offset}-",
                        "If-Range": resume.validator,
                    },
                    allow_partial=True,
                )
            except Exception:
                upstream = None
            if upstream is not None and upstream.response.status_code == 206:
                if _matches_resume_response(upstream.response, resume):
                    return RemoteArtifact(
                        content_type=upstream.content_type,
                        expected_size=resume.total_size,
                        chunks=upstream.iter_bytes(),
                        resume_offset=resume.offset,
                        resume_validator=resume.validator,
                    )
                await upstream.response.aclose()
                upstream = None
        if upstream is None or upstream.response.status_code != 200:
            upstream = await open_first_available(
                self._client,
                cdn_mirror_urls,
                kind,
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
        return RemoteArtifact(
            content_type=upstream.content_type,
            expected_size=expected_size,
            chunks=upstream.iter_bytes(),
            resume_validator=_response_validator(upstream.response),
        )


def _response_validator(response: httpx.Response) -> str | None:
    raw_etag = response.headers.get("etag")
    etag = str(raw_etag) if raw_etag is not None else None
    if etag and not etag.startswith("W/"):
        return etag
    raw_last_modified = response.headers.get("last-modified")
    return str(raw_last_modified) if raw_last_modified is not None else None


def _matches_resume_response(
    response: httpx.Response,
    resume: RemoteResumeRequest,
) -> bool:
    validator = _response_validator(response)
    match = re.fullmatch(
        r"bytes (\d+)-(\d+)/(\d+)",
        response.headers.get("content-range", ""),
    )
    if validator != resume.validator or match is None:
        return False
    start, end, total = (int(value) for value in match.groups())
    raw_length = response.headers.get("content-length")
    try:
        content_length = int(raw_length) if raw_length is not None else -1
    except ValueError:
        return False
    return (
        start == resume.offset
        and total == resume.total_size
        and end == total - 1
        and content_length == total - start
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


class RecycleBin(Protocol):
    def move_to_recycle_bin(self, path: Path) -> None: ...


class WindowsRecycleBin:
    """Move one validated directory to the Windows Recycle Bin."""

    def __init__(self, shell_operation: object | None = None) -> None:
        self._shell_operation = shell_operation

    def move_to_recycle_bin(self, path: Path) -> None:
        if os.name != "nt":
            raise OSError("recycling archive folders requires Windows")

        class _SHFileOpStruct(ctypes.Structure):
            _fields_ = [
                ("hwnd", ctypes.c_void_p),
                ("wFunc", ctypes.c_uint),
                ("pFrom", ctypes.c_wchar_p),
                ("pTo", ctypes.c_wchar_p),
                ("fFlags", ctypes.c_ushort),
                ("fAnyOperationsAborted", ctypes.c_int),
                ("hNameMappings", ctypes.c_void_p),
                ("lpszProgressTitle", ctypes.c_wchar_p),
            ]

        operation = _SHFileOpStruct()
        operation.wFunc = 3  # FO_DELETE
        operation.pFrom = f"{path}\0\0"
        operation.fFlags = 0x0040 | 0x0010 | 0x0004 | 0x0400
        shell_operation = self._shell_operation
        if shell_operation is None:
            shell_operation = ctypes.windll.shell32.SHFileOperationW
        result = shell_operation(ctypes.byref(operation))  # type: ignore[operator]
        if result != 0 or operation.fAnyOperationsAborted:
            raise OSError(f"Windows Recycle Bin operation failed: {result}")


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
