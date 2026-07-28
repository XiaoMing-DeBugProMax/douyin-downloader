from __future__ import annotations

import ctypes
import hashlib
import json
import os
from ctypes import wintypes
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

APP_DIR_NAME = "DouyinLocalDownloader"
RUNTIME_FILENAME = "runtime.json"
_RUNTIME_FIELDS = {"instance_id", "base_url", "management_token", "pid"}
_ERROR_ALREADY_EXISTS = 183
_WAIT_OBJECT_0 = 0x00000000
_WAIT_ABANDONED = 0x00000080
_WAIT_TIMEOUT = 0x00000102
_WAIT_FAILED = 0xFFFFFFFF
_FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400


class WindowsInstanceMutex:
    def __init__(self, name: str) -> None:
        if os.name != "nt":
            raise RuntimeError("Windows instance mutex requires Windows")
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]
        kernel32.ReleaseMutex.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        handle = kernel32.CreateMutexW(None, True, name)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self._kernel32 = kernel32
        self._handle = int(handle)
        self._is_owner = ctypes.get_last_error() != _ERROR_ALREADY_EXISTS
        self._closed = False

    @property
    def is_owner(self) -> bool:
        return self._is_owner and not self._closed

    def try_acquire(self) -> bool:
        if self._closed:
            raise RuntimeError("instance mutex is closed")
        if self._is_owner:
            return True
        result = int(self._kernel32.WaitForSingleObject(self._handle, 0))
        if result in {_WAIT_OBJECT_0, _WAIT_ABANDONED}:
            self._is_owner = True
            return True
        if result == _WAIT_TIMEOUT:
            return False
        if result == _WAIT_FAILED:
            raise ctypes.WinError(ctypes.get_last_error())
        raise OSError(f"unexpected Windows mutex wait result: {result}")

    def close(self) -> None:
        if self._closed:
            return
        release_error: OSError | None = None
        if self._is_owner and not self._kernel32.ReleaseMutex(self._handle):
            release_error = ctypes.WinError(ctypes.get_last_error())
        self._is_owner = False
        if not self._kernel32.CloseHandle(self._handle) and release_error is None:
            release_error = ctypes.WinError(ctypes.get_last_error())
        self._closed = True
        if release_error is not None:
            raise release_error


@dataclass(frozen=True, slots=True)
class RuntimeInfo:
    instance_id: str
    base_url: str
    management_token: str
    pid: int


class RuntimeStore:
    def __init__(self) -> None:
        local_app_data = os.environ.get("LOCALAPPDATA")
        if not local_app_data:
            raise RuntimeError("LOCALAPPDATA is required")
        local_app_data_path = Path(local_app_data)
        if not local_app_data_path.is_absolute():
            raise RuntimeError("LOCALAPPDATA must be an absolute path")
        self._local_app_data = local_app_data_path.resolve(strict=False)
        self.app_dir = self._local_app_data / APP_DIR_NAME
        self.runtime_path = self.app_dir / RUNTIME_FILENAME
        self._expected_runtime_path = self.runtime_path

    def instance_mutex(self) -> WindowsInstanceMutex:
        digest = hashlib.sha256(str(self.app_dir).casefold().encode("utf-8")).hexdigest()[:24]
        return WindowsInstanceMutex(f"Local\\DouyinLocalDownloader-{digest}")

    def read(self) -> RuntimeInfo | None:
        if not self._path_is_exact():
            return None
        try:
            payload = json.loads(self.runtime_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        return self._parse(payload)

    def write(self, info: RuntimeInfo) -> None:
        if not self._path_is_exact():
            raise RuntimeError("runtime path is outside the application directory")
        self.app_dir.mkdir(parents=True, exist_ok=True)
        if not self._path_is_exact():
            raise RuntimeError("runtime path is outside the application directory")
        temporary_path = self.runtime_path.with_suffix(".json.tmp")
        if _is_reparse_point(temporary_path):
            raise RuntimeError("runtime temporary file must not be a reparse point")
        temporary_path.write_text(
            json.dumps(asdict(info), ensure_ascii=True, separators=(",", ":")),
            encoding="utf-8",
        )
        temporary_path.replace(self.runtime_path)

    def remove_if_owned(self, instance_id: str) -> bool:
        if not self._path_is_exact():
            return False
        info = self.read()
        if info is None or info.instance_id != instance_id:
            return False
        try:
            self.runtime_path.unlink()
        except (FileNotFoundError, OSError):
            return False
        return True

    def _path_is_exact(self) -> bool:
        if (
            self.runtime_path != self._expected_runtime_path
            or self.runtime_path.parent != self.app_dir
            or self.runtime_path.name != RUNTIME_FILENAME
            or _is_reparse_point(self.app_dir)
            or _is_reparse_point(self.runtime_path)
        ):
            return False
        try:
            resolved_app_dir = self.app_dir.resolve(strict=False)
            resolved_runtime = self.runtime_path.resolve(strict=False)
        except OSError:
            return False
        return (
            resolved_app_dir.parent == self._local_app_data
            and resolved_app_dir.name == APP_DIR_NAME
            and resolved_runtime.parent == resolved_app_dir
            and resolved_runtime.name == RUNTIME_FILENAME
        )

    @staticmethod
    def _parse(payload: Any) -> RuntimeInfo | None:
        if not isinstance(payload, dict) or set(payload) != _RUNTIME_FIELDS:
            return None
        instance_id = payload.get("instance_id")
        base_url = payload.get("base_url")
        management_token = payload.get("management_token")
        pid = payload.get("pid")
        if (
            not isinstance(instance_id, str)
            or not instance_id
            or not isinstance(base_url, str)
            or not isinstance(management_token, str)
            or not management_token
            or not isinstance(pid, int)
            or isinstance(pid, bool)
            or pid <= 0
        ):
            return None
        try:
            parsed = urlsplit(base_url)
            port = parsed.port
        except ValueError:
            return None
        if (
            parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or parsed.username is not None
            or parsed.password is not None
            or port is None
            or not 1 <= port <= 65535
            or parsed.netloc != f"127.0.0.1:{port}"
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            return None
        return RuntimeInfo(
            instance_id=instance_id,
            base_url=base_url,
            management_token=management_token,
            pid=pid,
        )


def _is_reparse_point(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attributes = getattr(path.stat(follow_symlinks=False), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & _FILE_ATTRIBUTE_REPARSE_POINT)
