# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller recipe for ClipClapCloup, in either shape.

Set CCC_ONEFILE=0 to build the folder version instead of the single file.
The two exist for one reason: antivirus engines treat a self-extracting
single executable with far more suspicion than a plain folder of files.

ffmpeg is expected at bin/ffmpeg.exe before building — the CI workflow
downloads it. Building locally, drop a Windows ffmpeg.exe there yourself.
"""
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

ONEFILE = os.environ.get("CCC_ONEFILE", "1") != "0"

def collect_safely(package):
    """collect_all for a package that may legitimately be absent (the
    Windows-only .NET bridge, when building elsewhere)."""
    try:
        return collect_all(package)
    except Exception:  # noqa: BLE001 - a missing optional package is not a build failure
        return [], [], []


# yt-dlp loads its extractors by name at runtime, so nothing but a full
# collect gets them into the bundle.
ytdlp_datas, ytdlp_binaries, ytdlp_hidden = collect_all("yt_dlp")

# pywebview drives the Edge WebView2 control through pythonnet. Its runtime
# assemblies are loaded dynamically, which PyInstaller cannot see on its own —
# miss them and the window silently refuses to open in the packaged build.
extra_datas, extra_binaries, extra_hidden = [], [], []
for package in ("webview", "clr_loader", "pythonnet"):
    package_datas, package_binaries, package_hidden = collect_safely(package)
    extra_datas += package_datas
    extra_binaries += package_binaries
    extra_hidden += package_hidden

datas = [
    ("clipclapcloup/web", "clipclapcloup/web"),
    ("assets/fonts", "assets/fonts"),
    *ytdlp_datas,
    *extra_datas,
]

binaries = list(ytdlp_binaries) + extra_binaries
if Path("bin/ffmpeg.exe").exists():
    binaries.append(("bin/ffmpeg.exe", "bin"))

analysis = Analysis(
    ["run.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=[
        *ytdlp_hidden,
        *extra_hidden,
        "webview",
        "webview.platforms.edgechromium",
        "webview.platforms.winforms",
        "clr_loader",
        "qrcode",
        "PIL.Image",
        "PIL.ImageDraw",
        "PIL.ImageFont",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "pytest", "setuptools"],
    noarchive=False,
)

pyz = PYZ(analysis.pure)

common = dict(
    name="ClipClapCloup",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon="assets/icon.ico",
)

if ONEFILE:
    # Everything in one file. Convenient to hand over, but the self-extracting
    # stub is the same shape packers use, so antivirus engines flag it far more
    # often — hence the folder build below as an alternative.
    exe = EXE(
        pyz,
        analysis.scripts,
        analysis.binaries,
        analysis.datas,
        [],
        runtime_tmpdir=None,
        **common,
    )
else:
    exe = EXE(pyz, analysis.scripts, [], exclude_binaries=True, **common)
    collected = COLLECT(
        exe,
        analysis.binaries,
        analysis.datas,
        strip=False,
        upx=False,
        name="ClipClapCloup",
    )
