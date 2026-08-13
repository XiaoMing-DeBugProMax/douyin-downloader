from __future__ import annotations

import os
import subprocess
from pathlib import Path

POWERSHELL = (
    Path(os.environ["SystemRoot"])
    / "System32"
    / "WindowsPowerShell"
    / "v1.0"
    / "powershell.exe"
)


def test_verify_preflight_reports_the_selected_environment() -> None:
    project_root = Path(__file__).parents[2]
    script = project_root / "scripts" / "verify.ps1"

    result = subprocess.run(  # noqa: S603
        [
            str(POWERSHELL),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-Preflight",
        ],
        cwd=project_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert f"VERIFY_PROJECT_ROOT={project_root}" in result.stdout
    assert f"VERIFY_PYTHON={project_root / '.venv' / 'Scripts' / 'python.exe'}" in result.stdout
    assert "VERIFY_PYTHON_VERSION=3.12." in result.stdout
    assert f"VERIFY_SOURCE_ROOT={project_root / 'src'}" in result.stdout
    assert "VERIFY_DEPENDENCIES=ok" in result.stdout
    assert "VERIFY_PREFLIGHT=ok" in result.stdout


def test_verify_focused_runs_only_the_requested_pytest_node() -> None:
    project_root = Path(__file__).parents[2]
    script = project_root / "scripts" / "verify.ps1"
    node = "tests/unit/test_app_baseline.py::test_launcher_entry_point_is_callable"

    result = subprocess.run(  # noqa: S603
        [
            str(POWERSHELL),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-Focused",
            node,
        ],
        cwd=project_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert f"VERIFY_FOCUSED={node}" in result.stdout
    assert "1 passed" in result.stdout
    assert "VERIFY_RESULT=ok" in result.stdout


def test_bootstrap_check_reuses_the_read_only_preflight() -> None:
    project_root = Path(__file__).parents[2]
    script = project_root / "scripts" / "bootstrap-dev.ps1"

    result = subprocess.run(  # noqa: S603
        [
            str(POWERSHELL),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-Check",
        ],
        cwd=project_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "BOOTSTRAP_MODE=check" in result.stdout
    assert "VERIFY_PREFLIGHT=ok" in result.stdout
    assert "BOOTSTRAP_RESULT=ready" in result.stdout
