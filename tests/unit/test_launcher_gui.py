from __future__ import annotations

import runpy
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

import douyin_downloader.launcher as launcher_module
from douyin_downloader.launcher import ControlWindow, ServerStartError, main
from douyin_downloader.runtime import RuntimeStore
from douyin_downloader.session import SessionManager


class FakeRoot:
    def __init__(self) -> None:
        self.window_title = ""
        self.window_geometry = ""
        self.resizable_value: tuple[bool, bool] | None = None
        self.protocols: dict[str, object] = {}
        self.mainloop_calls = 0
        self.destroy_calls = 0

    def title(self, value: str) -> None:
        self.window_title = value

    def geometry(self, value: str) -> None:
        self.window_geometry = value

    def resizable(self, width: bool, height: bool) -> None:
        self.resizable_value = (width, height)

    def protocol(self, name: str, callback: object) -> None:
        self.protocols[name] = callback

    def mainloop(self) -> None:
        self.mainloop_calls += 1

    def destroy(self) -> None:
        self.destroy_calls += 1


class FakeWidget:
    def __init__(self, kind: str, _: object, **kwargs: object) -> None:
        self.kind = kind
        self.kwargs = kwargs
        self.pack_options: dict[str, object] = {}

    def pack(self, **kwargs: object) -> None:
        self.pack_options = kwargs


class FakeStringVar:
    def __init__(self, *, master: object, value: str) -> None:
        self.master = master
        self.value = value


class FakeRunningServer:
    def __init__(self) -> None:
        self.base_url = "http://127.0.0.1:45678"
        self.sessions = SessionManager()
        self.stop_calls = 0

    def stop(self) -> None:
        self.stop_calls += 1


def install_fake_tk(monkeypatch: pytest.MonkeyPatch) -> list[FakeWidget]:
    widgets: list[FakeWidget] = []

    def constructor(kind: str) -> object:
        def create(master: object, **kwargs: object) -> FakeWidget:
            widget = FakeWidget(kind, master, **kwargs)
            widgets.append(widget)
            return widget

        return create

    monkeypatch.setattr(launcher_module.tk, "Frame", constructor("frame"))
    monkeypatch.setattr(launcher_module.tk, "Label", constructor("label"))
    monkeypatch.setattr(launcher_module.tk, "Entry", constructor("entry"))
    monkeypatch.setattr(launcher_module.tk, "Button", constructor("button"))
    monkeypatch.setattr(launcher_module.tk, "StringVar", FakeStringVar)
    return widgets


def test_control_window_renders_confirmed_copy_and_wires_actions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    widgets = install_fake_tk(monkeypatch)
    root = FakeRoot()
    running = FakeRunningServer()
    opened_urls: list[str] = []

    window = ControlWindow(
        running,  # type: ignore[arg-type]
        browser_open=opened_urls.append,
        root=root,  # type: ignore[arg-type]
    )

    assert root.window_title == "抖音视频下载"
    assert root.window_geometry == "380x200"
    assert root.resizable_value == (False, False)
    assert "WM_DELETE_WINDOW" in root.protocols
    assert any(
        widget.kind == "label" and widget.kwargs.get("text") == "本地服务运行中"
        for widget in widgets
    )
    address = next(widget for widget in widgets if widget.kind == "entry")
    assert address.kwargs["state"] == "readonly"
    assert address.kwargs["textvariable"].value == running.base_url  # type: ignore[union-attr]
    buttons = {
        str(widget.kwargs["text"]): widget
        for widget in widgets
        if widget.kind == "button"
    }
    assert set(buttons) == {"重新打开网页", "停止并退出"}

    reopen = buttons["重新打开网页"].kwargs["command"]
    assert callable(reopen)
    reopen()
    assert len(opened_urls) == 1
    query = parse_qs(urlsplit(opened_urls[0]).query)
    assert set(query) == {"launch_token"}
    assert running.sessions.consume_launch_token(query["launch_token"][0])

    stop = buttons["停止并退出"].kwargs["command"]
    assert callable(stop)
    stop()
    stop()
    assert running.stop_calls == 1
    assert root.destroy_calls == 1

    window.run()
    assert root.mainloop_calls == 1


def test_main_runs_new_instance_in_order_and_always_stops(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))
    store = RuntimeStore()
    events: list[str] = []
    running = FakeRunningServer()

    class FakeLogger:
        def info(self, _: str, *, extra: dict[str, object]) -> None:
            events.append(f"log:{extra['operation']}")

    def configure_logging() -> FakeLogger:
        events.append("logging")
        return FakeLogger()

    monkeypatch.setattr(launcher_module, "configure_logging", configure_logging)

    def stop() -> None:
        running.stop_calls += 1
        events.append("stop")

    running.stop = stop  # type: ignore[method-assign]

    class FakeStarter:
        def start(self) -> FakeRunningServer:
            events.append("start")
            return running

    class FakeWindow:
        def run(self) -> None:
            events.append("window")

    result = main(
        runtime_store=store,
        server_factory=lambda _: FakeStarter(),  # type: ignore[arg-type,return-value]
        window_factory=lambda *_: FakeWindow(),
        browser_open=lambda _: events.append("browser"),
        show_error=lambda *_: pytest.fail("successful startup must not show an error"),
    )

    assert result == 0
    assert events == [
        "logging",
        "start",
        "log:application_start",
        "browser",
        "window",
        "stop",
        "log:application_stop",
    ]
    assert running.stop_calls == 1


def test_main_reports_startup_failure_in_chinese_and_returns_nonzero(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))
    errors: list[tuple[str, str]] = []

    class FailingStarter:
        def start(self) -> None:
            raise ServerStartError("details that must not be shown")

    result = main(
        runtime_store=RuntimeStore(),
        server_factory=lambda _: FailingStarter(),  # type: ignore[arg-type,return-value]
        window_factory=lambda *_: pytest.fail("failed startup must not create a window"),
        browser_open=lambda _: pytest.fail("failed startup must not open a browser"),
        show_error=lambda title, message: errors.append((title, message)),
    )

    assert result == 1
    assert errors == [("启动失败", "本地服务启动失败，请重试。")]


def test_main_reports_logging_permission_failure_without_starting_server(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))
    errors: list[tuple[str, str]] = []
    starts: list[str] = []

    def fail_logging() -> None:
        raise PermissionError("private filesystem detail")

    monkeypatch.setattr(launcher_module, "configure_logging", fail_logging)

    result = main(
        runtime_store=RuntimeStore(),
        server_factory=lambda _: starts.append("started"),  # type: ignore[arg-type,return-value]
        browser_open=lambda _: pytest.fail("logging failure must not open a browser"),
        show_error=lambda title, message: errors.append((title, message)),
    )

    assert result == 1
    assert starts == []
    assert errors == [("启动失败", "无法访问当前用户的本地应用目录。")]


def test_python_module_entrypoint_exits_with_main_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(launcher_module, "main", lambda: 23)

    with pytest.raises(SystemExit) as exit_info:
        runpy.run_module("douyin_downloader", run_name="__main__")

    assert exit_info.value.code == 23
