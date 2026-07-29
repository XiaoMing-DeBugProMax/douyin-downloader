from scripts.check_sensitive import scan_text


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
