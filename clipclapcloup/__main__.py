"""Starting the app: local server first, then the window on top of it.

The interface is a web page, but the user never sees a browser — it runs in a
native window. If the windowing layer isn't available (running from source on
a bare machine, for instance), we fall back to the default browser rather
than failing to start.
"""
from __future__ import annotations

import socket
import sys
import time
import webbrowser

from . import APP_NAME, __version__
from .config import SERVER_PORT
from .server import serve

URL = f"http://127.0.0.1:{SERVER_PORT}/"


def port_is_taken(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.4)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def open_window() -> bool:
    """Open the native window. Returns False if that isn't possible here."""
    try:
        import webview
    except ImportError:
        return False

    webview.create_window(
        f"{APP_NAME} {__version__}",
        URL,
        width=1080,
        height=780,
        min_size=(860, 620),
        text_select=True,
    )
    webview.start()
    return True


def main() -> int:
    if port_is_taken(SERVER_PORT):
        # Almost always a second launch: point at the instance already running
        # instead of starting a rival server on a port TikTok doesn't know.
        print(f"{APP_NAME} is already running — opening the existing window.")
        webbrowser.open(URL)
        return 0

    serve(SERVER_PORT)
    # Give the socket a moment so the window never lands on a refused connection.
    for _ in range(40):
        if port_is_taken(SERVER_PORT):
            break
        time.sleep(0.05)

    if not open_window():
        print(f"{APP_NAME} is running at {URL} (Ctrl+C to stop).")
        webbrowser.open(URL)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
