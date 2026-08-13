from __future__ import annotations

import runpy
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

import douyin_downloader.launcher as launcher_module
from douyin_downloader.launcher import (
    ControlWindow,
    ServerStartError,
    TrayActions,
    WindowsTray,
    main,
)
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
        self.withdraw_calls = 0
        self.deiconify_calls = 0
        self.lift_calls = 0
        self.after_callbacks: list[object] = []

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

    def withdraw(self) -> None:
        self.withdraw_calls += 1

    def deiconify(self) -> None:
        self.deiconify_calls += 1

    def lift(self) -> None:
        self.lift_calls += 1

    def after(self, _: int, callback: object) -> None:
        self.after_callbacks.append(callback)

    def run_after_callback(self) -> None:
        callback = self.after_callbacks.pop(0)
        assert callable(callback)
        callback()


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
        self.active_tasks = False
        self.pause_all_calls = 0
        self.interrupt_all_calls = 0
        self.fail_active_query = False

    def stop(self) -> None:
        self.stop_calls += 1

    def has_active_tasks(self) -> bool:
        if self.fail_active_query:
            raise OSError("server unavailable")
        return self.active_tasks

    def pause_all(self) -> None:
        self.pause_all_calls += 1

    def interrupt_all(self) -> None:
        self.interrupt_all_calls += 1


class FakeTray:
    def __init__(self) -> None:
        self.actions: object | None = None
        self.start_calls = 0
        self.stop_calls = 0
        self.fail_start = False

    def start(self, actions: object) -> None:
        if self.fail_start:
            raise OSError("tray unavailable")
        self.actions = actions
        self.start_calls += 1

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


def test_idle_window_close_stops_without_prompting_or_starting_tray(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_tk(monkeypatch)
    root = FakeRoot()
    running = FakeRunningServer()
    tray = FakeTray()
    prompts: list[bool] = []
    window = ControlWindow(
        running,  # type: ignore[arg-type]
        root=root,  # type: ignore[arg-type]
        close_prompt=lambda _: prompts.append(True) or "cancel",
        tray=tray,  # type: ignore[arg-type]
    )

    window.request_close()

    assert prompts == []
    assert tray.start_calls == 0
    assert running.interrupt_all_calls == 0
    assert running.stop_calls == 1
    assert root.destroy_calls == 1


def test_stop_still_releases_window_when_active_query_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_tk(monkeypatch)
    root = FakeRoot()
    running = FakeRunningServer()
    running.fail_active_query = True
    window = ControlWindow(
        running,  # type: ignore[arg-type]
        root=root,  # type: ignore[arg-type]
        tray=FakeTray(),  # type: ignore[arg-type]
    )

    window.stop_and_exit()

    assert running.stop_calls == 1
    assert root.destroy_calls == 1


def test_active_window_close_can_be_cancelled_without_changing_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_tk(monkeypatch)
    root = FakeRoot()
    running = FakeRunningServer()
    running.active_tasks = True
    tray = FakeTray()
    window = ControlWindow(
        running,  # type: ignore[arg-type]
        root=root,  # type: ignore[arg-type]
        close_prompt=lambda _: "cancel",
        tray=tray,  # type: ignore[arg-type]
    )

    window.request_close()

    assert tray.start_calls == 0
    assert running.pause_all_calls == 0
    assert running.interrupt_all_calls == 0
    assert running.stop_calls == 0
    assert root.withdraw_calls == 0
    assert root.destroy_calls == 0


def test_active_window_can_continue_in_tray_and_reopen_same_control_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_tk(monkeypatch)
    root = FakeRoot()
    running = FakeRunningServer()
    running.active_tasks = True
    tray = FakeTray()
    window = ControlWindow(
        running,  # type: ignore[arg-type]
        root=root,  # type: ignore[arg-type]
        close_prompt=lambda _: "tray",
        tray=tray,  # type: ignore[arg-type]
    )

    window.request_close()

    assert root.withdraw_calls == 1
    assert tray.start_calls == 1
    assert running.stop_calls == 0
    assert tray.actions is not None
    scheduled_callbacks = len(root.after_callbacks)
    tray.actions.reopen()  # type: ignore[union-attr]
    assert len(root.after_callbacks) == scheduled_callbacks
    assert tray.stop_calls == 0
    assert root.deiconify_calls == 0
    root.run_after_callback()
    assert tray.stop_calls == 1
    assert root.deiconify_calls == 1
    assert root.lift_calls == 1


def test_tray_start_failure_restores_control_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_tk(monkeypatch)
    root = FakeRoot()
    running = FakeRunningServer()
    running.active_tasks = True
    tray = FakeTray()
    tray.fail_start = True
    window = ControlWindow(
        running,  # type: ignore[arg-type]
        root=root,  # type: ignore[arg-type]
        close_prompt=lambda _: "tray",
        tray=tray,  # type: ignore[arg-type]
    )

    window.request_close()

    assert root.withdraw_calls == 1
    assert root.deiconify_calls == 1
    assert root.lift_calls == 1
    assert running.stop_calls == 0
    assert root.destroy_calls == 0


def test_tray_menu_opens_task_center_and_pauses_all_active_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_tk(monkeypatch)
    root = FakeRoot()
    running = FakeRunningServer()
    running.active_tasks = True
    tray = FakeTray()
    opened_urls: list[str] = []
    window = ControlWindow(
        running,  # type: ignore[arg-type]
        browser_open=opened_urls.append,
        root=root,  # type: ignore[arg-type]
        close_prompt=lambda _: "tray",
        tray=tray,  # type: ignore[arg-type]
    )
    window.request_close()
    assert tray.actions is not None
    scheduled_callbacks = len(root.after_callbacks)

    tray.actions.open_tasks()  # type: ignore[union-attr]
    tray.actions.pause_all()  # type: ignore[union-attr]
    assert len(root.after_callbacks) == scheduled_callbacks
    assert opened_urls == []
    assert running.pause_all_calls == 0
    root.run_after_callback()

    query = parse_qs(urlsplit(opened_urls[0]).query)
    assert query["workspace"] == ["tasks"]
    assert running.pause_all_calls == 1
    assert running.stop_calls == 0


def test_explicit_stop_interrupts_work_and_releases_tray_and_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_tk(monkeypatch)
    root = FakeRoot()
    running = FakeRunningServer()
    running.active_tasks = True
    tray = FakeTray()
    window = ControlWindow(
        running,  # type: ignore[arg-type]
        root=root,  # type: ignore[arg-type]
        close_prompt=lambda _: "tray",
        tray=tray,  # type: ignore[arg-type]
    )
    window.request_close()
    assert tray.actions is not None
    scheduled_callbacks = len(root.after_callbacks)

    tray.actions.stop()  # type: ignore[union-attr]
    assert len(root.after_callbacks) == scheduled_callbacks
    assert running.stop_calls == 0
    root.run_after_callback()

    assert running.interrupt_all_calls == 1
    assert running.stop_calls == 1
    assert tray.stop_calls == 1
    assert root.destroy_calls == 1


def test_windows_tray_wires_required_menu_and_stops_idempotently(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    items: list[SimpleNamespace] = []
    icons: list[SimpleNamespace] = []

    def menu_item(text: str, action: object, **kwargs: object) -> SimpleNamespace:
        item = SimpleNamespace(text=text, action=action, kwargs=kwargs)
        items.append(item)
        return item

    def icon(*args: object) -> SimpleNamespace:
        created = SimpleNamespace(
            args=args,
            run_detached_calls=0,
            stop_calls=0,
            visible=True,
        )
        created.run_detached = lambda: setattr(
            created, "run_detached_calls", created.run_detached_calls + 1
        )
        created.stop = lambda: setattr(created, "stop_calls", created.stop_calls + 1)
        icons.append(created)
        return created

    fake_pystray = SimpleNamespace(
        Icon=icon,
        Menu=lambda *entries: entries,
        MenuItem=menu_item,
    )
    monkeypatch.setitem(sys.modules, "pystray", fake_pystray)
    image = object()

    class FakeImageSource:
        def __enter__(self) -> FakeImageSource:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def copy(self) -> object:
            return image

    monkeypatch.setattr("PIL.Image.open", lambda _: FakeImageSource())
    events: list[str] = []
    tray = WindowsTray(tmp_path / "app.ico")
    actions = TrayActions(
        reopen=lambda: events.append("reopen"),
        open_tasks=lambda: events.append("tasks"),
        pause_all=lambda: events.append("pause"),
        stop=lambda: events.append("stop"),
    )

    tray.start(actions)
    tray.start(actions)

    assert len(icons) == 1
    assert icons[0].run_detached_calls == 1
    assert [item.text for item in items] == [
        "重新打开",
        "打开任务中心",
        "暂停全部",
        "停止应用",
    ]
    assert items[0].kwargs == {"default": True}
    for item in items:
        item.action(None, item)
    assert events == ["reopen", "tasks", "pause", "stop"]

    tray.stop()
    tray.stop()
    assert icons[0].stop_calls == 1


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
