"""Fetching the source video, using yt-dlp as a library rather than a CLI.

Calling it in-process gets us exact progress numbers through its own hooks —
no parsing of console output — and one less binary to ship.
"""
from __future__ import annotations

from pathlib import Path

import yt_dlp

from .ffmpeg import ffmpeg_dir

# 1080p is plenty for a 9:16 export and downloads far faster than 4K.
FORMAT = "bv*[height<=1080]+ba/b[height<=1080]"


class DownloadError(RuntimeError):
    pass


def _base_options() -> dict:
    return {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "consoletitle": False,
    }


def fetch_metadata(url: str) -> dict:
    """Title, description and tags of the source video, without downloading
    it — used to name the folder and draft captions."""
    options = _base_options()
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:  # noqa: BLE001 - yt-dlp raises a wide range of errors
        raise DownloadError(f"Could not read that video: {exc}") from exc

    if info is None:
        raise DownloadError("Could not read that video.")

    return {
        "id": info.get("id") or "",
        "title": info.get("title") or "Clip",
        "description": (info.get("description") or "")[:800],
        "tags": list(info.get("tags") or [])[:10],
        "duration": float(info.get("duration") or 0),
        "uploader": info.get("uploader") or "",
    }


def download_source(url: str, dest_dir: Path, on_progress=None) -> Path:
    """Download the full video into dest_dir and return the resulting file.

    Progress is reported across the whole download, not per stream: video and
    audio arrive as two separate files, and a bar that fills up twice reads as
    a bug. We total the bytes of every stream whose size is known and report
    against that.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    seen: dict[str, tuple[int, int]] = {}  # filename -> (downloaded, total)

    def hook(event: dict) -> None:
        if not on_progress:
            return
        name = event.get("filename") or event.get("tmpfilename") or ""
        if event.get("status") == "downloading":
            total = event.get("total_bytes") or event.get("total_bytes_estimate") or 0
            seen[name] = (event.get("downloaded_bytes") or 0, int(total))
        elif event.get("status") == "finished":
            done, total = seen.get(name, (0, 0))
            seen[name] = (total or done, total or done)

        downloaded = sum(d for d, _ in seen.values())
        known_total = sum(t for _, t in seen.values())
        if known_total <= 0:
            return
        try:
            on_progress(max(0.0, min(100.0, downloaded / known_total * 100)))
        except Exception:  # noqa: BLE001
            pass

    options = {
        **_base_options(),
        "format": FORMAT,
        "merge_output_format": "mp4",
        "outtmpl": str(dest_dir / "source.%(ext)s"),
        "ffmpeg_location": ffmpeg_dir(),
        "progress_hooks": [hook],
        "retries": 3,
    }

    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.download([url])
    except Exception as exc:  # noqa: BLE001
        raise DownloadError(f"Download failed: {exc}") from exc

    candidates = sorted(
        (p for p in dest_dir.glob("source.*") if p.suffix.lower() != ".part"),
        key=lambda p: p.stat().st_size,
        reverse=True,
    )
    if not candidates:
        raise DownloadError("The download finished but produced no video file.")
    return candidates[0]
