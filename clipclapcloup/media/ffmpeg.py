"""Locating and running ffmpeg.

The packaged app ships its own ffmpeg binary, so a user never has to install
anything. Running from source we fall back to whatever is on PATH, which is
what a developer will have.

Every call goes through run_ffmpeg(), which asks ffmpeg for machine-readable
progress on stderr (`-progress pipe:2`) and turns it into a 0-100 percentage.
stdout stays untouched so callers can pipe raw audio out of it.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import threading
from pathlib import Path

from ..config import resource_path

_BINARY_NAME = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"


class FFmpegMissing(RuntimeError):
    """Raised when no usable ffmpeg binary could be found at all."""


def ffmpeg_path() -> str:
    """Absolute path to the ffmpeg binary to use."""
    bundled = resource_path("bin", _BINARY_NAME)
    if bundled.exists():
        return str(bundled)
    found = shutil.which("ffmpeg")
    if found:
        return found
    raise FFmpegMissing(
        "ffmpeg was not found. The packaged app ships with it; running from "
        "source, install ffmpeg and make sure it is on your PATH."
    )


def ffmpeg_dir() -> str:
    """Folder holding ffmpeg — yt-dlp wants this to merge video and audio."""
    return str(Path(ffmpeg_path()).parent)


def has_ffmpeg() -> bool:
    try:
        ffmpeg_path()
        return True
    except FFmpegMissing:
        return False


def _parse_progress_line(line: str) -> float | None:
    """Return the position in seconds from an ffmpeg -progress line, if any.
    Both out_time_us and the older out_time_ms are microseconds despite the
    name, which is a long-standing ffmpeg quirk."""
    for key in ("out_time_us=", "out_time_ms="):
        if line.startswith(key):
            raw = line[len(key):].strip()
            if raw and raw != "N/A":
                try:
                    return int(raw) / 1_000_000
                except ValueError:
                    return None
    return None


def run_ffmpeg(
    args: list[str],
    *,
    duration: float | None = None,
    on_progress=None,
    capture_stdout: bool = False,
) -> bytes:
    """Run ffmpeg with `args` (everything after the global flags).

    duration + on_progress: when both are given, on_progress(percent) is
    called as encoding advances. Returns stdout bytes when capture_stdout is
    set (used to pipe raw PCM out for the loudness analysis), otherwise b"".
    Raises RuntimeError carrying ffmpeg's own error output on failure.
    """
    cmd = [
        ffmpeg_path(),
        "-hide_banner",
        "-nostdin",
        "-y",
        "-nostats",
        "-loglevel", "error",
        "-progress", "pipe:2",
        *args,
    ]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE if capture_stdout else subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )

    errors: list[str] = []

    def watch_stderr() -> None:
        assert proc.stderr is not None
        for raw in proc.stderr:
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            position = _parse_progress_line(line)
            if position is not None:
                if on_progress and duration:
                    percent = max(0.0, min(100.0, position / duration * 100))
                    try:
                        on_progress(percent)
                    except Exception:  # noqa: BLE001 - a UI hiccup must not kill the encode
                        pass
            elif "=" not in line:
                # real log output rather than a progress key/value pair
                errors.append(line)
                del errors[:-40]

    watcher = threading.Thread(target=watch_stderr, daemon=True)
    watcher.start()

    stdout = b""
    if capture_stdout:
        assert proc.stdout is not None
        stdout = proc.stdout.read()
        proc.stdout.close()

    proc.wait()
    watcher.join(timeout=5)

    if proc.returncode != 0:
        detail = "\n".join(errors[-12:]) or "no error output"
        raise RuntimeError(f"ffmpeg failed:\n{detail}")

    if on_progress and duration:
        try:
            on_progress(100.0)
        except Exception:  # noqa: BLE001
            pass
    return stdout
