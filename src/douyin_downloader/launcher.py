from __future__ import annotations

import os
import socket
import threading
import time
import tkinter as tk
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass, field
from tkinter import messagebox
from typing import Protocol
from urllib.parse import urlencode

import httpx
import uvicorn

from douyin_downloader.logging_config import configure_logging, log_operation
from douyin_downloader.runtime import RuntimeInfo, RuntimeStore, WindowsInstanceMutex
from douyin_downloader.session import SessionManager
from douyin_downloader.web.app import create_app

LOOPBACK_HOST = "127.0.0.1"
STARTUP_TIMEOUT_SECONDS = 5.0
STOP_TIMEOUT_SECONDS = 5.0
EXISTING_INSTANCE_TIMEOUT_SECONDS = 1.0
DUPLICATE_WAIT_SECONDS = 7.0

BrowserOpen = Callable[[str], object]
StartNewInstance = Callable[[], int]
ErrorDialog = Callable[[str, str], object]


class ServerStarter(Protocol):
    def start(self) -> RunningServer: ...


class WindowRunner(Protocol):
    def run(self) -> None: ...


ServerFactory = Callable[[RuntimeStore], ServerStarter]


class ServerStartError(RuntimeError):
    def __init__(self, message: str, *, thread_stopped: bool = True) -> None:
        super().__init__(message)
        self.thread_stopped = thread_stopped


@dataclass(slots=True)
class RunningServer:
    host: str
    port: int
    base_url: str
    instance_id: str
    sessions: SessionManager
    server: uvicorn.Server
    thread: threading.Thread
    listener: socket.socket
    runtime_store: RuntimeStore
    _stop_lock: threading.Lock = field(default_factory=threading.Lock)
    _stopped: bool = False

    def stop(self) -> None:
        with self._stop_lock:
            if self._stopped:
                return
            self.server.should_exit = True
            self.thread.join(timeout=STOP_TIMEOUT_SECONDS)
            if self.thread.is_alive():
                self.server.force_exit = True
                self.thread.join(timeout=1)
            if self.thread.is_alive():
                raise RuntimeError("local server did not stop")
            try:
                self.listener.close()
            finally:
                self.runtime_store.remove_if_owned(self.instance_id)
                self._stopped = True


WindowFactory = Callable[[RunningServer, BrowserOpen], WindowRunner]


class LocalServer:
    def __init__(
        self,
        runtime_store: RuntimeStore,
        *,
        startup_timeout: float = STARTUP_TIMEOUT_SECONDS,
    ) -> None:
        self._runtime_store = runtime_store
        self._startup_timeout = startup_timeout

    def start(self) -> RunningServer:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind((LOOPBACK_HOST, 0))
            listener.listen(2048)
        except BaseException:
            listener.close()
            raise

        try:
            port = int(listener.getsockname()[1])
            sessions = SessionManager()
            app = create_app(session_manager=sessions)
            instance_id = str(app.state.instance_id)
            config = uvicorn.Config(
                app,
                host=LOOPBACK_HOST,
                port=port,
                log_config=None,
                access_log=False,
            )
            server = uvicorn.Server(config)
            thread = threading.Thread(
                target=server.run,
                kwargs={"sockets": [listener]},
                name="local-fastapi",
                daemon=False,
            )
            thread.start()
        except BaseException:
            listener.close()
            raise

        deadline = time.monotonic() + self._startup_timeout
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        if not server.started:
            self._cleanup_failed_start(server, thread, listener, instance_id)
            raise ServerStartError("local server did not start")

        running = RunningServer(
            host=LOOPBACK_HOST,
            port=port,
            base_url=f"http://{LOOPBACK_HOST}:{port}",
            instance_id=instance_id,
            sessions=sessions,
            server=server,
            thread=thread,
            listener=listener,
            runtime_store=self._runtime_store,
        )
        try:
            self._runtime_store.write(
                RuntimeInfo(
                    instance_id=instance_id,
                    base_url=running.base_url,
                    management_token=sessions.management_token,
                    pid=os.getpid(),
                )
            )
        except BaseException as error:
            try:
                running.stop()
            except RuntimeError as cleanup_error:
                raise ServerStartError(
                    "could not stop after runtime publication failed",
                    thread_stopped=False,
                ) from cleanup_error
            raise ServerStartError("could not record local server state") from error
        return running

    def _cleanup_failed_start(
        self,
        server: uvicorn.Server,
        thread: threading.Thread,
        listener: socket.socket,
        instance_id: str,
    ) -> None:
        server.should_exit = True
        thread.join(timeout=STOP_TIMEOUT_SECONDS)
        if thread.is_alive():
            server.force_exit = True
            thread.join(timeout=1)
        if thread.is_alive():
            raise ServerStartError("local server thread did not stop", thread_stopped=False)
        listener.close()
        self._runtime_store.remove_if_owned(instance_id)


class ControlWindow:
    def __init__(
        self,
        running_server: RunningServer,
        *,
        browser_open: BrowserOpen = webbrowser.open,
        root: tk.Tk | None = None,
    ) -> None:
        self._running_server = running_server
        self._browser_open = browser_open
        self._root = root if root is not None else tk.Tk()
        self._closed = False

        self._root.title("抖音视频下载")
        self._root.geometry("380x200")
        self._root.resizable(False, False)
        self._root.protocol("WM_DELETE_WINDOW", self.stop_and_exit)

        frame = tk.Frame(self._root, padx=24, pady=20)
        frame.pack(fill="both", expand=True)
        tk.Label(frame, text="本地服务运行中", font=("Microsoft YaHei UI", 13, "bold")).pack(
            pady=(0, 14)
        )
        address = tk.StringVar(master=self._root, value=running_server.base_url)
        tk.Entry(
            frame,
            textvariable=address,
            state="readonly",
            justify="center",
            readonlybackground="white",
        ).pack(fill="x", pady=(0, 18))
        tk.Button(frame, text="重新打开网页", command=self.reopen_browser, width=15).pack(
            side="left", padx=(12, 8)
        )
        tk.Button(frame, text="停止并退出", command=self.stop_and_exit, width=15).pack(
            side="right", padx=(8, 12)
        )

    def reopen_browser(self) -> None:
        open_running_browser(self._running_server, self._browser_open)

    def stop_and_exit(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._running_server.stop()
        finally:
            self._root.destroy()

    def run(self) -> None:
        self._root.mainloop()


def run_or_wake(
    runtime_store: RuntimeStore,
    start_new_instance: StartNewInstance,
    browser_open: BrowserOpen,
) -> int:
    runtime_info = runtime_store.read()
    if runtime_info is not None and _wake_existing_instance(runtime_info, browser_open):
        return 0
    return start_new_instance()


def _wake_existing_instance(runtime_info: RuntimeInfo, browser_open: BrowserOpen) -> bool:
    try:
        with httpx.Client(
            timeout=EXISTING_INSTANCE_TIMEOUT_SECONDS,
            trust_env=False,
            follow_redirects=False,
        ) as client:
            health = client.get(f"{runtime_info.base_url}/api/health")
            if (
                health.status_code != 200
                or health.json().get("instance_id") != runtime_info.instance_id
            ):
                return False
            issued = client.post(
                f"{runtime_info.base_url}/api/internal/launch-token",
                headers={"x-management-token": runtime_info.management_token},
            )
            if issued.status_code != 200:
                return False
            launch_token = issued.json().get("launch_token")
            if not isinstance(launch_token, str) or not launch_token:
                return False
    except (httpx.HTTPError, ValueError, AttributeError):
        return False

    browser_open(_launch_url(runtime_info.base_url, launch_token))
    return True


def _wait_for_existing_or_mutex_ownership(
    runtime_store: RuntimeStore,
    mutex: WindowsInstanceMutex,
    browser_open: BrowserOpen,
) -> bool:
    deadline = time.monotonic() + DUPLICATE_WAIT_SECONDS
    while time.monotonic() < deadline:
        runtime_info = runtime_store.read()
        if runtime_info is not None and _wake_existing_instance(runtime_info, browser_open):
            return True
        if mutex.try_acquire():
            return False
        time.sleep(0.02)
    raise TimeoutError("existing instance did not publish runtime state")


def open_running_browser(running_server: RunningServer, browser_open: BrowserOpen) -> None:
    launch_token = running_server.sessions.issue_launch_token()
    browser_open(_launch_url(running_server.base_url, launch_token))


def _launch_url(base_url: str, launch_token: str) -> str:
    return f"{base_url}/?{urlencode({'launch_token': launch_token})}"


def main(
    *,
    runtime_store: RuntimeStore | None = None,
    server_factory: ServerFactory | None = None,
    window_factory: WindowFactory | None = None,
    browser_open: BrowserOpen | None = None,
    show_error: ErrorDialog | None = None,
) -> int:
    opener = browser_open if browser_open is not None else webbrowser.open
    error_dialog = show_error if show_error is not None else messagebox.showerror
    try:
        logger = configure_logging()
        store = runtime_store if runtime_store is not None else RuntimeStore()
    except (OSError, RuntimeError):
        error_dialog("启动失败", "无法访问当前用户的本地应用目录。")
        return 1

    make_server = server_factory if server_factory is not None else LocalServer

    def default_window_factory(
        running_server: RunningServer,
        selected_browser_open: BrowserOpen,
    ) -> ControlWindow:
        return ControlWindow(running_server, browser_open=selected_browser_open)

    make_window = window_factory if window_factory is not None else default_window_factory
    mutex_safe_to_close = True

    def start_new_instance() -> int:
        nonlocal mutex_safe_to_close
        started_at = time.monotonic()
        try:
            running_server = make_server(store).start()
        except (OSError, RuntimeError) as error:
            log_operation(
                logger,
                operation="application_start",
                error_code="START_FAILED",
                elapsed_ms=int((time.monotonic() - started_at) * 1000),
                bytes_streamed=0,
            )
            if isinstance(error, ServerStartError) and not error.thread_stopped:
                mutex_safe_to_close = False
            error_dialog("启动失败", "本地服务启动失败，请重试。")
            return 1
        log_operation(
            logger,
            operation="application_start",
            error_code="-",
            elapsed_ms=int((time.monotonic() - started_at) * 1000),
            bytes_streamed=0,
        )
        try:
            open_running_browser(running_server, opener)
            make_window(running_server, opener).run()
        finally:
            stopped_at = time.monotonic()
            stop_error_code = "-"
            try:
                running_server.stop()
            except RuntimeError:
                mutex_safe_to_close = False
                stop_error_code = "STOP_FAILED"
                raise
            finally:
                log_operation(
                    logger,
                    operation="application_stop",
                    error_code=stop_error_code,
                    elapsed_ms=int((time.monotonic() - stopped_at) * 1000),
                    bytes_streamed=0,
                )
        return 0

    try:
        mutex = store.instance_mutex()
    except OSError:
        error_dialog("启动失败", "无法建立本地单实例锁，请重试。")
        return 1

    try:
        if not mutex.is_owner:
            try:
                woke_existing = _wait_for_existing_or_mutex_ownership(store, mutex, opener)
            except (OSError, TimeoutError):
                error_dialog("启动失败", "已有实例正在启动，请稍后重试。")
                return 1
            if woke_existing:
                return 0

        runtime_info = store.read()
        if runtime_info is not None and _wake_existing_instance(runtime_info, opener):
            return 0
        return start_new_instance()
    finally:
        if mutex_safe_to_close:
            mutex.close()
