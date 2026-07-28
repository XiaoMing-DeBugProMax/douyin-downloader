from __future__ import annotations

import multiprocessing
import os
import secrets
import socket
import time
from pathlib import Path
from queue import Empty
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

import douyin_downloader.launcher as launcher_module
from douyin_downloader.launcher import (
    LocalServer,
    RunningServer,
    ServerStartError,
    main,
    run_or_wake,
)
from douyin_downloader.runtime import RuntimeInfo, RuntimeStore
from douyin_downloader.session import SessionManager


def _simultaneous_main_worker(
    local_app_data: str,
    start_gate: multiprocessing.synchronize.Event,
    stop_window: multiprocessing.synchronize.Event,
    messages: multiprocessing.queues.Queue,
) -> None:
    os.environ["LOCALAPPDATA"] = local_app_data
    store = RuntimeStore()

    def server_factory(selected_store: RuntimeStore) -> LocalServer:
        messages.put(("server", os.getpid()))
        time.sleep(0.3)
        return LocalServer(selected_store)

    class WaitingWindow:
        def __init__(self, running_server: RunningServer) -> None:
            self._running_server = running_server

        def run(self) -> None:
            messages.put(("window", os.getpid(), self._running_server.port))
            if not stop_window.wait(10):
                raise RuntimeError("multiprocess test window timed out")

    start_gate.wait(5)
    result = main(
        runtime_store=store,
        server_factory=server_factory,
        window_factory=lambda running, _: WaitingWindow(running),
        browser_open=lambda _: messages.put(("browser", os.getpid())),
        show_error=lambda title, message: messages.put(("error", title, message)),
    )
    messages.put(("exit", os.getpid(), result))


@pytest.fixture
def runtime_store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> RuntimeStore:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))
    return RuntimeStore()


def test_server_binds_loopback_writes_runtime_and_stops_idempotently(
    runtime_store: RuntimeStore,
) -> None:
    running_server = LocalServer(runtime_store).start()
    try:
        assert running_server.host == "127.0.0.1"
        assert running_server.base_url.startswith("http://127.0.0.1:")
        response = httpx.get(f"{running_server.base_url}/api/health", timeout=1, trust_env=False)
        assert response.status_code == 200
        assert response.json()["instance_id"] == running_server.instance_id
        runtime_info = runtime_store.read()
        assert runtime_info is not None
        assert runtime_info.instance_id == running_server.instance_id
        assert runtime_info.base_url == running_server.base_url

        running_server.stop()
        running_server.stop()

        assert not running_server.thread.is_alive()
        assert runtime_store.read() is None
        with pytest.raises((httpx.ConnectError, httpx.ConnectTimeout)):
            httpx.get(
                f"{running_server.base_url}/api/health",
                timeout=0.5,
                trust_env=False,
            )
        rebound = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            rebound.bind((running_server.host, running_server.port))
        finally:
            rebound.close()
    finally:
        running_server.stop()


def test_startup_failure_closes_prebound_socket_and_leaves_no_runtime(
    runtime_store: RuntimeStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class NeverStartsServer:
        def __init__(self, _: object) -> None:
            self.started = False
            self.should_exit = False
            self.force_exit = False
            self.listener: socket.socket | None = None

        def run(self, *, sockets: list[socket.socket]) -> None:
            self.listener = sockets[0]

    fake_server = NeverStartsServer(object())
    monkeypatch.setattr(launcher_module.uvicorn, "Server", lambda _: fake_server)

    with pytest.raises(ServerStartError, match="did not start"):
        LocalServer(runtime_store, startup_timeout=0.05).start()

    assert fake_server.should_exit is True
    assert fake_server.listener is not None
    assert fake_server.listener.fileno() == -1
    assert runtime_store.read() is None


def test_configuration_failure_closes_prebound_socket(
    runtime_store: RuntimeStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_socket = socket.socket
    created_sockets: list[socket.socket] = []

    def recording_socket(*args: object, **kwargs: object) -> socket.socket:
        listener = real_socket(*args, **kwargs)
        created_sockets.append(listener)
        return listener

    def fail_config(*_: object, **__: object) -> None:
        raise OSError("simulated uvicorn configuration failure")

    monkeypatch.setattr(launcher_module.socket, "socket", recording_socket)
    monkeypatch.setattr(launcher_module.uvicorn, "Config", fail_config)

    with pytest.raises(OSError, match="configuration failure"):
        LocalServer(runtime_store).start()

    assert len(created_sockets) == 1
    assert created_sockets[0].fileno() == -1
    assert runtime_store.read() is None


def test_uncooperative_server_stop_keeps_runtime_and_remains_retryable(
    runtime_store: RuntimeStore,
) -> None:
    info = RuntimeInfo(
        instance_id="uncooperative-owner",
        base_url="http://127.0.0.1:45678",
        management_token=secrets.token_urlsafe(32),
        pid=os.getpid(),
    )
    runtime_store.write(info)

    class UncooperativeThread:
        def __init__(self) -> None:
            self.join_calls = 0

        def join(self, timeout: float | None = None) -> None:
            self.join_calls += 1

        def is_alive(self) -> bool:
            return True

    fake_thread = UncooperativeThread()
    fake_server = SimpleNamespace(should_exit=False, force_exit=False)
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    running = RunningServer(
        host="127.0.0.1",
        port=45678,
        base_url=info.base_url,
        instance_id=info.instance_id,
        sessions=SessionManager(),
        server=fake_server,  # type: ignore[arg-type]
        thread=fake_thread,  # type: ignore[arg-type]
        listener=listener,
        runtime_store=runtime_store,
    )
    try:
        with pytest.raises(RuntimeError, match="did not stop"):
            running.stop()
        assert fake_server.should_exit is True
        assert fake_server.force_exit is True
        assert runtime_store.read() == info
        assert running._stopped is False

        with pytest.raises(RuntimeError, match="did not stop"):
            running.stop()
        assert fake_thread.join_calls == 4
        assert runtime_store.read() == info
    finally:
        listener.close()
        runtime_store.remove_if_owned(info.instance_id)


def test_failed_start_forces_uncooperative_thread_to_exit(
    runtime_store: RuntimeStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_server = SimpleNamespace(
        started=False,
        should_exit=False,
        force_exit=False,
        run=lambda **_: None,
    )

    class ForceExitThread:
        def __init__(self, **_: object) -> None:
            self.join_calls = 0

        def start(self) -> None:
            pass

        def join(self, timeout: float | None = None) -> None:
            self.join_calls += 1

        def is_alive(self) -> bool:
            return not fake_server.force_exit

    fake_thread = ForceExitThread()
    monkeypatch.setattr(launcher_module.uvicorn, "Server", lambda _: fake_server)
    monkeypatch.setattr(launcher_module.threading, "Thread", lambda **_: fake_thread)

    with pytest.raises(ServerStartError, match="did not start"):
        LocalServer(runtime_store, startup_timeout=0).start()

    assert fake_server.should_exit is True
    assert fake_server.force_exit is True
    assert fake_thread.join_calls == 2
    assert not fake_thread.is_alive()
    assert runtime_store.read() is None


def test_duplicate_invocation_only_wakes_existing_instance(
    runtime_store: RuntimeStore,
) -> None:
    running_server = LocalServer(runtime_store).start()
    opened_urls: list[str] = []
    start_calls: list[bool] = []

    def forbidden_server_factory(_: RuntimeStore) -> LocalServer:
        start_calls.append(True)
        raise AssertionError("duplicate invocation must not create a LocalServer")

    try:
        result = main(
            runtime_store=runtime_store,
            server_factory=forbidden_server_factory,
            window_factory=lambda *_: pytest.fail("duplicate invocation must not create a window"),
            browser_open=opened_urls.append,
            show_error=lambda *_: pytest.fail("duplicate invocation must not show an error"),
        )

        assert result == 0
        assert start_calls == []
        assert len(opened_urls) == 1
        browser_url = opened_urls[0]
        parsed = urlsplit(browser_url)
        assert parsed.scheme == "http"
        assert parsed.hostname == "127.0.0.1"
        assert parsed.port == running_server.port
        assert set(parse_qs(parsed.query)) == {"launch_token"}
        runtime_info = runtime_store.read()
        assert runtime_info is not None
        assert runtime_info.management_token not in browser_url

        launched = httpx.get(
            browser_url,
            timeout=1,
            trust_env=False,
            follow_redirects=False,
        )
        assert launched.status_code == 303
        assert launched.headers["location"] == "/"
    finally:
        running_server.stop()


def test_stale_runtime_with_wrong_instance_id_starts_new_and_is_not_removed_by_old_owner(
    runtime_store: RuntimeStore,
) -> None:
    running_server = LocalServer(runtime_store).start()
    active_info = runtime_store.read()
    assert active_info is not None
    stale_info = RuntimeInfo(
        instance_id="stale-owner",
        base_url=active_info.base_url,
        management_token=active_info.management_token,
        pid=active_info.pid,
    )
    runtime_store.write(stale_info)
    start_calls: list[bool] = []

    try:
        result = run_or_wake(
            runtime_store,
            lambda: start_calls.append(True) or 17,
            lambda _: pytest.fail("stale runtime must not open the browser"),
        )

        assert result == 17
        assert start_calls == [True]
        running_server.stop()
        assert runtime_store.read() == stale_info
    finally:
        running_server.stop()
        runtime_store.remove_if_owned(stale_info.instance_id)


@pytest.mark.skipif(os.name != "nt", reason="Windows named mutex contract")
def test_simultaneous_processes_construct_exactly_one_server_and_window(
    runtime_store: RuntimeStore,
) -> None:
    context = multiprocessing.get_context("spawn")
    start_gate = context.Event()
    stop_window = context.Event()
    messages = context.Queue()
    workers = [
        context.Process(
            target=_simultaneous_main_worker,
            args=(str(runtime_store.app_dir.parent), start_gate, stop_window, messages),
        )
        for _ in range(2)
    ]
    received: list[tuple[object, ...]] = []
    try:
        for worker in workers:
            worker.start()
        start_gate.set()

        deadline = time.monotonic() + 12
        while not any(message[0] == "exit" for message in received):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                pytest.fail(f"duplicate process did not exit; messages={received!r}")
            try:
                received.append(messages.get(timeout=min(0.5, remaining)))
            except Empty:
                continue

        assert sum(message[0] == "server" for message in received) == 1
        assert sum(message[0] == "window" for message in received) == 1
        assert not any(message[0] == "error" for message in received)

        stop_window.set()
        for worker in workers:
            worker.join(timeout=10)
            assert worker.exitcode == 0
        while True:
            try:
                received.append(messages.get_nowait())
            except Empty:
                break

        assert sum(message[0] == "server" for message in received) == 1
        windows = [message for message in received if message[0] == "window"]
        assert len(windows) == 1
        assert sum(message[0] == "exit" and message[2] == 0 for message in received) == 2
        assert sum(message[0] == "browser" for message in received) == 2
        assert runtime_store.read() is None
        rebound = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            rebound.bind(("127.0.0.1", int(windows[0][2])))
        finally:
            rebound.close()
    finally:
        stop_window.set()
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
            worker.join(timeout=5)
        messages.close()
        messages.join_thread()
