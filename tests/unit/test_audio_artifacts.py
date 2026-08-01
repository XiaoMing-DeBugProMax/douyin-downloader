from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from subprocess import CompletedProcess, TimeoutExpired

import pytest

from douyin_downloader.audio_artifacts import (
    AudioArtifactError,
    FfmpegAudioArtifactTool,
)


class RecordingRunner:
    def __init__(
        self,
        results: Iterable[CompletedProcess[str]],
        *,
        create_output_on_call: int | None = None,
    ) -> None:
        self._results = iter(results)
        self._create_output_on_call = create_output_on_call
        self.argv: list[tuple[str, ...]] = []

    def __call__(
        self,
        argv: tuple[str, ...],
        timeout_seconds: float,
    ) -> CompletedProcess[str]:
        del timeout_seconds
        self.argv.append(argv)
        if self._create_output_on_call == len(self.argv):
            Path(argv[-1]).write_bytes(b"validated-m4a")
        return next(self._results)


def _completed_json(payload: object) -> CompletedProcess[str]:
    return CompletedProcess((), 0, json.dumps(payload), "")


def _audio_probe(codec: str = "aac", duration: float = 15.0) -> object:
    return {
        "streams": [
            {
                "codec_name": codec,
                "codec_type": "audio",
                "duration": str(duration),
            }
        ],
        "format": {"duration": str(duration)},
    }


def test_no_audio_is_a_structured_successful_outcome(tmp_path: Path) -> None:
    source = (tmp_path / "video.mp4").resolve()
    source.write_bytes(b"video")
    output = (tmp_path / "video.audio.m4a.part").resolve()
    runner = RecordingRunner([_completed_json({"streams": [], "format": {}})])
    tool = FfmpegAudioArtifactTool(
        Path("C:/app/ffmpeg.exe"),
        Path("C:/app/ffprobe.exe"),
        runner=runner,
    )

    outcome = tool.extract(source, output)

    assert outcome == "no_audio"
    assert len(runner.argv) == 1
    assert runner.argv[0][0] == "C:\\app\\ffprobe.exe"
    assert not output.exists()


def test_audio_is_stream_copied_with_fixed_arguments_and_reprobed(
    tmp_path: Path,
) -> None:
    source = (tmp_path / "video.mp4").resolve()
    source.write_bytes(b"video")
    output = (tmp_path / "video.audio.m4a.part").resolve()
    runner = RecordingRunner(
        [
            _completed_json(_audio_probe()),
            CompletedProcess((), 0, "", ""),
            _completed_json(_audio_probe()),
        ],
        create_output_on_call=2,
    )
    tool = FfmpegAudioArtifactTool(
        Path("C:/app/ffmpeg.exe"),
        Path("C:/app/ffprobe.exe"),
        runner=runner,
    )

    outcome = tool.extract(source, output)

    assert outcome == "ready"
    assert len(runner.argv) == 3
    extraction = runner.argv[1]
    assert extraction[0] == "C:\\app\\ffmpeg.exe"
    assert extraction[extraction.index("-map") + 1] == "0:a:0"
    assert extraction[extraction.index("-c:a") + 1] == "copy"
    assert extraction[extraction.index("-f") + 1] == "ipod"
    assert extraction[extraction.index("-i") + 1] == str(source)
    assert extraction[-1] == str(output)
    assert output.read_bytes() == b"validated-m4a"


def test_probe_timeout_is_reported_without_exposing_process_details(
    tmp_path: Path,
) -> None:
    source = (tmp_path / "video.mp4").resolve()
    source.write_bytes(b"video")

    def timed_out(
        argv: tuple[str, ...],
        timeout_seconds: float,
    ) -> CompletedProcess[str]:
        raise TimeoutExpired(argv, timeout_seconds, stderr="private path")

    tool = FfmpegAudioArtifactTool(
        Path("C:/app/ffmpeg.exe"),
        Path("C:/app/ffprobe.exe"),
        runner=timed_out,
    )

    with pytest.raises(AudioArtifactError) as raised:
        tool.extract(source, (tmp_path / "video.audio.m4a.part").resolve())

    assert raised.value.reason == "probe_failed"
    assert str(raised.value) == "probe_failed"


def test_output_must_be_an_application_generated_sibling_path(tmp_path: Path) -> None:
    source = (tmp_path / "video.mp4").resolve()
    source.write_bytes(b"video")
    outside = tmp_path / "outside"
    outside.mkdir()

    def unexpected_runner(
        argv: tuple[str, ...],
        timeout_seconds: float,
    ) -> CompletedProcess[str]:
        raise AssertionError((argv, timeout_seconds))

    tool = FfmpegAudioArtifactTool(
        Path("C:/app/ffmpeg.exe"),
        Path("C:/app/ffprobe.exe"),
        runner=unexpected_runner,
    )

    with pytest.raises(AudioArtifactError) as raised:
        tool.extract(source, (outside / "video.audio.m4a.part").resolve())

    assert raised.value.reason == "validation_failed"
