"""The local HTTP server behind the app window.

Everything the interface does goes through here. Two access levels, enforced
by the client's address rather than a password:

  * the interface and its API answer only to 127.0.0.1 — that is the app
    window itself;
  * the two sharing routes, /d/<token> and /f/<token>, answer to anything on
    the local network, because that is how a phone picks up a clip after
    scanning the QR code. They are reachable only with the clip's own
    unguessable token, and they expose nothing else.
"""
from __future__ import annotations

import io
import json
import mimetypes
import os
import socket
import subprocess
import sys
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import __version__, jobs, library, pipeline
from .config import (
    SERVER_PORT,
    clips_dir,
    load_settings,
    resource_path,
    save_settings,
)
from .media.ffmpeg import has_ffmpeg
from .tiktok import api as tiktok_api
from .tiktok import auth as tiktok_auth

WEB_DIR = lambda: resource_path("clipclapcloup", "web")  # noqa: E731

SECRET_FIELDS = ("tiktok_client_secret", "anthropic_api_key")

# The only external sites the interface may send the user to.
ALLOWED_EXTERNAL_HOSTS = {
    "developers.tiktok.com",
    "www.tiktok.com",
    "console.anthropic.com",
    "platform.claude.com",
}


def local_ip() -> str:
    """This machine's address on the local network, for the QR code. No
    packet is actually sent — connecting a UDP socket just picks the route."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 80))
        return probe.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        probe.close()


def open_in_file_manager(target: Path) -> None:
    """Reveal a file or folder in the OS file manager."""
    target = Path(target)
    if sys.platform == "win32":
        if target.is_file():
            subprocess.Popen(["explorer", "/select,", str(target)])
        else:
            os.startfile(str(target))  # type: ignore[attr-defined]  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R" if target.is_file() else "", str(target)])
    else:
        subprocess.Popen(["xdg-open", str(target if target.is_dir() else target.parent)])


def _public_settings() -> dict:
    """Settings for the UI: secrets become a boolean, never the value."""
    settings = load_settings()
    public = {k: v for k, v in settings.items() if k not in SECRET_FIELDS}
    for field in SECRET_FIELDS:
        public[f"{field}_set"] = bool((settings.get(field) or "").strip())
    public["clips_dir_resolved"] = str(clips_dir())
    return public


def _qr_data_uri(url: str) -> str:
    import base64

    import qrcode

    image = qrcode.make(url)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


class Handler(BaseHTTPRequestHandler):
    server_version = f"ClipClapCloup/{__version__}"

    def log_message(self, *args):  # keep the console quiet
        pass

    # -- plumbing -------------------------------------------------------

    @property
    def is_local(self) -> bool:
        return self.client_address[0] in ("127.0.0.1", "::1", "localhost")

    def _send(self, status: int, body: bytes, content_type: str, extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def json(self, payload, status: int = 200) -> None:
        self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def fail(self, message: str, status: int = 400) -> None:
        self.json({"error": message}, status)

    def html(self, markup: str, status: int = 200) -> None:
        self._send(status, markup.encode("utf-8"), "text/html; charset=utf-8")

    def body_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return {}

    def serve_asset(self, path: Path) -> None:
        if not path.exists() or not path.is_file():
            self.fail("Not found", 404)
            return
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self._send(200, path.read_bytes(), content_type, {"Cache-Control": "no-store"})

    def serve_video(self, path: Path) -> None:
        """Serve an mp4 with range support so previews can seek and phones can
        resume — a plain whole-file response makes both feel broken."""
        if not path.exists():
            self.fail("That clip file is missing.", 404)
            return
        size = path.stat().st_size
        range_header = self.headers.get("Range")
        start, end = 0, size - 1

        if range_header and range_header.startswith("bytes="):
            raw = range_header[6:].split(",")[0]
            first, _, last = raw.partition("-")
            try:
                if first:
                    start = int(first)
                    end = int(last) if last else size - 1
                elif last:
                    start = max(0, size - int(last))
            except ValueError:
                start, end = 0, size - 1
            end = min(end, size - 1)
            if start > end:
                self._send(416, b"", "text/plain", {"Content-Range": f"bytes */{size}"})
                return

        length = end - start + 1
        status = 206 if range_header else 200
        headers = {
            "Accept-Ranges": "bytes",
            "Content-Disposition": f'inline; filename="{path.name}"',
        }
        if status == 206:
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"

        self.send_response(status)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(length))
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        if self.command == "HEAD":
            return
        with open(path, "rb") as handle:
            handle.seek(start)
            remaining = length
            while remaining > 0:
                chunk = handle.read(min(256 * 1024, remaining))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    return
                remaining -= len(chunk)

    # -- routing --------------------------------------------------------

    def do_GET(self):  # noqa: N802
        url = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(url.path)
        query = urllib.parse.parse_qs(url.query)

        # Routes a phone on the same network is allowed to reach.
        if path.startswith("/d/"):
            return self.share_page(path[3:])
        if path.startswith("/f/"):
            return self.share_file(path[3:])

        if not self.is_local:
            return self.fail("This app only answers on this computer.", 403)

        if path in ("/", "/index.html"):
            return self.serve_asset(WEB_DIR() / "index.html")
        if path.startswith("/static/"):
            name = Path(path[len("/static/"):]).name
            return self.serve_asset(WEB_DIR() / name)
        if path == "/api/state":
            return self.state()
        if path == "/api/groups":
            status = (query.get("status") or [None])[0]
            return self.json({"groups": library.grouped_by_video(status)})
        if path.startswith("/api/jobs/"):
            since = int((query.get("since") or ["0"])[0] or 0)
            return self.job_status(path[len("/api/jobs/"):], since)
        if path.startswith("/media/"):
            return self.clip_media(path[len("/media/"):])
        if path.startswith("/api/clips/") and path.endswith("/share"):
            return self.clip_share(path[len("/api/clips/"):-len("/share")])
        if path == "/api/tiktok/login-url":
            return self.tiktok_login_url()
        if path == "/callback/" or path == "/callback":
            return self.tiktok_callback(query)

        return self.fail("Not found", 404)

    def do_POST(self):  # noqa: N802
        if not self.is_local:
            return self.fail("This app only answers on this computer.", 403)

        path = urllib.parse.urlparse(self.path).path

        if path == "/api/settings":
            return self.update_settings()
        if path == "/api/cut":
            return self.start_cut()
        if path == "/api/open-folder":
            return self.open_folder()
        if path == "/api/open-url":
            return self.open_url()
        if path == "/api/tiktok/disconnect":
            tiktok_auth.disconnect()
            return self.json({"ok": True})
        if path.startswith("/api/clips/"):
            rest = path[len("/api/clips/"):]
            clip_id, _, action = rest.partition("/")
            return self.clip_action(clip_id, action)

        return self.fail("Not found", 404)

    def do_DELETE(self):  # noqa: N802
        if not self.is_local:
            return self.fail("This app only answers on this computer.", 403)
        url = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(url.query)
        if url.path.startswith("/api/clips/"):
            clip_id = url.path[len("/api/clips/"):]
            delete_file = (query.get("file") or ["0"])[0] == "1"
            if not library.remove(clip_id, delete_file=delete_file):
                return self.fail("That clip is no longer in the library.", 404)
            return self.json({"ok": True})
        return self.fail("Not found", 404)

    # -- handlers -------------------------------------------------------

    def state(self) -> None:
        clips = library.all_clips()
        self.json(
            {
                "version": __version__,
                "settings": _public_settings(),
                "ffmpeg": has_ffmpeg(),
                "tiktok": {
                    "connected": tiktok_auth.is_connected(),
                    "redirect_uri": tiktok_auth.redirect_uri(),
                    "scopes": tiktok_auth.SCOPES,
                },
                "counts": {
                    "pending": sum(1 for c in clips if c.get("status") == library.PENDING),
                    "published": sum(1 for c in clips if c.get("status") != library.PENDING),
                },
            }
        )

    def update_settings(self) -> None:
        patch = self.body_json()
        # A blank secret means "leave it alone", not "erase it" — the UI never
        # receives the stored value, so it cannot send it back.
        for field in SECRET_FIELDS:
            if field in patch and not str(patch[field]).strip():
                patch.pop(field)
        save_settings(patch)
        self.json({"settings": _public_settings()})

    def start_cut(self) -> None:
        body = self.body_json()
        url = (body.get("url") or "").strip()
        if not url:
            return self.fail("Paste a YouTube link first.")
        if not has_ffmpeg():
            return self.fail("ffmpeg is missing — reinstall the app, or install ffmpeg if running from source.")

        options = {
            "parts": body.get("parts"),
            "part_duration": body.get("part_duration"),
            "overlap": body.get("overlap"),
            "mode": body.get("mode"),
            "hook_text": body.get("hook_text"),
            "burn_part_label": body.get("burn_part_label", True),
        }
        job = jobs.start("cut", lambda j: pipeline.create_clips(j, url, options))
        self.json({"job_id": job.id})

    def job_status(self, job_id: str, since: int) -> None:
        job = jobs.get(job_id)
        if job is None:
            return self.fail("That job is no longer around — the app was probably restarted.", 404)
        self.json(job.snapshot(since))

    def clip_media(self, clip_id: str) -> None:
        clip = library.get(clip_id)
        if not clip or not clip.get("path"):
            return self.fail("Unknown clip.", 404)
        self.serve_video(Path(clip["path"]))

    def clip_share(self, clip_id: str) -> None:
        clip = library.get(clip_id)
        if not clip:
            return self.fail("Unknown clip.", 404)
        url = f"http://{local_ip()}:{SERVER_PORT}/d/{clip['download_token']}"
        self.json({"url": url, "qr": _qr_data_uri(url)})

    def clip_action(self, clip_id: str, action: str) -> None:
        clip = library.get(clip_id)
        if not clip:
            return self.fail("Unknown clip.", 404)
        body = self.body_json()

        if action == "caption":
            caption = (body.get("caption") or "").strip()
            updated = library.update(clip_id, caption=caption)
            if updated and updated.get("path"):
                library.write_captions_file(Path(updated["path"]).parent)
            return self.json({"clip": updated})

        if action == "manual":
            caption = (body.get("caption") or "").strip()
            if caption:
                library.update(clip_id, caption=caption)
            return self.json({"clip": library.mark_published(clip_id, manual=True)})

        if action == "pending":
            return self.json({"clip": library.mark_pending(clip_id)})

        if action == "publish":
            return self.start_publish(clip, body)

        return self.fail("Unknown action.", 404)

    def start_publish(self, clip: dict, body: dict) -> None:
        if not tiktok_auth.is_connected():
            return self.fail("Connect your TikTok account in Settings first.")
        caption = (body.get("caption") or clip.get("caption") or "").strip()
        if not caption:
            return self.fail("Write a caption before posting.")
        library.update(clip["id"], caption=caption)
        path = Path(clip["path"])
        if not path.exists():
            return self.fail("That clip file is missing from disk.")

        def work(job):
            job.update(phase="upload", detail="Uploading to TikTok…", percent=1)
            token = tiktok_auth.get_valid_access_token()
            publish_id = tiktok_api.publish_video(
                token,
                path,
                caption,
                privacy_level=tiktok_api.PRIVATE,
                on_progress=lambda p: job.update(percent=min(90.0, p * 0.9)),
            )
            job.update(phase="processing", detail="TikTok is processing the video…", percent=92)
            status = tiktok_api.wait_for_publish(
                token, publish_id, on_tick=lambda s: job.say(f"Status: {s}")
            )
            state = status.get("status")
            if state != "PUBLISH_COMPLETE":
                raise RuntimeError(f"TikTok reported: {state or 'unknown status'}")
            library.mark_published(
                clip["id"], manual=False, publish_id=publish_id, privacy=tiktok_api.PRIVATE
            )
            job.update(percent=100, detail="Posted")
            return {"clip_id": clip["id"], "publish_id": publish_id}

        job = jobs.start("publish", work)
        self.json({"job_id": job.id})

    def open_folder(self) -> None:
        body = self.body_json()
        raw = (body.get("path") or "").strip()
        target = Path(raw) if raw else clips_dir()
        root = clips_dir().resolve()
        try:
            target.resolve().relative_to(root)
        except ValueError:
            target = root
        if not target.exists():
            target = root
        try:
            open_in_file_manager(target)
        except OSError as exc:
            return self.fail(f"Could not open the folder: {exc}")
        self.json({"ok": True})

    def open_url(self) -> None:
        """Open an external page in the real browser.

        The window the app runs in is not a browser and handles target=_blank
        poorly, so links go through here. Only the handful of sites the setup
        walkthrough points at are allowed, so a stray link can never turn this
        into a way to launch arbitrary URLs."""
        raw = (self.body_json().get("url") or "").strip()
        parsed = urllib.parse.urlparse(raw)
        if parsed.scheme != "https" or parsed.hostname not in ALLOWED_EXTERNAL_HOSTS:
            return self.fail("That link isn't one this app opens.")
        webbrowser.open(raw)
        self.json({"ok": True})

    # -- TikTok sign-in --------------------------------------------------

    def tiktok_login_url(self) -> None:
        try:
            self.json({"url": tiktok_auth.start_login()})
        except tiktok_auth.AuthError as exc:
            self.fail(str(exc))

    def tiktok_callback(self, query: dict) -> None:
        error = (query.get("error") or [None])[0]
        if error:
            description = (query.get("error_description") or [""])[0]
            return self.html(_callback_page(False, description or error))
        code = (query.get("code") or [None])[0]
        state = (query.get("state") or [""])[0]
        if not code:
            return self.html(_callback_page(False, "TikTok did not send an authorization code."))
        try:
            tiktok_auth.complete_login(code, state)
        except tiktok_auth.AuthError as exc:
            return self.html(_callback_page(False, str(exc)))
        self.html(_callback_page(True, ""))

    # -- sharing to a phone ----------------------------------------------

    def share_page(self, token: str) -> None:
        clip = library.by_download_token(token)
        if not clip:
            return self.html(_share_not_found(), 404)
        self.html(_share_page(clip, token))

    def share_file(self, token: str) -> None:
        clip = library.by_download_token(token)
        if not clip or not clip.get("path"):
            return self.fail("Unknown link.", 404)
        self.serve_video(Path(clip["path"]))


def _callback_page(success: bool, message: str) -> str:
    title = "TikTok account connected" if success else "Sign-in failed"
    body = (
        "You can close this tab and go back to ClipClapCloup."
        if success
        else f"{message}<br><br>Close this tab and try again from Settings."
    )
    tint = "#16a34a" if success else "#dc2626"
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>{title}</title><style>
body{{font-family:system-ui,-apple-system,'Segoe UI',sans-serif;background:#faf9f7;color:#1f1d1a;
display:grid;place-items:center;min-height:100vh;margin:0;padding:24px;text-align:center}}
.card{{background:#fff;border:1px solid #e8e4dd;border-radius:14px;padding:32px 36px;max-width:420px}}
h1{{font-size:1.25rem;margin:0 0 10px;color:{tint}}}p{{margin:0;line-height:1.55;color:#6b6356}}
</style></head><body><div class="card"><h1>{title}</h1><p>{body}</p></div></body></html>"""


def _share_not_found() -> str:
    return """<!doctype html><html><head><meta charset="utf-8"><title>Link expired</title>
<meta name="viewport" content="width=device-width,initial-scale=1"><style>
body{font-family:system-ui,-apple-system,sans-serif;background:#0e0d0c;color:#f5f2ec;display:grid;
place-items:center;min-height:100vh;margin:0;padding:24px;text-align:center}</style></head>
<body><div><h1>Link expired</h1><p>This clip is no longer shared. Scan the QR code again.</p></div></body></html>"""


def _share_page(clip: dict, token: str) -> str:
    import html

    title = html.escape(clip.get("video_title") or "Clip")
    caption = html.escape(clip.get("caption") or "")
    part = ""
    if clip.get("parts_total", 1) > 1:
        part = f"Part {clip.get('part_index')} of {clip.get('parts_total')}"
    filename = html.escape(Path(clip["path"]).name) if clip.get("path") else "clip.mp4"

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><style>
:root{{color-scheme:dark}}
*{{box-sizing:border-box}}
body{{margin:0;padding:20px 16px 48px;background:#0e0d0c;color:#f5f2ec;
font-family:system-ui,-apple-system,'Segoe UI',sans-serif;max-width:560px;margin-inline:auto}}
h1{{font-size:1.1rem;margin:0 0 4px;line-height:1.35}}
.part{{color:#a8a096;font-size:.85rem;margin:0 0 16px}}
video{{width:100%;border-radius:14px;background:#000;display:block;margin-bottom:18px}}
.btn{{display:block;text-align:center;background:#d0491f;color:#fff;text-decoration:none;
padding:15px;border-radius:12px;font-weight:600;font-size:1rem;margin-bottom:22px}}
.caption{{background:#1a1816;border:1px solid #2c2823;border-radius:12px;padding:14px}}
.caption h2{{font-size:.72rem;text-transform:uppercase;letter-spacing:.08em;color:#a8a096;margin:0 0 8px}}
.caption p{{margin:0 0 12px;white-space:pre-wrap;line-height:1.5;font-size:.95rem}}
button{{width:100%;background:none;border:1px solid #3a352e;color:#f5f2ec;padding:11px;
border-radius:10px;font-size:.9rem;cursor:pointer;font-family:inherit}}
button:active{{background:#221f1c}}
</style></head><body>
<h1>{title}</h1><p class="part">{part}</p>
<video src="/f/{token}" controls playsinline preload="metadata"></video>
<a class="btn" href="/f/{token}" download="{filename}">Save the video</a>
<div class="caption"><h2>Caption</h2><p id="cap">{caption}</p>
<button onclick="navigator.clipboard.writeText(document.getElementById('cap').innerText).then(()=>{{
const b=document.querySelector('button');b.textContent='Copied';setTimeout(()=>b.textContent='Copy caption',1600)}})">
Copy caption</button></div>
<script>
// iOS Safari ignores the download attribute on cross-origin-ish links; long-press
// to save works there, so the inline player above is the reliable path.
</script>
</body></html>"""


def serve(port: int = SERVER_PORT) -> ThreadingHTTPServer:
    """Start the server on a background thread and return it."""
    import threading

    mimetypes.add_type("text/javascript", ".js")
    httpd = ThreadingHTTPServer(("0.0.0.0", port), Handler)  # noqa: S104 - see module docstring
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True, name="http").start()
    return httpd
