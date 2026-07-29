from pathlib import Path

import pytest
from PyInstaller.archive.writers import CArchiveWriter, ZlibArchiveWriter

from scripts.check_sensitive import scan_artifact, scan_artifact_entry, scan_text


def _write_pyz_artifact(tmp_path: Path, source: str) -> Path:
    pyz_path = tmp_path / "modules.pyz"
    module_name = "embedded_module"
    ZlibArchiveWriter(
        str(pyz_path),
        [(module_name, "embedded_module.py", "PYMODULE")],
        {module_name: compile(source, "embedded_module.py", "exec")},
    )
    artifact_path = tmp_path / "artifact.exe"
    CArchiveWriter(
        str(artifact_path),
        [("PYZ.pyz", str(pyz_path), False, "z")],
        "python312.dll",
    )
    return artifact_path


def test_sensitive_gate_detects_concrete_values_without_echoing_them() -> None:
    long_value = "ABCDEFGHIJKLMNOPQRSTUVWXYZ" + "123456"
    findings = scan_text(
        "src/example.py",
        "\n".join(
            (
                f'cookie = "ttwid={long_value}"',
                f'cookie = "s_v_web_id={long_value}"',
                f'"launch_token": "{long_value}"',
                "media = 'https://v95-web.douyinvod.com/video.mp4" + "?secret=value'",
            )
        ),
    )

    assert [finding.rule for finding in findings] == [
        "concrete_ttwid",
        "concrete_verify_fp",
        "concrete_launch_token",
        "queried_media_url",
    ]
    rendered = "\n".join(finding.safe_summary() for finding in findings)
    assert "ABCDEFGHIJKLMNOPQRSTUVWXYZ" not in rendered
    assert "secret=value" not in rendered


def test_sensitive_gate_allows_keys_templates_and_short_test_fixtures() -> None:
    findings = scan_text(
        "src/example.py",
        "\n".join(
            (
                'cookie = f"ttwid={ttwid}; s_v_web_id={verify_fp};"',
                'cookie = "ttwid=guest; s_v_web_id=verify;"',
                "launch_token = sessions.issue_launch_token()",
                'SENSITIVE_FIELDS = {"launch_token", "media_url"}',
                "pattern = r'ttwid=[A-Za-z0-9_-]{20,}'",
            )
        ),
    )

    assert findings == []


def test_sensitive_gate_rejects_real_short_share_url_only_in_committed_report() -> None:
    assert scan_text(
        "docs/test-reports/uat.md",
        "sample=https://v.douyin.com/AbCdEf123/",
    )
    assert not scan_text(
        "tests/unit/test_example.py",
        "sample=https://v.douyin.com/example/",
    )


def test_artifact_gate_rejects_dependency_test_data_and_secret_content() -> None:
    long_value = "ABCDEFGHIJKLMNOPQRSTUVWXYZ" + "123456"

    name_findings = scan_artifact_entry("f2/conf/test.yaml", b"safe: true")
    content_findings = scan_artifact_entry(
        "f2/conf/settings.yaml",
        f'cookie: "ttwid={long_value}"'.encode(),
    )

    assert [finding.rule for finding in name_findings] == [
        "artifact_forbidden_test_data"
    ]
    assert [finding.rule for finding in content_findings] == ["concrete_ttwid"]
    assert long_value not in content_findings[0].safe_summary()


def test_artifact_gate_allows_required_code_and_sanitized_assets() -> None:
    assert scan_artifact_entry("f2/utils/abogus.pyc", b"\0compiled-code") == []
    assert scan_artifact_entry(
        "douyin_downloader/web/static/app.js",
        b"const launch_token = sessions.issue_launch_token();",
    ) == []


def test_artifact_gate_detects_sensitive_constants_in_embedded_pyz(tmp_path: Path) -> None:
    long_value = "ABCDEFGHIJKLMNOPQRSTUVWXYZ123456"
    artifact = _write_pyz_artifact(
        tmp_path,
        "def nested():\n"
        f"    cookie = b'ttwid={long_value}'\n"
        f"    verify = 's_v_web_id={long_value}'\n",
    )

    findings = scan_artifact(artifact)

    assert [finding.rule for finding in findings] == [
        "concrete_ttwid",
        "concrete_verify_fp",
    ]
    assert all(finding.path.startswith("artifact/PYZ.pyz/") for finding in findings)
    rendered = "\n".join(finding.safe_summary() for finding in findings)
    assert long_value not in rendered


def test_artifact_gate_allows_sanitized_constants_in_embedded_pyz(tmp_path: Path) -> None:
    artifact = _write_pyz_artifact(
        tmp_path,
        "def nested():\n"
        "    cookie = b'ttwid=guest'\n"
        "    token = 'launch_token: generated-at-runtime'\n",
    )

    assert scan_artifact(artifact) == []


def test_artifact_gate_scans_current_executable_when_available() -> None:
    artifact = Path(__file__).resolve().parents[2] / "dist" / "抖音视频下载.exe"
    if not artifact.is_file():
        pytest.skip("current packaged executable is not available")

    assert scan_artifact(artifact) == []
