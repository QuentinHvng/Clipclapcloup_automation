"""Where things live on disk, and the settings the user can change.

Two separate places, on purpose:

  * app data (settings, the clip library, TikTok tokens) lives in the OS's
    per-user config folder — %APPDATA%\\ClipClapCloup on Windows. The user
    never needs to open it.
  * the clips themselves land in Videos\\ClipClapCloup, where they're easy
    to find, preview and drag into a phone-sync folder.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from . import APP_NAME

# The port the local server listens on. It is also baked into the TikTok
# redirect URI the user registers, so it is deliberately fixed rather than
# "whatever is free" — see tiktok/auth.py.
SERVER_PORT = 8765


def is_frozen() -> bool:
    """True when running from the packaged .exe rather than from source."""
    return getattr(sys, "frozen", False)


def resource_path(*parts: str) -> Path:
    """Locate a file shipped with the app (fonts, web assets, ffmpeg).

    PyInstaller unpacks those into a temp folder it points sys._MEIPASS at;
    running from source, they sit next to the repository root."""
    if is_frozen():
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    else:
        base = Path(__file__).resolve().parent.parent
    return base.joinpath(*parts)


def app_data_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    path = base / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_clips_dir() -> Path:
    for candidate in ("Videos", "Movies"):
        folder = Path.home() / candidate
        if folder.is_dir():
            return folder / APP_NAME
    return Path.home() / APP_NAME


SETTINGS_PATH = lambda: app_data_dir() / "settings.json"  # noqa: E731
LIBRARY_PATH = lambda: app_data_dir() / "library.json"  # noqa: E731
TOKENS_PATH = lambda: app_data_dir() / "tokens.json"  # noqa: E731
EMOJI_CACHE_DIR = lambda: app_data_dir() / "emoji-cache"  # noqa: E731


DEFAULT_SETTINGS: dict = {
    # first-run wizard
    "onboarded": False,
    # where finished clips are written
    "clips_dir": "",  # empty means default_clips_dir()
    # cutting defaults, editable in the UI
    "parts": 3,
    "part_duration": 70,
    "overlap": 5,
    "mode": "blur-pad",  # or "crop"
    "burn_part_label": True,
    # which browser's cookies to borrow when YouTube demands a sign-in
    # ("" = none, otherwise firefox / chrome / edge / brave / opera / chromium)
    "cookies_browser": "",
    # captions
    "caption_language": "en",  # "en" or "fr"
    "anthropic_api_key": "",
    "anthropic_model": "claude-haiku-4-5",
    # TikTok API (optional, advanced)
    "tiktok_client_key": "",
    "tiktok_client_secret": "",
}


def load_settings() -> dict:
    settings = dict(DEFAULT_SETTINGS)
    path = SETTINGS_PATH()
    if path.exists():
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            stored = {}
        settings.update({k: v for k, v in stored.items() if k in DEFAULT_SETTINGS})
    return settings


def save_settings(patch: dict) -> dict:
    """Merge `patch` into the stored settings and return the new full set.
    Unknown keys are ignored so a stray field from the UI can't pollute the
    file."""
    settings = load_settings()
    settings.update({k: v for k, v in patch.items() if k in DEFAULT_SETTINGS})
    path = SETTINGS_PATH()
    path.write_text(json.dumps(settings, indent=2, ensure_ascii=False), encoding="utf-8")
    _restrict_permissions(path)
    return settings


def clips_dir() -> Path:
    configured = (load_settings().get("clips_dir") or "").strip()
    path = Path(configured) if configured else default_clips_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _restrict_permissions(path: Path) -> None:
    """Best-effort: keep files holding secrets readable by this user only.
    On Windows the per-user AppData folder is already private, and chmod is
    mostly a no-op there, so failure is not worth reporting."""
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
