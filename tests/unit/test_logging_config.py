from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest

import douyin_downloader.logging_config as logging_config
from douyin_downloader.logging_config import configure_logging, log_operation

SENSITIVE_FIELDS = (
    "share_text",
    "cookie",
    "launch_token",
    "parse_token",
    "media_url",
)


def _flush(logger: logging.Logger) -> None:
    for handler in logger.handlers:
        handler.flush()


def test_configure_logging_redacts_exact_sensitive_fields_and_omits_unsafe_data(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    local_app_data = tmp_path / "LocalAppData"
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))

    logger = configure_logging()
    secrets = {field: f"secret-{field}" for field in SENSITIVE_FIELDS}
    logger.info(
        "message text must not be persisted",
        extra={
            "operation": "parse",
            "error_code": "UPSTREAM_TIMEOUT",
            "elapsed_ms": 123,
            "bytes_streamed": 456,
            "unsafe_extra": "must-not-be-persisted",
            **secrets,
        },
    )
    _flush(logger)

    log_path = local_app_data / "DouyinLocalDownloader" / "logs" / "app.log"
    content = log_path.read_text(encoding="utf-8")
    for field, secret in secrets.items():
        assert f"{field}=[REDACTED]" in content
        assert secret not in content
    assert "operation=parse" in content
    assert "error_code=UPSTREAM_TIMEOUT" in content
    assert "elapsed_ms=123" in content
    assert "bytes_streamed=456" in content
    assert "message text must not be persisted" not in content
    assert "must-not-be-persisted" not in content


def test_configure_logging_uses_exact_local_app_data_path_and_rotation_policy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    local_app_data = tmp_path / "LocalAppData"
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))

    logger = configure_logging()

    assert len(logger.handlers) == 1
    handler = logger.handlers[0]
    assert isinstance(handler, RotatingFileHandler)
    assert Path(handler.baseFilename) == (
        local_app_data / "DouyinLocalDownloader" / "logs" / "app.log"
    )
    assert handler.maxBytes == 1_048_576
    assert handler.backupCount == 5
    assert handler.encoding == "utf-8"


def test_configure_logging_refuses_reparse_points(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    local_app_data = tmp_path / "LocalAppData"
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    monkeypatch.setattr(
        logging_config,
        "_is_reparse_point",
        lambda path: path.name == "DouyinLocalDownloader",
    )

    with pytest.raises(RuntimeError, match="reparse"):
        configure_logging()


def test_log_operation_never_breaks_user_operation_when_logger_fails() -> None:
    class BrokenLogger:
        def info(self, *_: object, **__: object) -> None:
            raise OSError("simulated logging failure")

    log_operation(
        BrokenLogger(),  # type: ignore[arg-type]
        operation="parse",
        error_code="UPSTREAM_BLOCKED",
        elapsed_ms=12,
        bytes_streamed=0,
    )
