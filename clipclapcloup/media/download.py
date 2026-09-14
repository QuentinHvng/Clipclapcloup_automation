"""Fetching the source video, using yt-dlp as a library rather than a CLI.

Calling it in-process gets us exact progress numbers through its own hooks —
no parsing of console output — and one less binary to ship.

How much YouTube trusts a request depends partly on which of its own clients
we claim to be, and which ones work changes every few months. Betting on a
single one is how you end up broken; so we try a short list in order, keep
what yt-dlp decides on its own first, and fall back to browser cookies only
if the user has pointed us at a browser. Every attempt is announced, so the
job log shows which one got through.
"""
from __future__ import annotations

from pathlib import Path

import yt_dlp

from .ffmpeg import ffmpeg_dir

# 1080p is plenty for a 9:16 export and downloads far faster than 4K.
FORMAT = "bv*[height<=1080]+ba/b[height<=1080]"

BOT_CHECK_HINTS = ("not a bot", "sign in to confirm")

# Reading a browser's cookie jar fails in its own distinctive ways, and those
# say nothing about YouTube. Chrome and Edge encrypt theirs with a scheme
# yt-dlp cannot open on Windows, and they hold the database open while running.
COOKIE_PROBLEM_HINTS = (
    "dpapi",
    "decrypt",
    "could not copy",
    "cookie database",
    "permission denied",
    "no such file",
    "could not find",
    "unsupported browser",
)

# In order. The first is yt-dlp's own judgement, which is what the app used
# before any of this and works most of the time; the rest are escape hatches
# for when YouTube decides it doesn't like the look of us.
CLIENT_ATTEMPTS: list[tuple[str, list[str] | None]] = [
    ("yt-dlp's default", None),
    ("the TV client", ["tv"]),
    ("the Safari client", ["web_safari"]),
    ("the iOS client", ["ios"]),
]

# Cookies and the TV client cancel each other out, so the authenticated try
# uses a browser client.
COOKIE_CLIENTS = ["web_safari"]


class DownloadError(RuntimeError):
    pass


def _mentions(error: Exception | None, hints) -> bool:
    if error is None:
        return False
    message = str(error).lower()
    return any(hint in message for hint in hints)


def _is_bot_check(error: Exception | None) -> bool:
    return _mentions(error, BOT_CHECK_HINTS)


def _is_cookie_problem(error: Exception | None) -> bool:
    return _mentions(error, COOKIE_PROBLEM_HINTS)


def _options(clients: list[str] | None, browser: str = "") -> dict:
    options = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "consoletitle": False,
    }
    if clients:
        options["extractor_args"] = {"youtube": {"player_client": clients}}
    if browser:
        options["cookiesfrombrowser"] = (browser,)
    return options


def _plan(settings: dict | None) -> list[tuple[str, dict]]:
    """The ordered list of (description, yt-dlp options) to try."""
    plan = [(label, _options(clients)) for label, clients in CLIENT_ATTEMPTS]
    browser = ((settings or {}).get("cookies_browser") or "").strip().lower()
    if browser:
        plan.append((f"{browser}'s cookies", _options(COOKIE_CLIENTS, browser)))
    return plan


def _explain(failures: list[tuple[str, Exception]], settings: dict | None) -> DownloadError:
    """One sentence worth reading, out of everything that went wrong."""
    browser = ((settings or {}).get("cookies_browser") or "").strip()
    errors = [error for _, error in failures]

    cookie_note = ""
    if browser and any(_is_cookie_problem(error) for error in errors):
        cookie_note = (
            f" (Your {browser} cookies could not be read either — Chrome and Edge encrypt "
            "theirs in a way this cannot open on Windows, and a browser that isn't installed "
            "has nothing to read. Set it back to no browser in Settings if it isn't helping.)"
        )

    if any(_is_bot_check(error) for error in errors):
        return DownloadError(
            "YouTube blocked every way this app knows how to ask, saying it wants to confirm "
            "you're not a bot. This usually passes on its own after a few minutes — it depends "
            "on your connection, not on the video." + cookie_note
        )

    real = next((error for error in errors if not _is_cookie_problem(error)), None)
    return DownloadError(f"Could not read that video: {real or (errors[0] if errors else 'unknown failure')}")


def _note(on_note, message: str) -> None:
    if on_note:
        try:
            on_note(message)
        except Exception:  # noqa: BLE001
            pass


def fetch_metadata(url: str, settings: dict | None = None, on_note=None) -> dict:
    """Title, description and tags of the source video, without downloading
    it — used to name the folder and draft captions."""
    failures: list[tuple[str, Exception]] = []

    for label, options in _plan(settings):
        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as exc:  # noqa: BLE001 - yt-dlp raises a wide range of errors
            failures.append((label, exc))
            _note(on_note, f"Asking with {label} — refused.")
            continue

        if info is None:
            failures.append((label, RuntimeError("no video information came back")))
            continue

        if failures:
            _note(on_note, f"Asking with {label} — accepted.")
        return {
            "id": info.get("id") or "",
            "title": info.get("title") or "Clip",
            "description": (info.get("description") or "")[:800],
            "tags": list(info.get("tags") or [])[:10],
            "duration": float(info.get("duration") or 0),
            "uploader": info.get("uploader") or "",
            # remember what worked so the download doesn't start from scratch
            "_options": options,
        }

    raise _explain(failures, settings)


def download_source(
    url: str,
    dest_dir: Path,
    on_progress=None,
    settings: dict | None = None,
    preferred_options: dict | None = None,
    on_note=None,
) -> Path:
    """Download the full video into dest_dir and return the resulting file.

    Progress is reported across the whole download, not per stream: video and
    audio arrive as two separate files, and a bar that fills up twice reads as
    a bug. We total the bytes of every stream whose size is known and report
    against that.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    seen: dict[str, tuple[int, int]] = {}

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

    plan = _plan(settings)
    if preferred_options is not None:
        # Whatever answered a moment ago is the best bet; keep the rest as backup.
        plan = [("the same way as before", preferred_options)] + [
            entry for entry in plan if entry[1] != preferred_options
        ]

    failures: list[tuple[str, Exception]] = []
    for label, base_options in plan:
        options = {
            **base_options,
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
            failures.append((label, exc))
            _note(on_note, f"Downloading with {label} — refused.")
            seen.clear()
            continue

        # Prefer the merged container. Video and audio arrive as separate
        # files named source.f137.mp4 and the like; if a merge went wrong one
        # of those can survive, and picking it would hand the next stage a
        # clip with no sound.
        for extension in ("mp4", "mkv", "webm"):
            candidate = dest_dir / f"source.{extension}"
            if candidate.exists():
                return candidate

        leftovers = sorted(
            (p for p in dest_dir.glob("source.*") if p.suffix.lower() != ".part"),
            key=lambda p: p.stat().st_size,
            reverse=True,
        )
        if leftovers:
            return leftovers[0]
        failures.append((label, RuntimeError("the download finished but produced no video file")))

    raise _explain(failures, settings)
