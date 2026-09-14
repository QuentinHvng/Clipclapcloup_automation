"""The clip library: one JSON file that knows every clip and its state.

This replaces the scattered bookkeeping of the original scripts (a folder to
scan plus a separate "already published" file). One record per clip, holding
where the file is, what caption goes with it, and whether it has gone out —
through the API or posted by hand.
"""
from __future__ import annotations

import json
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path

from .config import LIBRARY_PATH

PENDING = "pending"
PUBLISHED_API = "published_api"
PUBLISHED_MANUAL = "published_manual"

_LOCK = threading.RLock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read() -> dict:
    path = LIBRARY_PATH()
    if not path.exists():
        return {"clips": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"clips": []}
    if not isinstance(data, dict) or not isinstance(data.get("clips"), list):
        return {"clips": []}
    return data


def _write(data: dict) -> None:
    path = LIBRARY_PATH()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def all_clips() -> list[dict]:
    """Newest first."""
    with _LOCK:
        clips = _read()["clips"]
    return sorted(clips, key=lambda c: c.get("created_at") or "", reverse=True)


def get(clip_id: str) -> dict | None:
    with _LOCK:
        for clip in _read()["clips"]:
            if clip.get("id") == clip_id:
                return clip
    return None


def by_download_token(token: str) -> dict | None:
    if not token:
        return None
    with _LOCK:
        for clip in _read()["clips"]:
            if clip.get("download_token") == token:
                return clip
    return None


def add(clip: dict) -> dict:
    record = {
        "id": secrets.token_hex(8),
        "download_token": secrets.token_urlsafe(16),
        "created_at": _now(),
        "status": PENDING,
        "published_at": None,
        "publish_id": None,
        "privacy": None,
        **clip,
    }
    with _LOCK:
        data = _read()
        data["clips"].append(record)
        _write(data)
    return record


def update(clip_id: str, **fields) -> dict | None:
    with _LOCK:
        data = _read()
        for clip in data["clips"]:
            if clip.get("id") == clip_id:
                clip.update(fields)
                _write(data)
                return clip
    return None


def mark_published(
    clip_id: str,
    *,
    manual: bool,
    publish_id: str | None = None,
    privacy: str | None = None,
) -> dict | None:
    return update(
        clip_id,
        status=PUBLISHED_MANUAL if manual else PUBLISHED_API,
        published_at=_now(),
        publish_id=publish_id,
        privacy=privacy or ("manual" if manual else None),
    )


def mark_pending(clip_id: str) -> dict | None:
    """Put a clip back in the queue — used when a video was taken down from
    TikTok and shouldn't count as published any more."""
    return update(clip_id, status=PENDING, published_at=None, publish_id=None, privacy=None)


def remove(clip_id: str, delete_file: bool = False) -> bool:
    with _LOCK:
        data = _read()
        remaining = []
        removed = None
        for clip in data["clips"]:
            if clip.get("id") == clip_id:
                removed = clip
            else:
                remaining.append(clip)
        if removed is None:
            return False
        data["clips"] = remaining
        _write(data)

    if delete_file and removed.get("path"):
        path = Path(removed["path"])
        try:
            path.unlink(missing_ok=True)
            parent = path.parent
            if parent.is_dir() and not any(p for p in parent.iterdir() if p.suffix == ".mp4"):
                for leftover in parent.iterdir():
                    leftover.unlink(missing_ok=True)
                parent.rmdir()
        except OSError:
            pass
    return True


def grouped_by_video(status: str | None = None) -> list[dict]:
    """Clips bundled under the video they came from, which is how the library
    screen shows them: one card per source video, its parts inside.

    `status` is "pending", "posted" (either way of posting) or None for all."""
    groups: dict[str, dict] = {}
    for clip in all_clips():
        clip_status = clip.get("status")
        if status == "pending" and clip_status != PENDING:
            continue
        if status == "posted" and clip_status == PENDING:
            continue
        if status not in (None, "pending", "posted") and clip_status != status:
            continue
        key = clip.get("group_key") or clip.get("youtube_url") or clip["id"]
        group = groups.setdefault(
            key,
            {
                "key": key,
                "video_title": clip.get("video_title") or "Untitled",
                "youtube_url": clip.get("youtube_url"),
                "created_at": clip.get("created_at"),
                "folder": str(Path(clip["path"]).parent) if clip.get("path") else "",
                "clips": [],
            },
        )
        group["clips"].append(clip)

    for group in groups.values():
        group["clips"].sort(key=lambda c: c.get("part_index") or 0)
    return sorted(groups.values(), key=lambda g: g.get("created_at") or "", reverse=True)


def write_captions_file(folder: Path) -> None:
    """Keep a plain-text captions.txt next to the clips, so captions can be
    copied without opening the app — handy when posting by hand."""
    clips = [c for c in all_clips() if c.get("path") and Path(c["path"]).parent == folder]
    if not clips:
        return
    clips.sort(key=lambda c: c.get("part_index") or 0)
    lines = [clips[0].get("video_title") or "Clips", ""]
    for clip in clips:
        name = Path(clip["path"]).name
        lines.append(name)
        lines.append(clip.get("caption") or "")
        lines.append("")
    try:
        (folder / "captions.txt").write_text("\n".join(lines), encoding="utf-8")
    except OSError:
        pass
