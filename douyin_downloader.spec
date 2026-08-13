# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

PROJECT_ROOT = Path(SPECPATH)
FFMPEG_ROOT = PROJECT_ROOT / ".deps" / "ffmpeg"
FFMPEG_BIN = FFMPEG_ROOT / "bin"
FFMPEG_REQUIRED = (
    FFMPEG_BIN / "ffmpeg.exe",
    FFMPEG_BIN / "ffprobe.exe",
    FFMPEG_ROOT / "audit.json",
    PROJECT_ROOT / "third_party" / "ffmpeg" / "manifest.json",
    PROJECT_ROOT / "third_party" / "ffmpeg" / "NOTICE.md",
)
missing_ffmpeg = [str(path) for path in FFMPEG_REQUIRED if not path.is_file()]
if missing_ffmpeg:
    raise RuntimeError("Audited FFmpeg distribution is incomplete")

FFMPEG_BINARIES = [
    (str(path), "ffmpeg")
    for path in sorted(FFMPEG_BIN.iterdir())
    if path.is_file()
    and (
        path.suffix.casefold() == ".dll"
        or path.name.casefold() in {"ffmpeg.exe", "ffprobe.exe"}
    )
]
FFMPEG_COMPLIANCE_DATA = [
    (str(FFMPEG_ROOT / "audit.json"), "third-party/ffmpeg"),
    (
        str(PROJECT_ROOT / "third_party" / "ffmpeg" / "manifest.json"),
        "third-party/ffmpeg",
    ),
    (
        str(PROJECT_ROOT / "third_party" / "ffmpeg" / "NOTICE.md"),
        "third-party/ffmpeg",
    ),
    *[
        (str(path), "third-party/ffmpeg/licenses")
        for path in sorted((FFMPEG_ROOT / "licenses").iterdir())
        if path.is_file()
    ],
]
STATIC_DATA = [
    (
        str(PROJECT_ROOT / "src" / "douyin_downloader" / "web" / "static"),
        "douyin_downloader/web/static",
    ),
    (str(PROJECT_ROOT / "assets" / "app-icon.ico"), "assets"),
    (str(PROJECT_ROOT / "assets" / "app-icon.svg"), "assets"),
    *FFMPEG_COMPLIANCE_DATA,
]
HIDDEN_IMPORTS = [
    "f2.utils.abogus",
    "pystray._win32",
    "uvicorn.lifespan.on",
    "uvicorn.logging",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.websockets_impl",
]

a = Analysis(
    [str(PROJECT_ROOT / "src" / "douyin_downloader" / "__main__.py")],
    pathex=[str(PROJECT_ROOT / "src")],
    binaries=FFMPEG_BINARIES,
    datas=STATIC_DATA,
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
