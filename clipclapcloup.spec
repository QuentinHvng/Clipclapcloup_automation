# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller recipe: one self-contained ClipClapCloup.exe.

ffmpeg is expected at bin/ffmpeg.exe before building — the CI workflow
downloads it. Building locally, drop a Windows ffmpeg.exe there yourself.
"""
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

# yt-dlp loads its extractors by name at runtime, so nothing but a full
# collect gets them into the bundle.
ytdlp_datas, ytdlp_binaries, ytdlp_hidden = collect_all("yt_dlp")

datas = [
    ("clipclapcloup/web", "clipclapcloup/web"),
    ("assets/fonts", "assets/fonts"),
    *ytdlp_datas,
]

binaries = list(ytdlp_binaries)
if Path("bin/ffmpeg.exe").exists():
    binaries.append(("bin/ffmpeg.exe", "bin"))

analysis = Analysis(
    ["run.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=[
        *ytdlp_hidden,
        "webview",
        "webview.platforms.edgechromium",
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
