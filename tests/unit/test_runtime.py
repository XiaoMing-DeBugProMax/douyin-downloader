from __future__ import annotations

import json
import multiprocessing
import os
import secrets
import subprocess
import time
from dataclasses import asdict
from pathlib import Path

import pytest

import douyin_downloader.runtime as runtime_module
from douyin_downloader.runtime import RuntimeInfo, RuntimeStore


def _acquire_instance_mutex_in_child(
    local_app_data: str,
    acquired: multiprocessing.synchronize.Event,
    release: multiprocessing.synchronize.Event,
) -> None:
    os.environ["LOCALAPPDATA"] = local_app_data
    mutex = RuntimeStore().instance_mutex()
    try:
        deadline = time.monotonic() + 5
        while not mutex.is_owner and time.monotonic() < deadline:
            mutex.try_acquire()
            if not mutex.is_owner:
                time.sleep(0.01)
        if mutex.is_owner:
            acquired.set()
            release.wait(5)
    finally:
        mutex.close()


@pytest.fixture
def local_app_data(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    root = tmp_path / "LocalAppData"
    monkeypatch.setenv("LOCALAPPDATA", str(root))
    return root


def runtime_info(*, instance_id: str = "instance-one") -> RuntimeInfo:
    return RuntimeInfo(
        instance_id=instance_id,
        base_url="http://127.0.0.1:43123",
        management_token=secrets.token_urlsafe(32),
        pid=1234,
    )


def test_runtime_store_uses_exact_current_user_local_app_data_path(
    local_app_data: Path,
) -> None:
    store = RuntimeStore()

    assert store.app_dir == local_app_data / "DouyinLocalDownloader"
    assert store.runtime_path == store.app_dir / "runtime.json"


def test_runtime_store_refuses_to_guess_path_without_local_app_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LOCALAPPDATA", raising=False)

    with pytest.raises(RuntimeError, match="LOCALAPPDATA"):
        RuntimeStore()


def test_runtime_store_refuses_relative_local_app_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", "relative-local-app-data")

    with pytest.raises(RuntimeError, match="absolute"):
        RuntimeStore()


@pytest.mark.skipif(os.name != "nt", reason="Windows named mutex contract")
def test_instance_mutex_excludes_another_process(
    local_app_data: Path,
) -> None:
    store = RuntimeStore()
    owner = store.instance_mutex()
    assert owner.is_owner
    context = multiprocessing.get_context("spawn")
    acquired = context.Event()
    release_child = context.Event()
    child = context.Process(
        target=_acquire_instance_mutex_in_child,
        args=(str(local_app_data), acquired, release_child),
    )
    try:
        child.start()
        assert not acquired.wait(0.3)
        owner.close()
        assert acquired.wait(5)
        release_child.set()
        child.join(timeout=5)
        assert child.exitcode == 0
    finally:
        owner.close()
        release_child.set()
        if child.is_alive():
            child.terminate()
            child.join(timeout=5)


def test_runtime_store_writes_with_sibling_replace_and_reads_round_trip(
    local_app_data: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = RuntimeStore()
    info = runtime_info()
    replacements: list[tuple[Path, Path]] = []
    original_replace = Path.replace

    def recording_replace(source: Path, target: Path) -> Path:
        replacements.append((source, target))
        return original_replace(source, target)

    monkeypatch.setattr(Path, "replace", recording_replace)

    store.write(info)

    assert replacements == [(store.runtime_path.with_suffix(".json.tmp"), store.runtime_path)]
    assert not store.runtime_path.with_suffix(".json.tmp").exists()
    assert store.read() == info
    assert json.loads(store.runtime_path.read_text(encoding="utf-8")) == {
        "instance_id": "instance-one",
        "base_url": "http://127.0.0.1:43123",
        "management_token": info.management_token,
        "pid": 1234,
    }


@pytest.mark.parametrize(
    "contents",
    [
        "{not-json",
        json.dumps({"instance_id": "missing-fields"}),
        json.dumps(
            {
                "instance_id": "instance-one",
                "base_url": "http://0.0.0.0:43123",
                "management_token": "management-secret",
                "pid": 1234,
            }
        ),
        json.dumps(
            {
                "instance_id": "instance-one",
                "base_url": "http://127.0.0.1:not-a-port",
                "management_token": "management-secret",
                "pid": 1234,
            }
        ),
        json.dumps(
            {
                "instance_id": "instance-one",
                "base_url": "http://127.0.0.1:43123/path",
                "management_token": "management-secret",
                "pid": 0,
            }
        ),
    ],
)
def test_runtime_store_treats_malformed_or_unsafe_state_as_inactive(
    local_app_data: Path,
    contents: str,
) -> None:
    store = RuntimeStore()
    store.app_dir.mkdir(parents=True)
    store.runtime_path.write_text(contents, encoding="utf-8")

    assert store.read() is None


def test_remove_if_owned_only_removes_matching_instance(local_app_data: Path) -> None:
    store = RuntimeStore()
    store.write(runtime_info(instance_id="new-owner"))

    assert store.remove_if_owned("old-owner") is False
    assert store.runtime_path.exists()
    assert store.remove_if_owned("new-owner") is True
    assert not store.runtime_path.exists()
    assert store.remove_if_owned("new-owner") is False


def test_remove_if_owned_never_unlinks_an_overridden_out_of_bounds_path(
    local_app_data: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = RuntimeStore()
    outside = tmp_path / "outside-runtime.json"
    outside.write_text(json.dumps(asdict(runtime_info())), encoding="utf-8")
    monkeypatch.setattr(store, "runtime_path", outside)

    assert store.remove_if_owned("instance-one") is False
    assert outside.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows junction contract")
def test_runtime_store_rejects_application_directory_junction_escape(
    local_app_data: Path,
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    app_dir = local_app_data / "DouyinLocalDownloader"
    local_app_data.mkdir()
    command = Path(os.environ["SystemRoot"]) / "System32" / "cmd.exe"
    result = subprocess.run(  # noqa: S603
        [str(command), "/d", "/c", "mklink", "/J", str(app_dir), str(outside)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip("Windows junction creation is unavailable")

    info = runtime_info()
    escaped_runtime = outside / "runtime.json"
    escaped_runtime.write_text(json.dumps(asdict(info)), encoding="utf-8")
    try:
        store = RuntimeStore()
        assert store.read() is None
        assert store.remove_if_owned(info.instance_id) is False
        assert escaped_runtime.exists()
        with pytest.raises(RuntimeError, match="outside"):
            store.write(info)
    finally:
        app_dir.rmdir()


def test_runtime_store_rejects_runtime_file_reparse_point(
    local_app_data: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = RuntimeStore()
    info = runtime_info()
    store.write(info)
    original_is_reparse_point = runtime_module._is_reparse_point

    def fake_reparse_point(path: Path) -> bool:
        return path == store.runtime_path or original_is_reparse_point(path)

    monkeypatch.setattr(runtime_module, "_is_reparse_point", fake_reparse_point)

    assert store.read() is None
    assert store.remove_if_owned(info.instance_id) is False
    assert store.runtime_path.exists()
    with pytest.raises(RuntimeError, match="outside"):
        store.write(info)
