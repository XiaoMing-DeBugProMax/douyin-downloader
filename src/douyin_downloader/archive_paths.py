from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType

from douyin_downloader.domain import AppError, ResolvedWork

_FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
_FILE_ATTRIBUTE_DIRECTORY = 0x00000010
_FILE_READ_ATTRIBUTES = 0x00000080
_DELETE_ACCESS = 0x00010000
_FILE_SHARE_READ = 0x00000001
_FILE_SHARE_WRITE = 0x00000002
_FILE_SHARE_DELETE = 0x00000004
_OPEN_EXISTING = 3
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_WINDOWS_LONG_PATH_CAPACITY = 32_768


def work_directory(resolved: ResolvedWork) -> Path:
    author_digest = hashlib.sha256(
        resolved.snapshot.author.stable_id.encode("utf-8")
    ).hexdigest()[:16]
    published_at = resolved.snapshot.published_at
    year = (
        str(datetime.fromtimestamp(published_at, UTC).year)
        if published_at is not None
        else "unknown"
    )
    return Path(f"author-{author_digest}") / year / f"work-{resolved.snapshot.aweme_id}"


@dataclass(slots=True)
class PinnedWorkDirectory:
    path: Path
    _handles: list[int]

    def __enter__(self) -> Path:
        return self.path

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback
        for handle in reversed(self._handles):
            _close_directory_handle(handle)


def pin_work_directory(
    root: Path,
    relative_directory: Path,
    *,
    create: bool,
    share_delete: bool = False,
) -> PinnedWorkDirectory:
    if relative_directory.is_absolute() or ".." in relative_directory.parts:
        raise archive_path_invalid()
    handles: list[int] = []
    try:
        resolved_root = root.resolve(strict=True)
        root_handle, pinned_root = _open_directory_handle(
            resolved_root, share_delete=share_delete
        )
        handles.append(root_handle)
        if pinned_root != resolved_root:
            raise archive_path_invalid()

        current = resolved_root
        for segment in relative_directory.parts:
            candidate = current / segment
            if create and not candidate.exists() and not candidate.is_symlink():
                candidate.mkdir()
            if is_reparse_point(candidate) or not candidate.is_dir():
                raise archive_path_invalid()
            handle, pinned_path = _open_directory_handle(
                candidate, share_delete=share_delete
            )
            handles.append(handle)
            if (
                pinned_path != candidate.resolve(strict=True)
                or not pinned_path.is_relative_to(resolved_root)
            ):
                raise archive_path_invalid()
            current = pinned_path
        return PinnedWorkDirectory(current, handles)
    except (OSError, AppError) as error:
        for handle in reversed(handles):
            _close_directory_handle(handle)
        if isinstance(error, AppError):
            raise
        raise archive_path_invalid() from error


def is_reparse_point(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attributes = getattr(path.stat(follow_symlinks=False), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & _FILE_ATTRIBUTE_REPARSE_POINT)


def archive_path_invalid() -> AppError:
    return AppError("ARCHIVE_PATH_INVALID", "归档路径无效。", 409)


def _open_directory_handle(path: Path, *, share_delete: bool = False) -> tuple[int, Path]:
    if os.name != "nt":
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        handle = os.open(path, flags)
        return handle, path.resolve(strict=True)

    import ctypes
    from ctypes import wintypes

    class ByHandleFileInformation(ctypes.Structure):
        _fields_ = [
            ("dwFileAttributes", wintypes.DWORD),
            ("ftCreationTime", wintypes.FILETIME),
            ("ftLastAccessTime", wintypes.FILETIME),
            ("ftLastWriteTime", wintypes.FILETIME),
            ("dwVolumeSerialNumber", wintypes.DWORD),
            ("nFileSizeHigh", wintypes.DWORD),
            ("nFileSizeLow", wintypes.DWORD),
            ("nNumberOfLinks", wintypes.DWORD),
            ("nFileIndexHigh", wintypes.DWORD),
            ("nFileIndexLow", wintypes.DWORD),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    share_mode = _FILE_SHARE_READ | _FILE_SHARE_WRITE
    if share_delete:
        share_mode |= _FILE_SHARE_DELETE
    raw_handle = create_file(
        str(path),
        _FILE_READ_ATTRIBUTES | _DELETE_ACCESS,
        share_mode,
        None,
        _OPEN_EXISTING,
        _FILE_FLAG_BACKUP_SEMANTICS | _FILE_FLAG_OPEN_REPARSE_POINT,
        None,
    )
    invalid_handle = ctypes.c_void_p(-1).value
    if raw_handle == invalid_handle:
        raise OSError(ctypes.get_last_error(), f"cannot pin directory: {path}")
    handle = int(raw_handle)
    try:
        information = ByHandleFileInformation()
        get_information = kernel32.GetFileInformationByHandle
        get_information.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(ByHandleFileInformation),
        ]
        get_information.restype = wintypes.BOOL
        if not get_information(wintypes.HANDLE(handle), ctypes.byref(information)):
            raise OSError(ctypes.get_last_error(), "cannot inspect directory handle")
        if (
            information.dwFileAttributes & _FILE_ATTRIBUTE_REPARSE_POINT
            or not information.dwFileAttributes & _FILE_ATTRIBUTE_DIRECTORY
        ):
            raise archive_path_invalid()

        buffer = ctypes.create_unicode_buffer(_WINDOWS_LONG_PATH_CAPACITY)
        get_final_path = kernel32.GetFinalPathNameByHandleW
        get_final_path.argtypes = [
            wintypes.HANDLE,
            wintypes.LPWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
        ]
        get_final_path.restype = wintypes.DWORD
        length = get_final_path(
            wintypes.HANDLE(handle),
            buffer,
            len(buffer),
            0,
        )
        if length == 0 or length >= len(buffer):
            raise OSError(ctypes.get_last_error(), "cannot resolve directory handle")
        return handle, Path(_strip_windows_device_prefix(buffer.value))
    except BaseException:
        _close_directory_handle(handle)
        raise


def _close_directory_handle(handle: int) -> None:
    if os.name != "nt":
        os.close(handle)
        return
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CloseHandle(wintypes.HANDLE(handle))


def _strip_windows_device_prefix(path: str) -> str:
    if path.startswith("\\\\?\\UNC\\"):
        return "\\\\" + path[8:]
    if path.startswith("\\\\?\\"):
        return path[4:]
    return path
