from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from douyin_downloader.resources import app_icon_path


@dataclass(frozen=True, slots=True)
class TrayActions:
    reopen: Callable[[], None]
    open_tasks: Callable[[], None]
    pause_all: Callable[[], None]
    stop: Callable[[], None]


class TrayController(Protocol):
    def start(self, actions: TrayActions) -> None: ...

    def stop(self) -> None: ...


class _TrayIcon(Protocol):
    visible: bool

    def stop(self) -> None: ...


class WindowsTray:
    def __init__(self, icon_path: Path | None = None) -> None:
        self._icon_path = icon_path or app_icon_path()
        self._icon: _TrayIcon | None = None

    def start(self, actions: TrayActions) -> None:
        if self._icon is not None:
            return
        from PIL import Image
        from pystray import Icon, Menu, MenuItem  # type: ignore[import-untyped]

        with Image.open(self._icon_path) as source:
            image = source.copy()
        menu = Menu(
            MenuItem("重新打开", lambda *_: actions.reopen(), default=True),
            MenuItem("打开任务中心", lambda *_: actions.open_tasks()),
            MenuItem("暂停全部", lambda *_: actions.pause_all()),
            MenuItem("停止应用", lambda *_: actions.stop()),
        )
        icon = Icon("douyin-local-downloader", image, "抖音视频下载", menu)
        icon.run_detached()
        self._icon = icon

    def is_visible(self) -> bool:
        icon = self._icon
        return icon is not None and bool(icon.visible)

    def stop(self) -> None:
        icon = self._icon
        if icon is None:
            return
        self._icon = None
        icon.visible = False
        icon.stop()
