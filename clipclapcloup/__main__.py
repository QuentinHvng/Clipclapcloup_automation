"""Starting the app: local server first, then the window on top of it.

A windowed build has no console, so anything that goes wrong would otherwise
vanish without a trace. Every step writes to startup.log next to the settings,
and a failure the user can't see becomes a message box rather than a process
that disappears.
"""
from __future__ import annotations

import ctypes
import socket
import sys
import time
import traceback
import webbrowser
from datetime import datetime

from . import APP_NAME, __version__
from .config import SERVER_PORT, app_data_dir

URL = f"http://127.0.0.1:{SERVER_PORT}/"
LOG_LIMIT = 200_000


def log_path():
    return app_data_dir() / "startup.log"


def log(message: str) -> None:
    line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  {message}"
    try:
        path = log_path()
        if path.exists() and path.stat().st_size > LOG_LIMIT:
            path.unlink(missing_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        pass
    if sys.stdout is not None:
        try:
            print(line, flush=True)
        except (OSError, ValueError):
            pass


def alert(title: str, message: str) -> None:
    """Say something when there is neither a window nor a console to say it in."""
    log(f"ALERT — {title}: {message}")
    if sys.platform == "win32":
        try:
            ctypes.windll.user32.MessageBoxW(None, message, f"{APP_NAME} — {title}", 0x10)
        except Exception:  # noqa: BLE001 - an alert that fails must not mask the real error
            pass


def port_is_taken(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.4)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def wait_until_serving(timeout: float = 8.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if port_is_taken(SERVER_PORT):
            return True
        time.sleep(0.05)
    return False


def open_window() -> bool:
    """Open the native window. False means we could not, and said why."""
    try:
        import webview
    except Exception as exc:  # noqa: BLE001 - a missing runtime shows up in many shapes
        log(f"native window unavailable ({exc.__class__.__name__}: {exc})")
        log(traceback.format_exc())
        return False

    try:
        log(f"pywebview {getattr(webview, '__version__', 'unknown')} starting")
        webview.create_window(
            f"{APP_NAME} {__version__}",
            URL,
            width=1080,
            height=780,
            min_size=(860, 620),
            text_select=True,
        )
        webview.start()
        log("window closed by the user")
        return True
    except Exception as exc:  # noqa: BLE001
        log(f"the window failed to start ({exc.__class__.__name__}: {exc})")
        log(traceback.format_exc())
        return False


def stay_alive() -> None:
    log("running in browser mode — close this from the tray or task manager")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


def run() -> int:
    log(f"--- {APP_NAME} {__version__} starting (frozen={getattr(sys, 'frozen', False)}) ---")
    log(f"data folder: {app_data_dir()}")

    if port_is_taken(SERVER_PORT):
        log("port already in use — opening the instance that is already running")
        webbrowser.open(URL)
        return 0

    from .server import serve  # imported here so an import error lands in the log

    server = serve(SERVER_PORT)
    host = server.server_address[0]
    log(f"server listening on {host}:{SERVER_PORT}")
    if host == "127.0.0.1":
        log("bound to loopback only — 'send to phone' will not reach other devices")

    if not wait_until_serving():
        alert(
            "Could not start",
            "The app's local service did not come up.\n\n"
            f"Details were written to:\n{log_path()}",
        )
        return 1

    if not open_window():
        log("falling back to the default browser")
        webbrowser.open(URL)
        alert(
            "Opened in your browser",
            f"{APP_NAME} could not open its own window, so it opened in your browser instead.\n\n"
            f"It keeps running until you close it from the Task Manager.\n\n"
            f"Details were written to:\n{log_path()}",
        )
        stay_alive()

    log("--- exiting ---")
    return 0


def main() -> int:
    try:
        return run()
    except Exception as exc:  # noqa: BLE001 - last line of defence before a silent death
        log(traceback.format_exc())
        alert(
            "Crashed on startup",
            f"{exc.__class__.__name__}: {exc}\n\nThe full report was written to:\n{log_path()}",
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
