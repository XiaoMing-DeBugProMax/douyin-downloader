import os
from pathlib import Path

import pytest

from douyin_downloader.archive_adapters import WindowsRecycleBin


@pytest.mark.skipif(os.name != "nt", reason="Windows Shell API contract")
def test_windows_recycle_bin_requests_undoable_directory_delete(tmp_path: Path) -> None:
    directory = tmp_path / "work"
    directory.mkdir()
    captured: dict[str, object] = {}

    def shell_operation(pointer: object) -> int:
        operation = pointer._obj  # type: ignore[attr-defined]
        captured.update(
            function=operation.wFunc,
            source=operation.pFrom,
            flags=operation.fFlags,
        )
        return 0

    WindowsRecycleBin(shell_operation).move_to_recycle_bin(directory)

    assert captured["function"] == 3
    assert captured["source"].startswith(str(directory))  # type: ignore[union-attr]
    assert captured["flags"] & 0x0040  # FOF_ALLOWUNDO


@pytest.mark.skipif(os.name != "nt", reason="Windows Shell API contract")
@pytest.mark.parametrize(("result", "aborted"), [(5, False), (0, True)])
def test_windows_recycle_bin_reports_failure_or_user_cancellation(
    tmp_path: Path,
    result: int,
    aborted: bool,
) -> None:
    directory = tmp_path / "work"
    directory.mkdir()

    def shell_operation(pointer: object) -> int:
        if aborted:
            pointer._obj.fAnyOperationsAborted = 1  # type: ignore[attr-defined]
        return result

    with pytest.raises(OSError):
        WindowsRecycleBin(shell_operation).move_to_recycle_bin(directory)
