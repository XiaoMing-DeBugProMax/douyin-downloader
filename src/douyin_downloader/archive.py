from __future__ import annotations

import hashlib
import os
import sqlite3
import struct
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO, Protocol
from uuid import uuid4

import httpx

from douyin_downloader.domain import AppError, ResolvedWork
from douyin_downloader.media import open_first_available


@dataclass(frozen=True, slots=True)
class SingleArchiveRequest:
    aweme_id: str
    archive_root: Path


@dataclass(frozen=True, slots=True)
class RemoteVideo:
    content_type: str
    expected_size: int | None
    chunks: AsyncIterator[bytes]


@dataclass(frozen=True, slots=True)
class TaskSnapshot:
    task_id: str
    lifecycle: str
    phase: str
    result: str


@dataclass(frozen=True, slots=True)
class ArchiveItemSnapshot:
    aweme_id: str
    status: str
    relative_directory: Path


@dataclass(frozen=True, slots=True)
class ArchiveOperationSnapshot:
    operation: TaskSnapshot
    source_task: TaskSnapshot
    work_task: TaskSnapshot
    archive_item: ArchiveItemSnapshot


class WorkAccess(Protocol):
    async def fetch_work(self, aweme_id: str) -> ResolvedWork: ...


class MediaAccess(Protocol):
    async def open_video(self, cdn_mirror_urls: tuple[str, ...]) -> RemoteVideo: ...


class HttpMediaAccess:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def open_video(self, cdn_mirror_urls: tuple[str, ...]) -> RemoteVideo:
        upstream = await open_first_available(
            self._client,
            cdn_mirror_urls,
            "video",
        )
        raw_length = upstream.response.headers.get("content-length")
        expected_size: int | None = None
        if raw_length is not None:
            try:
                parsed_length = int(raw_length)
            except ValueError:
                parsed_length = 0
            if parsed_length > 0:
                expected_size = parsed_length
        return RemoteVideo(
            content_type=upstream.content_type,
            expected_size=expected_size,
            chunks=upstream.iter_bytes(),
        )


class FolderOpener(Protocol):
    def open_folder(self, path: Path) -> None: ...


class WindowsFolderOpener:
    def open_folder(self, path: Path) -> None:
        if os.name != "nt":
            raise OSError("opening archive folders requires Windows")
        os.startfile(path)  # noqa: S606 - validated registered local path


class WindowsDirectoryChooser:
    def choose_directory(self) -> Path | None:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        try:
            selected = filedialog.askdirectory(
                parent=root,
                title="选择本地归档目录",
                mustexist=True,
            )
        finally:
            root.destroy()
        return Path(selected) if selected else None


class ManagedArchive:
    def __init__(
        self,
        *,
        database_path: Path,
        work_access: WorkAccess,
        media_access: MediaAccess,
        folder_opener: FolderOpener | None = None,
    ) -> None:
        self._database_path = database_path
        self._work_access = work_access
        self._media_access = media_access
        self._folder_opener = folder_opener or WindowsFolderOpener()

    async def archive_single(
        self,
        request: SingleArchiveRequest,
    ) -> ArchiveOperationSnapshot:
        root = request.archive_root
        if not root.is_absolute() or not root.is_dir():
            raise AppError(
                "ARCHIVE_ROOT_INVALID",
                "请选择有效的本地归档目录。",
                400,
            )
        root = root.resolve(strict=True)
        if not request.aweme_id.isdigit():
            raise AppError("INVALID_INPUT", "作品标识无效。", 400)

        existing = self._load_completed_archive(request.aweme_id)
        if existing is not None:
            snapshot, stored_root = existing
            video_path = (
                stored_root
                / snapshot.archive_item.relative_directory
                / f"{request.aweme_id}.mp4"
            )
            if video_path.is_file():
                return snapshot

        resolved = await self._work_access.fetch_work(request.aweme_id)
        if resolved.snapshot.aweme_id != request.aweme_id:
            raise AppError("UPSTREAM_BLOCKED", "解析服务暂时不可用，请稍后重试。", 502)
        playback = resolved.preferred_playback_source()
        relative_directory = _work_directory(resolved)
        work_directory = root / relative_directory
        final_path = work_directory / f"{request.aweme_id}.mp4"
        part_path = final_path.with_suffix(".mp4.part")

        ids = _TaskIds(uuid4().hex, uuid4().hex, uuid4().hex)
        connection = self._connect()
        try:
            self._create_running_records(connection, ids, request.aweme_id, root)
            work_directory.mkdir(parents=True, exist_ok=True)
            remote = await self._media_access.open_video(playback.cdn_mirror_urls)
            if remote.content_type.split(";", 1)[0].lower() != "video/mp4":
                raise _archive_failed()
            written = 0
            with part_path.open("wb") as output:
                async for chunk in remote.chunks:
                    if chunk:
                        written += len(chunk)
                        output.write(chunk)
                output.flush()
            if remote.expected_size is not None and written != remote.expected_size:
                raise _archive_failed()
            duration_ms = _inspect_mp4(part_path)
            expected_duration = resolved.snapshot.duration_ms
            tolerance = max(2_000, int(expected_duration * 0.15))
            if expected_duration > 0 and abs(duration_ms - expected_duration) > tolerance:
                raise _archive_failed()

            part_path.replace(final_path)
            try:
                self._complete_records(
                    connection,
                    ids,
                    request.aweme_id,
                    root,
                    relative_directory,
                )
            except BaseException:
                final_path.unlink(missing_ok=True)
                raise
        except BaseException:
            part_path.unlink(missing_ok=True)
            self._fail_records(connection, ids)
            raise
        finally:
            connection.close()

        return _completed_snapshot(ids, request.aweme_id, relative_directory)

    def get_work_archive(self, aweme_id: str) -> ArchiveItemSnapshot | None:
        existing = self._load_completed_archive(aweme_id)
        return existing[0].archive_item if existing is not None else None

    def open_work_folder(self, aweme_id: str) -> None:
        existing = self._load_completed_archive(aweme_id)
        if existing is None:
            raise AppError("ARCHIVE_NOT_FOUND", "没有找到该作品的本地归档。", 404)
        snapshot, stored_root = existing
        try:
            resolved_root = stored_root.resolve(strict=True)
            work_directory = (
                resolved_root / snapshot.archive_item.relative_directory
            ).resolve(strict=True)
        except OSError as error:
            raise AppError("ARCHIVE_LOCATION_UNAVAILABLE", "归档位置当前不可用。", 409) from error
        if not work_directory.is_relative_to(resolved_root) or not work_directory.is_dir():
            raise AppError("ARCHIVE_PATH_INVALID", "归档路径无效。", 409)
        try:
            self._folder_opener.open_folder(work_directory)
        except OSError as error:
            raise AppError("ARCHIVE_OPEN_FAILED", "无法打开归档文件夹。", 500) from error

    def _load_completed_archive(
        self,
        aweme_id: str,
    ) -> tuple[ArchiveOperationSnapshot, Path] | None:
        if not self._database_path.is_file():
            return None
        connection = self._connect()
        try:
            row = connection.execute(
                """
                SELECT o.operation_id, s.source_task_id, w.work_task_id,
                       i.root_path, i.relative_directory, i.status
                FROM archive_items AS i
                JOIN archive_operations AS o ON o.operation_id = i.operation_id
                JOIN source_tasks AS s ON s.operation_id = o.operation_id
                JOIN work_tasks AS w ON w.source_task_id = s.source_task_id
                WHERE i.aweme_id = ? AND i.status = 'archived'
                """,
                (aweme_id,),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            return None
        ids = _TaskIds(str(row[0]), str(row[1]), str(row[2]))
        relative_directory = Path(str(row[4]))
        return (
            _completed_snapshot(ids, aweme_id, relative_directory),
            Path(str(row[3])),
        )

    def _connect(self) -> sqlite3.Connection:
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self._database_path)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS archive_operations (
                operation_id TEXT PRIMARY KEY,
                lifecycle TEXT NOT NULL,
                phase TEXT NOT NULL,
                result TEXT NOT NULL,
                root_path TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS source_tasks (
                source_task_id TEXT PRIMARY KEY,
                operation_id TEXT NOT NULL REFERENCES archive_operations(operation_id),
                lifecycle TEXT NOT NULL,
                phase TEXT NOT NULL,
                result TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS work_tasks (
                work_task_id TEXT PRIMARY KEY,
                source_task_id TEXT NOT NULL REFERENCES source_tasks(source_task_id),
                aweme_id TEXT NOT NULL,
                lifecycle TEXT NOT NULL,
                phase TEXT NOT NULL,
                result TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS archive_items (
                aweme_id TEXT PRIMARY KEY,
                operation_id TEXT NOT NULL REFERENCES archive_operations(operation_id),
                root_path TEXT NOT NULL,
                relative_directory TEXT NOT NULL,
                status TEXT NOT NULL
            );
            """
        )
        return connection

    @staticmethod
    def _create_running_records(
        connection: sqlite3.Connection,
        ids: _TaskIds,
        aweme_id: str,
        root: Path,
    ) -> None:
        with connection:
            connection.execute(
                "INSERT INTO archive_operations VALUES (?, 'running', 'resolving', 'none', ?)",
                (ids.operation, str(root)),
            )
            connection.execute(
                "INSERT INTO source_tasks VALUES (?, ?, 'running', 'resolving', 'none')",
                (ids.source, ids.operation),
            )
            connection.execute(
                "INSERT INTO work_tasks VALUES (?, ?, ?, 'running', 'resolving', 'none')",
                (ids.work, ids.source, aweme_id),
            )

    @staticmethod
    def _complete_records(
        connection: sqlite3.Connection,
        ids: _TaskIds,
        aweme_id: str,
        root: Path,
        relative_directory: Path,
    ) -> None:
        with connection:
            _set_task_results(connection, ids, "success")
            connection.execute(
                "INSERT INTO archive_items VALUES (?, ?, ?, ?, 'archived')",
                (aweme_id, ids.operation, str(root), str(relative_directory)),
            )

    @staticmethod
    def _fail_records(connection: sqlite3.Connection, ids: _TaskIds) -> None:
        try:
            with connection:
                _set_task_results(connection, ids, "failed")
        except sqlite3.Error:
            pass


@dataclass(frozen=True, slots=True)
class _TaskIds:
    operation: str
    source: str
    work: str


def _set_task_results(
    connection: sqlite3.Connection,
    ids: _TaskIds,
    result: str,
) -> None:
    values = ("finished", "idle", result)
    connection.execute(
        "UPDATE archive_operations SET lifecycle=?, phase=?, result=? "
        "WHERE operation_id=?",
        (*values, ids.operation),
    )
    connection.execute(
        "UPDATE source_tasks SET lifecycle=?, phase=?, result=? WHERE source_task_id=?",
        (*values, ids.source),
    )
    connection.execute(
        "UPDATE work_tasks SET lifecycle=?, phase=?, result=? WHERE work_task_id=?",
        (*values, ids.work),
    )


def _completed_snapshot(
    ids: _TaskIds,
    aweme_id: str,
    relative_directory: Path,
) -> ArchiveOperationSnapshot:
    def task(task_id: str) -> TaskSnapshot:
        return TaskSnapshot(task_id, "finished", "idle", "success")

    return ArchiveOperationSnapshot(
        operation=task(ids.operation),
        source_task=task(ids.source),
        work_task=task(ids.work),
        archive_item=ArchiveItemSnapshot(aweme_id, "archived", relative_directory),
    )


def _work_directory(resolved: ResolvedWork) -> Path:
    author_digest = hashlib.sha256(
        resolved.snapshot.author.stable_id.encode("utf-8")
    ).hexdigest()[:16]
    published_at = resolved.snapshot.published_at
    year = (
        str(datetime.fromtimestamp(published_at, UTC).year)
        if published_at is not None
        else "unknown"
    )
    return Path(f"author-{author_digest}") / year / f"work-{resolved.snapshot.aweme_id}"


def _archive_failed() -> AppError:
    return AppError("ARCHIVE_FAILED", "本地归档失败，请稍后重试。", 502)


def _boxes(stream: BinaryIO, start: int, end: int) -> Iterator[tuple[bytes, int, int]]:
    position = start
    while position < end:
        if end - position < 8:
            raise _archive_failed()
        stream.seek(position)
        header = stream.read(8)
        size, kind = struct.unpack(">I4s", header)
        header_size = 8
        if size == 1:
            extended = stream.read(8)
            if len(extended) != 8:
                raise _archive_failed()
            size = struct.unpack(">Q", extended)[0]
            header_size = 16
        elif size == 0:
            size = end - position
        if size < header_size or position + size > end:
            raise _archive_failed()
        payload_start = position + header_size
        box_end = position + size
        yield kind, payload_start, box_end
        position = box_end
    if position != end:
        raise _archive_failed()


def _inspect_mp4(path: Path) -> int:
    file_size = path.stat().st_size
    with path.open("rb") as stream:
        top_level = list(_boxes(stream, 0, file_size))
        kinds = {kind for kind, _, _ in top_level}
        if not {b"ftyp", b"moov", b"mdat"}.issubset(kinds):
            raise _archive_failed()
        movie = next(box for box in top_level if box[0] == b"moov")
        movie_children = list(_boxes(stream, movie[1], movie[2]))
        header = next((box for box in movie_children if box[0] == b"mvhd"), None)
        if header is None:
            raise _archive_failed()
        duration_ms = _movie_duration_ms(stream, header[1], header[2])
        if not any(_track_is_video(stream, box) for box in movie_children if box[0] == b"trak"):
            raise _archive_failed()
        return duration_ms


def _movie_duration_ms(stream: BinaryIO, start: int, end: int) -> int:
    stream.seek(start)
    payload = stream.read(min(end - start, 32))
    if len(payload) < 20:
        raise _archive_failed()
    version = payload[0]
    if version == 0:
        timescale, duration = struct.unpack(">II", payload[12:20])
    elif version == 1 and len(payload) >= 32:
        timescale = struct.unpack(">I", payload[20:24])[0]
        duration = struct.unpack(">Q", payload[24:32])[0]
    else:
        raise _archive_failed()
    if timescale <= 0 or duration <= 0:
        raise _archive_failed()
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
