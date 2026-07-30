from __future__ import annotations

import struct
from collections.abc import Iterator
from pathlib import Path
from typing import BinaryIO

from douyin_downloader.domain import AppError


def archive_failed() -> AppError:
    return AppError("ARCHIVE_FAILED", "本地归档失败，请稍后重试。", 502)


def inspect_mp4(path: Path) -> int:
    file_size = path.stat().st_size
    with path.open("rb") as stream:
        top_level = list(_boxes(stream, 0, file_size))
        kinds = {kind for kind, _, _ in top_level}
        if not {b"ftyp", b"moov", b"mdat"}.issubset(kinds):
            raise archive_failed()
        movie = next(box for box in top_level if box[0] == b"moov")
        movie_children = list(_boxes(stream, movie[1], movie[2]))
        header = next((box for box in movie_children if box[0] == b"mvhd"), None)
        if header is None:
            raise archive_failed()
        duration_ms = _movie_duration_ms(stream, header[1], header[2])
        if not any(
            _track_is_video(stream, box)
            for box in movie_children
            if box[0] == b"trak"
        ):
            raise archive_failed()
        return duration_ms


def _boxes(
    stream: BinaryIO,
    start: int,
    end: int,
) -> Iterator[tuple[bytes, int, int]]:
    position = start
    while position < end:
        if end - position < 8:
            raise archive_failed()
        stream.seek(position)
        header = stream.read(8)
        size, kind = struct.unpack(">I4s", header)
        header_size = 8
        if size == 1:
            extended = stream.read(8)
            if len(extended) != 8:
                raise archive_failed()
            size = struct.unpack(">Q", extended)[0]
            header_size = 16
        elif size == 0:
            size = end - position
        if size < header_size or position + size > end:
            raise archive_failed()
        payload_start = position + header_size
        box_end = position + size
        yield kind, payload_start, box_end
        position = box_end
    if position != end:
        raise archive_failed()


def _movie_duration_ms(stream: BinaryIO, start: int, end: int) -> int:
    stream.seek(start)
    payload = stream.read(min(end - start, 32))
    if len(payload) < 20:
        raise archive_failed()
    version = payload[0]
    if version == 0:
        timescale, duration = struct.unpack(">II", payload[12:20])
    elif version == 1 and len(payload) >= 32:
        timescale = struct.unpack(">I", payload[20:24])[0]
        duration = struct.unpack(">Q", payload[24:32])[0]
    else:
        raise archive_failed()
    if timescale <= 0 or duration <= 0:
        raise archive_failed()
    return int(duration * 1_000 / timescale)


def _track_is_video(stream: BinaryIO, track: tuple[bytes, int, int]) -> bool:
    for kind, start, end in _boxes(stream, track[1], track[2]):
        if kind != b"mdia":
            continue
        for child_kind, child_start, child_end in _boxes(stream, start, end):
            if child_kind != b"hdlr" or child_end - child_start < 12:
                continue
            stream.seek(child_start + 8)
            return stream.read(4) == b"vide"
    return False
