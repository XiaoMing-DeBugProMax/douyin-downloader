from __future__ import annotations

import logging
import os
import re
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from douyin_downloader.runtime import APP_DIR_NAME

LOGGER_NAME = "douyin_downloader"
LOG_DIR_NAME = "logs"
LOG_FILENAME = "app.log"
MAX_LOG_BYTES = 1_048_576
BACKUP_COUNT = 5
REDACTED = "[REDACTED]"
SENSITIVE_FIELDS = frozenset(
    {"share_text", "cookie", "launch_token", "parse_token", "media_url"}
)
_SAFE_LABEL = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400


def _is_reparse_point(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attributes = getattr(path.stat(follow_symlinks=False), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & _FILE_ATTRIBUTE_REPARSE_POINT)


def _safe_log_path() -> Path:
    raw_local_app_data = os.environ.get("LOCALAPPDATA")
    if not raw_local_app_data:
        raise RuntimeError("LOCALAPPDATA is required")
    local_app_data = Path(raw_local_app_data)
    if not local_app_data.is_absolute():
        raise RuntimeError("LOCALAPPDATA must be an absolute path")
    resolved_local_app_data = local_app_data.resolve(strict=False)
    app_dir = resolved_local_app_data / APP_DIR_NAME
    log_dir = app_dir / LOG_DIR_NAME
    log_path = log_dir / LOG_FILENAME

    if any(_is_reparse_point(path) for path in (app_dir, log_dir, log_path)):
        raise RuntimeError("log path must not contain a reparse point")
    app_dir.mkdir(parents=True, exist_ok=True)
    if _is_reparse_point(app_dir) or app_dir.resolve(strict=False) != app_dir:
        raise RuntimeError("application log directory must not be a reparse point")
    log_dir.mkdir(exist_ok=True)
    if _is_reparse_point(log_dir) or log_dir.resolve(strict=False) != log_dir:
        raise RuntimeError("log directory must not be a reparse point")
    if _is_reparse_point(log_path):
        raise RuntimeError("log file must not be a reparse point")
    if (
        app_dir.parent != resolved_local_app_data
        or app_dir.name != APP_DIR_NAME
        or log_dir.parent != app_dir
        or log_dir.name != LOG_DIR_NAME
        or log_path.parent != log_dir
        or log_path.name != LOG_FILENAME
    ):
        raise RuntimeError("log path is outside the exact application directory")
    return log_path


class SensitiveFieldFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        for field in SENSITIVE_FIELDS:
            if hasattr(record, field):
                setattr(record, field, REDACTED)
        if isinstance(record.args, dict):
            record.args = {
                key: REDACTED if key in SENSITIVE_FIELDS else value
                for key, value in record.args.items()
            }
        return True


def _safe_label(value: object) -> str:
    text = str(value)
    return text if _SAFE_LABEL.fullmatch(text) else "-"


def _safe_count(value: object) -> str:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return str(value)
    return "-"


class SafeOperationFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created, tz=UTC).isoformat(
            timespec="milliseconds"
        )
        fields = [
            f"timestamp={timestamp}",
            f"operation={_safe_label(getattr(record, 'operation', '-'))}",
            f"error_code={_safe_label(getattr(record, 'error_code', '-'))}",
            f"elapsed_ms={_safe_count(getattr(record, 'elapsed_ms', '-'))}",
            f"bytes_streamed={_safe_count(getattr(record, 'bytes_streamed', '-'))}",
        ]
        fields.extend(
            f"{field}={getattr(record, field)}"
            for field in sorted(SENSITIVE_FIELDS)
            if hasattr(record, field)
        )
        return " ".join(fields)


def log_operation(
    logger: logging.Logger,
    *,
    operation: str,
    error_code: str,
    elapsed_ms: int,
    bytes_streamed: int,
) -> None:
    try:
        logger.info(
            "",
            extra={
                "operation": operation,
                "error_code": error_code,
                "elapsed_ms": elapsed_ms,
                "bytes_streamed": bytes_streamed,
            },
        )
    except Exception:
        return


def _silence_dependency_loggers() -> None:
    for name in ("f2", "f2-trace", "uvicorn.access"):
        dependency_logger = logging.getLogger(name)
        for handler in dependency_logger.handlers:
            handler.close()
        dependency_logger.handlers.clear()
        dependency_logger.addHandler(logging.NullHandler())
        dependency_logger.propagate = False
    logging.getLogger("uvicorn.access").disabled = True


def configure_logging() -> logging.Logger:
    log_path = _safe_log_path()
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for existing in logger.handlers:
        existing.close()
    logger.handlers.clear()

    handler = RotatingFileHandler(
        log_path,
        maxBytes=MAX_LOG_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.addFilter(SensitiveFieldFilter())
    handler.setFormatter(SafeOperationFormatter())
    logger.addHandler(handler)
    _silence_dependency_loggers()
    return logger
