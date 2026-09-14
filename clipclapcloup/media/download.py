"""Fetching the source video, using yt-dlp as a library rather than a CLI.

Calling it in-process gets us exact progress numbers through its own hooks —
no parsing of console output — and one less binary to ship.

YouTube decides how much to trust a request partly from which of its own
clients we claim to be. The default web client now demands a token that only
real browser JavaScript can mint, which is what the "confirm you're not a bot"
wall is; the TV client is held to a much lower bar and works without any
account. So we ask as the TV client first, and only fall back to browser
cookies if the user has pointed us at a browser in the settings.
"""
from __future__ import annotations

from pathlib import Path

import yt_dlp

from .ffmpeg import ffmpeg_dir

# 1080p is plenty for a 9:16 export and downloads far faster than 4K.
FORMAT = "bv*[height<=1080]+ba/b[height<=1080]"

# Which of YouTube's own clients to impersonate. Pairing cookies with the TV
# client invalidates the session, so the authenticated attempt uses a browser
# client instead.
CLIENTS_ANONYMOUS = ["tv", "web_safari"]
CLIENTS_WITH_COOKIES = ["web_safari"]

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
)


class DownloadError(RuntimeError):
    pass


def _mentions(error: Exception | None, hints) -> bool:
    if error is None:
        return False
    message = str(error).lower()
    return any(hint in message for hint in hints)


def _looks_like_a_bot_check(error: Exception | None) -> bool:
    return _mentions(error, BOT_CHECK_HINTS)


def _options(settings: dict | None, with_cookies: bool) -> dict:
    settings = settings or {}
    options = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "consoletitle": False,
        "extractor_args": {
            "youtube": {
                "player_client": CLIENTS_WITH_COOKIES if with_cookies else CLIENTS_ANONYMOUS
            }
        },
    }
    if with_cookies:
        browser = (settings.get("cookies_browser") or "").strip().lower()
        if browser:
            options["cookiesfrombrowser"] = (browser,)
    return options


def _attempts(settings: dict | None) -> list[bool]:
    """Anonymous first; add the cookie attempt only if a browser is set."""
    browser = ((settings or {}).get("cookies_browser") or "").strip()
    return [False, True] if browser else [False]


def _explain(
    anonymous_error: Exception | None,
    cookie_error: Exception | None,
    settings: dict | None,
) -> DownloadError:
    """Turn whichever attempts failed into one sentence worth reading.

    The cookie attempt runs second, so its error is the most recent — but if it
    failed because the cookie jar itself could not be opened, that has nothing
    to do with the video, and reporting it alone hides what YouTube actually
    said."""
    browser = ((settings or {}).get("cookies_browser") or "").strip()

    if _mentions(cookie_error, COOKIE_PROBLEM_HINTS):
        aside = ""
        if _looks_like_a_bot_check(anonymous_error):
            aside = " Without cookies, YouTube asked to confirm you're not a bot."
        elif anonymous_error is not None:
            aside = f" Without cookies it failed too: {anonymous_error}"
        return DownloadError(
            f"The app could not read {browser or 'that browser'}'s cookies. Chrome and Edge "
            "encrypt them in a way this cannot open on Windows, and they keep the file locked "
            "while running. In Settings, either switch to Firefox or set it back to no browser."
            + aside
        )

    if _looks_like_a_bot_check(cookie_error):
        return DownloadError(
            "YouTube refused the download even with your browser cookies. Wait a few minutes "
            "and try again, and check you are signed in to YouTube in that browser."
        )

    if _looks_like_a_bot_check(anonymous_error) and not browser:
        return DownloadError(
            "YouTube is asking to confirm you're not a bot. Try again in a few minutes — and if "
            "it keeps happening, open Settings and point the app at Firefox to borrow its session."
        )

    return DownloadError(f"Could not read that video: {cookie_error or anonymous_error}")


def fetch_metadata(url: str, settings: dict | None = None) -> dict:
    """Title, description and tags of the source video, without downloading
    it — used to name the folder and draft captions."""
    failures: dict[bool, Exception] = {}

    for with_cookies in _attempts(settings):
        try:
            with yt_dlp.YoutubeDL(_options(settings, with_cookies)) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as exc:  # noqa: BLE001 - yt-dlp raises a wide range of errors
            failures[with_cookies] = exc
            continue

        if info is None:
            failures[with_cookies] = RuntimeError("no video information came back")
            continue

        return {
            "id": info.get("id") or "",
            "title": info.get("title") or "Clip",
            "description": (info.get("description") or "")[:800],
            "tags": list(info.get("tags") or [])[:10],
            "duration": float(info.get("duration") or 0),
            "uploader": info.get("uploader") or "",
        }

    raise _explain(failures.get(False), failures.get(True), settings)


def download_source(
    url: str,
    dest_dir: Path,
    on_progress=None,
    settings: dict | None = None,
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

    failures: dict[bool, Exception] = {}
    for with_cookies in _attempts(settings):
        options = {
            **_options(settings, with_cookies),
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
            failures[with_cookies] = exc
            seen.clear()
            continue

        # Prefer the merged container. Video and audio arrive as separate
        # files named source.f137.mp4 and the like; if a merge went wrong one
        # of those can survive, and picking it would hand the next stage a
        # clip with no sound.
        merged = [dest_dir / f"source.{ext}" for ext in ("mp4", "mkv", "webm")]
        for candidate in merged:
            if candidate.exists():
                return candidate

        leftovers = sorted(
            (p for p in dest_dir.glob("source.*") if p.suffix.lower() != ".part"),
            key=lambda p: p.stat().st_size,
            reverse=True,
        )
        if leftovers:
            return leftovers[0]
        failures[with_cookies] = RuntimeError("the download finished but produced no video file")

    raise _explain(failures.get(False), failures.get(True), settings)
