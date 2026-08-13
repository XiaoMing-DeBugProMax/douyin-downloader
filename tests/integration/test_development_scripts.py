from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

POWERSHELL = (
    Path(os.environ["SystemRoot"])
    / "System32"
    / "WindowsPowerShell"
    / "v1.0"
    / "powershell.exe"
)
GIT = Path(shutil.which("git") or "")


def _run_powershell(script: Path, *arguments: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [
            str(POWERSHELL),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            *arguments,
        ],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def test_verify_preflight_reports_the_selected_environment() -> None:
    project_root = Path(__file__).parents[2]
    script = project_root / "scripts" / "verify.ps1"

    result = _run_powershell(script, "-Preflight", cwd=project_root)

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

    result = _run_powershell(script, "-Focused", node, cwd=project_root)

    assert result.returncode == 0, result.stdout + result.stderr
    assert f"VERIFY_FOCUSED={node}" in result.stdout
    assert "1 passed" in result.stdout
    assert "VERIFY_RESULT=ok" in result.stdout


def test_verify_focused_propagates_the_pytest_failure_exit_code() -> None:
    project_root = Path(__file__).parents[2]
    script = project_root / "scripts" / "verify.ps1"
    missing_node = "tests/unit/test_app_baseline.py::test_node_does_not_exist"

    result = _run_powershell(script, "-Focused", missing_node, cwd=project_root)

    assert result.returncode == 4, result.stdout + result.stderr
    assert f"VERIFY_FOCUSED={missing_node}" in result.stdout
    assert "VERIFY_RESULT=ok" not in result.stdout


def test_python_failure_classifier_recognizes_localized_access_denial() -> None:
    project_root = Path(__file__).parents[2]
    helper = project_root / "scripts" / "python-environment.ps1"
    command = (
        f". '{helper}'; "
        "$message = [string]([char]0x62D2) + [char]0x7EDD + "
        "[char]0x8BBF + [char]0x95EE; "
        "Get-PythonFailureCode -Message $message "
        "-FallbackCode 'PYTHON_UNAVAILABLE'"
    )

    result = subprocess.run(  # noqa: S603
        [str(POWERSHELL), "-NoProfile", "-Command", command],
        cwd=project_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "PYTHON_EXECUTION_DENIED"


def test_bootstrap_check_reuses_the_read_only_preflight() -> None:
    project_root = Path(__file__).parents[2]
    script = project_root / "scripts" / "bootstrap-dev.ps1"

    result = _run_powershell(script, "-Check", cwd=project_root)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "BOOTSTRAP_MODE=check" in result.stdout
    assert "VERIFY_PREFLIGHT=ok" in result.stdout
    assert "BOOTSTRAP_RESULT=ready" in result.stdout


def test_verify_uses_the_common_repository_venv_from_a_linked_worktree(
    tmp_path: Path,
) -> None:
    project_root = Path(__file__).parents[2]
    worktree = tmp_path / "linked-worktree"
    add = subprocess.run(  # noqa: S603
        [str(GIT), "worktree", "add", "--detach", str(worktree), "HEAD"],
        cwd=project_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert add.returncode == 0, add.stdout + add.stderr
    try:
        shutil.copy2(
            project_root / "scripts" / "verify.ps1",
            worktree / "scripts" / "verify.ps1",
        )
        shutil.copy2(
            project_root / "scripts" / "python-environment.ps1",
            worktree / "scripts" / "python-environment.ps1",
        )
        result = _run_powershell(
            worktree / "scripts" / "verify.ps1",
            "-Preflight",
            cwd=worktree,
        )

        assert result.returncode == 0, result.stdout + result.stderr
        assert f"VERIFY_PROJECT_ROOT={worktree}" in result.stdout
        assert (
            f"VERIFY_PYTHON={project_root / '.venv' / 'Scripts' / 'python.exe'}"
            in result.stdout
        )
        assert f"VERIFY_SOURCE_ROOT={worktree / 'src'}" in result.stdout
        expected_module = worktree / "src" / "douyin_downloader" / "__init__.py"
        assert f"VERIFY_MODULE={expected_module}" in result.stdout
    finally:
        subprocess.run(  # noqa: S603
            [str(GIT), "worktree", "remove", "--force", str(worktree)],
            cwd=project_root,
            capture_output=True,
            check=False,
        )


def test_verify_classifies_a_broken_launcher_as_an_incomplete_venv(tmp_path: Path) -> None:
    project_root = Path(__file__).parents[2]
    repository = tmp_path / "repository"
    scripts = repository / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(project_root / "scripts" / "verify.ps1", scripts / "verify.ps1")
    shutil.copy2(
        project_root / "scripts" / "python-environment.ps1",
        scripts / "python-environment.ps1",
    )
    (repository / "src").mkdir()
    subprocess.run(  # noqa: S603
        [str(GIT), "init", "--quiet"],
        cwd=repository,
        check=True,
    )
    venv_root = repository / ".venv"
    venv_scripts = venv_root / "Scripts"
    venv_scripts.mkdir(parents=True)
    shutil.copy2(Path(sys.executable), venv_scripts / "python.exe")
    (venv_root / "pyvenv.cfg").write_text(
        "home = Z:\\missing-python-home\nversion = 3.12.10\n",
        encoding="utf-8",
    )

    result = _run_powershell(scripts / "verify.ps1", "-Preflight", cwd=repository)

    assert result.returncode != 0
    assert "VERIFY_ERROR=VENV_INCOMPLETE" in result.stderr


def test_bootstrap_repair_rebuilds_an_existing_broken_venv(tmp_path: Path) -> None:
    project_root = Path(__file__).parents[2]
    repository = tmp_path / "repository"
    scripts = repository / "scripts"
    source_root = repository / "src"
    package = source_root / "douyin_downloader"
    tests = repository / "tests"
    scripts.mkdir(parents=True)
    package.mkdir(parents=True)
    for dependency in ("pytest", "playwright", "PyInstaller", "ruff", "mypy"):
        dependency_package = source_root / dependency
        dependency_package.mkdir()
        (dependency_package / "__init__.py").write_text("", encoding="utf-8")
    tests.mkdir()
    shutil.copy2(project_root / "scripts" / "verify.ps1", scripts / "verify.ps1")
    shutil.copy2(
        project_root / "scripts" / "python-environment.ps1",
        scripts / "python-environment.ps1",
    )
    shutil.copy2(
        project_root / "scripts" / "bootstrap-dev.ps1",
        scripts / "bootstrap-dev.ps1",
    )
    (package / "__init__.py").write_text("", encoding="utf-8")
    (tests / "test_fixture.py").write_text("def test_fixture(): assert True\n", encoding="utf-8")
    (repository / "pyproject.toml").write_text(
        """
[build-system]
requires = ["setuptools"]
build-backend = "setuptools.build_meta"

[project]
name = "douyin-local-downloader-fixture"
version = "0.0.0"
requires-python = ">=3.12,<3.13"
dependencies = []

[project.optional-dependencies]
dev = []

[tool.setuptools.packages.find]
where = ["src"]
""".strip()
        + "\n",
        encoding="utf-8",
    )
    subprocess.run(  # noqa: S603
        [str(GIT), "init", "--quiet"],
        cwd=repository,
        check=True,
    )
    venv_root = repository / ".venv"
    venv_scripts = venv_root / "Scripts"
    venv_scripts.mkdir(parents=True)
    shutil.copy2(Path(sys.executable), venv_scripts / "python.exe")
    (venv_root / "pyvenv.cfg").write_text(
        "home = Z:\\missing-python-home\nversion = 3.12.10\n",
        encoding="utf-8",
    )

    result = _run_powershell(
        scripts / "bootstrap-dev.ps1",
        "-Repair",
        "-Python",
        sys._base_executable,
        cwd=repository,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "BOOTSTRAP_MODE=install" in result.stdout
    assert "BOOTSTRAP_RESULT=ready" in result.stdout
    probe = subprocess.run(  # noqa: S603
        [str(venv_scripts / "python.exe"), "--version"],
        cwd=repository,
        capture_output=True,
        text=True,
        check=False,
    )
    assert probe.returncode == 0, probe.stdout + probe.stderr
    assert probe.stdout.startswith("Python 3.12.")


def test_bootstrap_refuses_to_clear_a_broken_venv_without_repair(tmp_path: Path) -> None:
    project_root = Path(__file__).parents[2]
    repository = tmp_path / "repository"
    scripts = repository / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(
        project_root / "scripts" / "python-environment.ps1",
        scripts / "python-environment.ps1",
    )
    shutil.copy2(
        project_root / "scripts" / "bootstrap-dev.ps1",
        scripts / "bootstrap-dev.ps1",
    )
    subprocess.run(  # noqa: S603
        [str(GIT), "init", "--quiet"],
        cwd=repository,
        check=True,
    )
    venv_root = repository / ".venv"
    venv_scripts = venv_root / "Scripts"
    venv_scripts.mkdir(parents=True)
    launcher = venv_scripts / "python.exe"
    shutil.copy2(Path(sys.executable), launcher)
    (venv_root / "pyvenv.cfg").write_text(
        "home = Z:\\missing-python-home\nversion = 3.12.10\n",
        encoding="utf-8",
    )
    marker = venv_root / "must-survive.txt"
    marker.write_text("preserve", encoding="utf-8")

    result = _run_powershell(
        scripts / "bootstrap-dev.ps1",
        "-Python",
        sys._base_executable,
        cwd=repository,
    )

    assert result.returncode != 0
    assert "BOOTSTRAP_ERROR=VENV_REPAIR_REQUIRED" in result.stderr
    assert marker.read_text(encoding="utf-8") == "preserve"
