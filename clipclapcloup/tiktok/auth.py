"""Signing in to TikTok from a desktop app.

TikTok's desktop flow is the OAuth authorization code flow with PKCE, and it
allows a loopback redirect URI — which is what makes this pleasant: the app's
own local server catches the redirect, so the user never copies a code around.
Two details differ from textbook PKCE and will bite you if you assume:

  * the code challenge is the **hex** digest of SHA256(verifier), not the
    usual base64url;
  * the token exchange still wants the client secret **alongside** the
    verifier, so this is not a public-client flow.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import time
from urllib.parse import urlencode

import requests

from ..config import SERVER_PORT, TOKENS_PATH, _restrict_permissions, load_settings

AUTHORIZE_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
USER_INFO_URL = "https://open.tiktokapis.com/v2/user/info/"

SCOPES = "user.info.basic,video.upload,video.publish"

# Kept in memory between opening the browser and TikTok calling us back.
_pending: dict = {}


class AuthError(RuntimeError):
    pass


def redirect_uri() -> str:
    """The exact string the user must register in the TikTok developer portal."""
    return f"http://127.0.0.1:{SERVER_PORT}/callback/"


def _make_verifier() -> str:
    # unreserved characters only, 43-128 long
    return secrets.token_urlsafe(64)[:100].replace("-", "_")


def _challenge(verifier: str) -> str:
    return hashlib.sha256(verifier.encode("utf-8")).hexdigest()


def start_login() -> str:
    """Return the URL to open in the browser, arming the pending exchange."""
    settings = load_settings()
    client_key = (settings.get("tiktok_client_key") or "").strip()
    if not client_key:
        raise AuthError("Add your TikTok client key in Settings first.")

    verifier = _make_verifier()
    state = secrets.token_urlsafe(16)
    _pending.clear()
    _pending.update({"verifier": verifier, "state": state, "started_at": time.time()})

    query = urlencode(
        {
            "client_key": client_key,
            "response_type": "code",
            "scope": SCOPES,
            "redirect_uri": redirect_uri(),
            "state": state,
            "code_challenge": _challenge(verifier),
            "code_challenge_method": "S256",
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


def complete_login(code: str, state: str) -> dict:
    """Exchange the callback code for tokens and store them."""
    if not _pending:
        raise AuthError("No sign-in is in progress. Start again from Settings.")
    if state != _pending.get("state"):
        raise AuthError("That sign-in response didn't match this app's request.")

    settings = load_settings()
    payload = {
        "client_key": (settings.get("tiktok_client_key") or "").strip(),
        "client_secret": (settings.get("tiktok_client_secret") or "").strip(),
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri(),
        "code_verifier": _pending["verifier"],
    }
    tokens = _post_token(payload)
    _pending.clear()
    return save_tokens(tokens)


def _post_token(payload: dict) -> dict:
    response = requests.post(
        TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data=payload,
        timeout=30,
    )
    try:
        data = response.json()
    except ValueError as exc:
        raise AuthError(f"TikTok returned an unreadable response ({response.status_code}).") from exc

    if "access_token" not in data:
        detail = data.get("error_description") or data.get("error") or data
        raise AuthError(f"TikTok refused the sign-in: {detail}")
    return data


def save_tokens(tokens: dict) -> dict:
    stored = dict(tokens)
    stored["obtained_at"] = int(time.time())
    path = TOKENS_PATH()
    path.write_text(json.dumps(stored, indent=2), encoding="utf-8")
    _restrict_permissions(path)
    return stored


def load_tokens() -> dict | None:
    path = TOKENS_PATH()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def disconnect() -> None:
    TOKENS_PATH().unlink(missing_ok=True)


def is_connected() -> bool:
    return load_tokens() is not None


def get_valid_access_token() -> str:
    """A live access token, refreshed first if it is close to expiring."""
    tokens = load_tokens()
    if not tokens:
        raise AuthError("Connect your TikTok account first.")

    age = int(time.time()) - int(tokens.get("obtained_at", 0))
    if age > int(tokens.get("expires_in", 0)) - 120:
        settings = load_settings()
        refreshed = _post_token(
            {
                "client_key": (settings.get("tiktok_client_key") or "").strip(),
                "client_secret": (settings.get("tiktok_client_secret") or "").strip(),
                "grant_type": "refresh_token",
                "refresh_token": tokens.get("refresh_token", ""),
            }
        )
        tokens = save_tokens(refreshed)
    return tokens["access_token"]


def account_name() -> str | None:
    """Display name of the connected account, for the settings screen. Best
    effort — a failure here says nothing about whether posting works."""
    try:
        token = get_valid_access_token()
        response = requests.get(
            USER_INFO_URL,
            params={"fields": "open_id,display_name"},
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        if response.status_code != 200:
            return None
        return response.json().get("data", {}).get("user", {}).get("display_name")
    except Exception:  # noqa: BLE001
        return None
