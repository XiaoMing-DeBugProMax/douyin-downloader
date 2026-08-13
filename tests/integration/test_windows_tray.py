from __future__ import annotations

import os
import time

import pytest

from douyin_downloader.launcher import TrayActions, WindowsTray


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
    icon = tray._icon
    try:
        assert icon is not None
        deadline = time.monotonic() + 5
        while not bool(icon.visible) and time.monotonic() < deadline:  # type: ignore[attr-defined]
            time.sleep(0.05)
        assert icon.visible is True  # type: ignore[attr-defined]
    finally:
        tray.stop()

    assert tray._icon is None
    assert icon.visible is False  # type: ignore[attr-defined]
