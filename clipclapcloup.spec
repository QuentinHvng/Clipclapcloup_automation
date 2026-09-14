# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller recipe: one self-contained ClipClapCloup.exe.

ffmpeg is expected at bin/ffmpeg.exe before building — the CI workflow
downloads it. Building locally, drop a Windows ffmpeg.exe there yourself.
"""
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

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

exe = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="ClipClapCloup",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,
    icon="assets/icon.ico",
)
