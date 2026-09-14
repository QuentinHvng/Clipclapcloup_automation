"""Posting a finished clip through TikTok's Content Posting API."""
from __future__ import annotations

import time
from pathlib import Path

import requests

INIT_URL = "https://open.tiktokapis.com/v2/post/publish/video/init/"
STATUS_URL = "https://open.tiktokapis.com/v2/post/publish/status/fetch/"

MIN_CHUNK = 5 * 1024 * 1024
MAX_CHUNK = 64 * 1024 * 1024

# Until TikTok audits an app, everything it posts is private to the account
# owner. This is TikTok's rule, not a setting we can talk our way around.
PRIVATE = "SELF_ONLY"
PUBLIC = "PUBLIC_TO_EVERYONE"


class PublishError(RuntimeError):
    pass


def _chunk_plan(size: int) -> tuple[int, int]:
    """TikTok's chunking rules are stricter than they look: with more than one
    chunk, *every* chunk — including the last — must be at least 5 MB. Naively
    slicing by a fixed size leaves a tiny tail and the API answers with an
    unhelpful "total chunk count is invalid", so grow the count until the last
    chunk lands in range."""
    if size <= MAX_CHUNK:
        return size, 1
    count = -(-size // MAX_CHUNK)
    while True:
        chunk = -(-size // count)
        last = size - chunk * (count - 1)
        if MIN_CHUNK <= last <= chunk <= MAX_CHUNK:
            return chunk, count
        count += 1


def publish_video(
    access_token: str,
    video_path: Path,
    caption: str,
    privacy_level: str = PRIVATE,
    on_progress=None,
) -> str:
    """Upload the file and hand it to TikTok. Returns the publish id."""
    size = video_path.stat().st_size
    chunk_size, chunk_count = _chunk_plan(size)

    response = requests.post(
        INIT_URL,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=UTF-8",
        },
        json={
            "post_info": {
                "title": caption,
                "privacy_level": privacy_level,
                "disable_duet": False,
                "disable_comment": False,
                "disable_stitch": False,
            },
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": size,
                "chunk_size": chunk_size,
                "total_chunk_count": chunk_count,
            },
        },
        timeout=30,
    )
    if response.status_code != 200:
        raise PublishError(f"TikTok rejected the upload request ({response.status_code}): {response.text[:400]}")

    payload = response.json()
    error = (payload.get("error") or {}).get("code")
    if error not in (None, "ok"):
        message = (payload.get("error") or {}).get("message") or error
        raise PublishError(f"TikTok rejected the upload request: {message}")

    data = payload["data"]
    publish_id = data["publish_id"]
    upload_url = data["upload_url"]

    sent = 0
    with open(video_path, "rb") as handle:
        while sent < size:
            end = min(sent + chunk_size, size) - 1
            handle.seek(sent)
            chunk = handle.read(end - sent + 1)
            put = requests.put(
                upload_url,
                headers={
                    "Content-Type": "video/mp4",
                    "Content-Range": f"bytes {sent}-{end}/{size}",
                },
                data=chunk,
                timeout=180,
            )
            if put.status_code not in (200, 201, 206):
                raise PublishError(f"Upload failed at bytes {sent}-{end}: {put.status_code} {put.text[:300]}")
            sent = end + 1
            if on_progress:
                try:
                    on_progress(sent / size * 100)
                except Exception:  # noqa: BLE001
                    pass

    return publish_id


def wait_for_publish(access_token: str, publish_id: str, timeout_s: int = 300, on_tick=None) -> dict:
    """Poll until TikTok says the post is live or has failed."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        response = requests.post(
            STATUS_URL,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json; charset=UTF-8",
            },
            json={"publish_id": publish_id},
            timeout=30,
        )
        if response.status_code != 200:
            raise PublishError(f"Could not read the post status ({response.status_code}).")
        data = response.json().get("data", {})
        status = data.get("status")
        if on_tick:
            try:
                on_tick(status or "processing")
            except Exception:  # noqa: BLE001
                pass
        if status in ("PUBLISH_COMPLETE", "FAILED"):
            return data
        time.sleep(5)
    raise PublishError("TikTok did not confirm the post in time. Check the app before retrying.")
