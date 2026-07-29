# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files


PROJECT_ROOT = Path(SPECPATH)
F2_DATA = collect_data_files("f2", include_py_files=False)
STATIC_DATA = [
    (
        str(PROJECT_ROOT / "src" / "douyin_downloader" / "web" / "static"),
        "douyin_downloader/web/static",
    ),
    (str(PROJECT_ROOT / "assets" / "app-icon.ico"), "assets"),
    (str(PROJECT_ROOT / "assets" / "app-icon.svg"), "assets"),
]
HIDDEN_IMPORTS = [
    "f2.utils.abogus",
    "uvicorn.lifespan.on",
    "uvicorn.logging",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.websockets_impl",
]

a = Analysis(
    [str(PROJECT_ROOT / "src" / "douyin_downloader" / "__main__.py")],
    pathex=[str(PROJECT_ROOT / "src")],
    binaries=[],
    datas=STATIC_DATA + F2_DATA,
    hiddenimports=HIDDEN_IMPORTS,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "tests"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="抖音视频下载",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(PROJECT_ROOT / "assets" / "app-icon.ico"),
)
