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

BOT_CHECK_HINTS = ("not a bot", "sign in to confirm", "cookies")


class DownloadError(RuntimeError):
    pass


def _looks_like_a_bot_check(error: Exception) -> bool:
    message = str(error).lower()
    return any(hint in message for hint in BOT_CHECK_HINTS)


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


def _explain(error: Exception, settings: dict | None) -> DownloadError:
    if _looks_like_a_bot_check(error):
        if ((settings or {}).get("cookies_browser") or "").strip():
            return DownloadError(
                "YouTube refused the download even with your browser cookies. "
                "Wait a few minutes and try again — and check you are signed in to "
                "YouTube in that browser."
            )
        return DownloadError(
            "YouTube is asking to confirm you're not a bot. Open Settings and pick the "
            "browser you watch YouTube in, so the app can borrow its session."
        )
    return DownloadError(f"Could not read that video: {error}")


def fetch_metadata(url: str, settings: dict | None = None) -> dict:
    """Title, description and tags of the source video, without downloading
    it — used to name the folder and draft captions."""
    last_error: Exception | None = None

    for with_cookies in _attempts(settings):
        try:
            with yt_dlp.YoutubeDL(_options(settings, with_cookies)) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as exc:  # noqa: BLE001 - yt-dlp raises a wide range of errors
            last_error = exc
            continue

        if info is None:
            last_error = RuntimeError("no video information came back")
            continue

        return {
            "id": info.get("id") or "",
            "title": info.get("title") or "Clip",
            "description": (info.get("description") or "")[:800],
            "tags": list(info.get("tags") or [])[:10],
            "duration": float(info.get("duration") or 0),
            "uploader": info.get("uploader") or "",
        }

    raise _explain(last_error or RuntimeError("unknown failure"), settings)


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

    last_error: Exception | None = None
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
            last_error = exc
            seen.clear()
            continue

        candidates = sorted(
            (p for p in dest_dir.glob("source.*") if p.suffix.lower() != ".part"),
            key=lambda p: p.stat().st_size,
            reverse=True,
        )
        if candidates:
            return candidates[0]
        last_error = RuntimeError("the download finished but produced no video file")

    raise _explain(last_error or RuntimeError("unknown failure"), settings)
