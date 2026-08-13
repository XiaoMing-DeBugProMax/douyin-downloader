from __future__ import annotations

import ctypes
import json
import os
import socket
import subprocess
import time
from ctypes import wintypes
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest

WM_CLOSE = 0x0010


def _close_visible_window_for_pid(pid: int) -> bool:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    windows: list[int] = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @callback_type
    def collect(window: int, _: int) -> bool:
        process_id = wintypes.DWORD()
        user32.GetWindowThreadProcessId(window, ctypes.byref(process_id))
        if process_id.value == pid and user32.IsWindowVisible(window):
            windows.append(int(window))
        return True

    user32.EnumWindows(collect, 0)
    for window in windows:
        if user32.PostMessageW(window, WM_CLOSE, 0, 0):
            return True
    return False


@pytest.mark.skipif(os.name != "nt", reason="Windows packaged runtime smoke test")
def test_packaged_application_starts_on_loopback_and_releases_runtime(tmp_path: Path) -> None:
    executable_value = os.environ.get("DOUYIN_BUILT_EXE")
    if executable_value is None:
        pytest.skip("built executable was not explicitly selected")
    executable = Path(executable_value)
    assert executable.is_file()
    local_app_data = tmp_path / "LocalAppData"
    local_app_data.mkdir()
    environment = os.environ.copy()
    environment["LOCALAPPDATA"] = str(local_app_data)
    process = subprocess.Popen(  # noqa: S603
        [str(executable)],
        env=environment,
    )
    runtime_path = local_app_data / "DouyinLocalDownloader" / "runtime.json"
    runtime: dict[str, object] | None = None
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and process.poll() is None:
            try:
                runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                time.sleep(0.05)
                continue
            break
        assert runtime is not None
        base_url = str(runtime["base_url"])
        parsed = urlsplit(base_url)
        assert parsed.hostname == "127.0.0.1"
        assert parsed.port is not None
        health = httpx.get(f"{base_url}/api/health", timeout=2, trust_env=False)
        assert health.status_code == 200

        duplicate = subprocess.run(  # noqa: S603
            [str(executable)],
            env=environment,
            timeout=10,
            check=False,
        )
        assert duplicate.returncode == 0
        persisted = json.loads(runtime_path.read_text(encoding="utf-8"))
        assert persisted["instance_id"] == runtime["instance_id"]

        close_deadline = time.monotonic() + 10
        while not _close_visible_window_for_pid(int(runtime["pid"])):
            assert time.monotonic() < close_deadline
            time.sleep(0.05)
        assert process.wait(timeout=10) == 0
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    deadline = time.monotonic() + 5
    while runtime_path.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not runtime_path.exists()
    if runtime is not None:
        port = urlsplit(str(runtime["base_url"])).port
        assert port is not None
        rebound = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            rebound.bind(("127.0.0.1", port))
        finally:
            rebound.close()
