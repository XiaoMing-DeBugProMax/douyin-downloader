from __future__ import annotations

import json
import math
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from subprocess import CompletedProcess
from typing import Literal, Protocol

AudioExtractionOutcome = Literal["ready", "no_audio"]
AudioFailureReason = Literal["probe_failed", "extract_failed", "validation_failed"]
CommandRunner = Callable[[tuple[str, ...], float], CompletedProcess[str]]

_PROBE_TIMEOUT_SECONDS = 30.0
_EXTRACT_TIMEOUT_SECONDS = 120.0
_PROBE_ARGS = (
    "-v",
    "error",
    "-select_streams",
    "a:0",
    "-show_entries",
    "stream=codec_name,codec_type,duration:format=duration",
    "-of",
    "json",
)


class AudioArtifactError(Exception):
    def __init__(self, reason: AudioFailureReason) -> None:
        super().__init__(reason)
        self.reason = reason


class AudioArtifactTool(Protocol):
    def extract(
        self,
        source_video: Path,
        output_part: Path,
    ) -> AudioExtractionOutcome: ...

    def validate(
        self,
        source_video: Path,
        audio_artifact: Path | None,
        expected: AudioExtractionOutcome,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class _AudioProbe:
    codec: str
    duration_seconds: float | None


class FfmpegAudioArtifactTool:
    def __init__(
        self,
        ffmpeg_path: Path,
        ffprobe_path: Path,
        *,
        runner: CommandRunner | None = None,
    ) -> None:
        self._ffmpeg_path = ffmpeg_path
        self._ffprobe_path = ffprobe_path
        self._runner = runner or _run_command

    def extract(
        self,
        source_video: Path,
        output_part: Path,
    ) -> AudioExtractionOutcome:
        _require_sibling_paths(source_video, output_part)
        source = self._probe(
            source_video,
            failure_reason="probe_failed",
        )
        if source is None:
            return "no_audio"
        output_part.unlink(missing_ok=True)
        completed = self._execute(
            (
                str(self._ffmpeg_path),
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-n",
                "-i",
                str(source_video),
                "-map",
                "0:a:0",
                "-vn",
                "-sn",
                "-dn",
                "-c:a",
                "copy",
                "-map_metadata",
                "-1",
                "-f",
                "ipod",
                str(output_part),
            ),
            _EXTRACT_TIMEOUT_SECONDS,
            "extract_failed",
        )
        if completed.returncode != 0:
            output_part.unlink(missing_ok=True)
            raise AudioArtifactError("extract_failed")
        try:
            self._validate_ready(source, output_part)
        except AudioArtifactError:
            output_part.unlink(missing_ok=True)
            raise
        return "ready"

    def validate(
        self,
        source_video: Path,
        audio_artifact: Path | None,
        expected: AudioExtractionOutcome,
    ) -> None:
        source = self._probe(source_video, failure_reason="validation_failed")
        if expected == "no_audio":
            if source is not None or audio_artifact is not None:
                raise AudioArtifactError("validation_failed")
            return
        if source is None or audio_artifact is None:
            raise AudioArtifactError("validation_failed")
        _require_sibling_paths(source_video, audio_artifact)
        self._validate_ready(source, audio_artifact)

    def _validate_ready(self, source: _AudioProbe, output: Path) -> None:
        if not output.is_file() or output.stat().st_size <= 0:
            raise AudioArtifactError("validation_failed")
        extracted = self._probe(output, failure_reason="validation_failed")
        if extracted is None or extracted.codec != source.codec:
            raise AudioArtifactError("validation_failed")
        if extracted.duration_seconds is None:
            raise AudioArtifactError("validation_failed")
        if source.duration_seconds is None:
            return
        tolerance = max(1.0, source.duration_seconds * 0.02)
        if abs(extracted.duration_seconds - source.duration_seconds) > tolerance:
            raise AudioArtifactError("validation_failed")

    def _probe(
        self,
        path: Path,
        *,
        failure_reason: AudioFailureReason,
    ) -> _AudioProbe | None:
        completed = self._execute(
            (str(self._ffprobe_path), *_PROBE_ARGS, str(path)),
            _PROBE_TIMEOUT_SECONDS,
            failure_reason,
        )
        try:
            payload = json.loads(completed.stdout)
        except (TypeError, json.JSONDecodeError) as error:
            raise AudioArtifactError(failure_reason) from error
        if completed.returncode != 0 or not isinstance(payload, dict):
            raise AudioArtifactError(failure_reason)
        streams = payload.get("streams")
        if not isinstance(streams, list):
            raise AudioArtifactError(failure_reason)
        if not streams:
            return None
        stream = streams[0]
        if (
            not isinstance(stream, dict)
            or stream.get("codec_type") != "audio"
            or not isinstance(stream.get("codec_name"), str)
            or not stream["codec_name"]
        ):
            raise AudioArtifactError(failure_reason)
        duration = _duration(stream.get("duration"))
        if duration is None:
            format_data = payload.get("format")
            if isinstance(format_data, dict):
                duration = _duration(format_data.get("duration"))
        return _AudioProbe(str(stream["codec_name"]), duration)

    def _execute(
        self,
        argv: tuple[str, ...],
        timeout_seconds: float,
        failure_reason: AudioFailureReason,
    ) -> CompletedProcess[str]:
        try:
            return self._runner(argv, timeout_seconds)
        except (OSError, subprocess.SubprocessError):
            raise AudioArtifactError(failure_reason) from None


def _run_command(
    argv: tuple[str, ...],
    timeout_seconds: float,
) -> CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - argv is fixed by this module
        argv,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _duration(value: object) -> float | None:
    if not isinstance(value, str | int | float):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and parsed > 0 else None


def _require_sibling_paths(source_video: Path, audio_path: Path) -> None:
    if (
        not source_video.is_absolute()
        or not audio_path.is_absolute()
        or source_video == audio_path
        or source_video.parent != audio_path.parent
    ):
        raise AudioArtifactError("validation_failed")
