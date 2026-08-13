from __future__ import annotations

import os
import time

import pytest

from douyin_downloader.system_tray import TrayActions, WindowsTray


@pytest.mark.skipif(os.name != "nt", reason="Windows system tray integration")
def test_windows_tray_icon_is_displayed_and_removed() -> None:
    tray = WindowsTray()
    actions = TrayActions(
        reopen=lambda: None,
        open_tasks=lambda: None,
        pause_all=lambda: None,
        stop=lambda: None,
    )

    tray.start(actions)
    try:
        deadline = time.monotonic() + 5
        while not tray.is_visible() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert tray.is_visible()
    finally:
        tray.stop()

    assert not tray.is_visible()
