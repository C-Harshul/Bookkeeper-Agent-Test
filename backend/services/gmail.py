"""Gmail OAuth (authorization code) and latest-message fetch for the inspect-email workflow."""

from __future__ import annotations

import base64
import os
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List

import requests

GMAIL_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GMAIL_TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1"
GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"

_TOKEN_CACHE: Dict[str, str] = {}


def _get_optional_env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def gmail_oauth_configured() -> bool:
    return bool(_get_optional_env("GMAIL_CLIENT_ID") and _get_optional_env("GMAIL_CLIENT_SECRET"))


def gmail_tokens_present() -> bool:
    refresh = _get_optional_env("GMAIL_REFRESH_TOKEN") or _TOKEN_CACHE.get("refresh_token", "")
    access = _get_optional_env("GMAIL_ACCESS_TOKEN") or _TOKEN_CACHE.get("access_token", "")
    return bool(refresh or access)


def gmail_public_oauth_urls() -> Dict[str, str]:
    """Non-secret values to paste into Google Cloud Console (fixes redirect_uri_mismatch)."""
    redirect = _get_optional_env("GMAIL_REDIRECT_URI")
    if not redirect:
        return {"redirectUri": "", "javascriptOrigin": ""}
    parsed = urllib.parse.urlparse(redirect)
    if parsed.scheme and parsed.netloc:
        origin = f"{parsed.scheme}://{parsed.netloc}"
    else:
        origin = ""
    return {"redirectUri": redirect, "javascriptOrigin": origin}


def _persist_env_values(values: Dict[str, str]) -> None:
    env_path = Path(".env")
    if not env_path.exists():
        return
    lines = env_path.read_text().splitlines()
    keys = set(values.keys())
    replaced: Dict[str, bool] = {key: False for key in keys}
    output: List[str] = []
    for line in lines:
        stripped = line.strip()
        if "=" not in stripped or stripped.startswith("#"):
            output.append(line)
            continue
        key = stripped.split("=", 1)[0]
        if key in values:
            output.append(f'{key}="{values[key]}"')
            replaced[key] = True
        else:
            output.append(line)
    for key, done in replaced.items():
        if not done:
            output.append(f'{key}="{values[key]}"')
    env_path.write_text("\n".join(output) + "\n")


def build_gmail_authorize_url(*, login_hint: str = "") -> str:
    client_id = _get_optional_env("GMAIL_CLIENT_ID")
    redirect_uri = _get_optional_env("GMAIL_REDIRECT_URI")
    if not client_id or not redirect_uri:
        raise ValueError("GMAIL_CLIENT_ID and GMAIL_REDIRECT_URI must be set for Gmail OAuth.")

    # select_account: show Google account chooser when multiple accounts exist.
    # consent: helps ensure a refresh_token on first connect.
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": GMAIL_READONLY_SCOPE,
        "access_type": "offline",
        "prompt": "select_account consent",
        "include_granted_scopes": "true",
    }
    hint = (login_hint or "").strip()
    if hint:
        params["login_hint"] = hint
    return f"{GMAIL_AUTH_URL}?{urllib.parse.urlencode(params)}"


def exchange_gmail_authorization_code(code: str, redirect_uri: str) -> Dict[str, Any]:
    client_id = _get_optional_env("GMAIL_CLIENT_ID")
    client_secret = _get_optional_env("GMAIL_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise ValueError("GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET must be set.")

    response = requests.post(
        GMAIL_TOKEN_URL,
        data={
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
        headers={"Accept": "application/json"},
        timeout=30,
    )
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        detail = response.text[:2000]
        raise requests.HTTPError(f"{exc}. Google token response: {detail}", response=response) from exc

    data = response.json()
    access_token = data.get("access_token")
    refresh_token = data.get("refresh_token")
    if access_token:
        _TOKEN_CACHE["access_token"] = access_token
        os.environ["GMAIL_ACCESS_TOKEN"] = access_token
    if refresh_token:
        _TOKEN_CACHE["refresh_token"] = refresh_token
        os.environ["GMAIL_REFRESH_TOKEN"] = refresh_token

    persist: Dict[str, str] = {}
    if access_token:
        persist["GMAIL_ACCESS_TOKEN"] = access_token
    if refresh_token:
        persist["GMAIL_REFRESH_TOKEN"] = refresh_token
    if persist:
        _persist_env_values(persist)

    return data


def _refresh_gmail_access_token() -> str:
    client_id = _get_optional_env("GMAIL_CLIENT_ID")
    client_secret = _get_optional_env("GMAIL_CLIENT_SECRET")
    refresh_token = _TOKEN_CACHE.get("refresh_token") or _get_optional_env("GMAIL_REFRESH_TOKEN")
    if not refresh_token:
        raise ValueError("No GMAIL_REFRESH_TOKEN available. Complete Gmail OAuth again.")
    if not client_id or not client_secret:
        raise ValueError("GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET must be set.")

    response = requests.post(
        GMAIL_TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        headers={"Accept": "application/json"},
        timeout=30,
    )
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        detail = response.text[:2000]
        raise requests.HTTPError(f"{exc}. Google token response: {detail}", response=response) from exc

    data = response.json()
    access_token = data.get("access_token")
    if not access_token:
        raise ValueError("Gmail token refresh succeeded but access_token was missing.")
    _TOKEN_CACHE["access_token"] = access_token
    os.environ["GMAIL_ACCESS_TOKEN"] = access_token
    _persist_env_values({"GMAIL_ACCESS_TOKEN": access_token})
    return access_token


def _gmail_access_token() -> str:
    return _TOKEN_CACHE.get("access_token") or _get_optional_env("GMAIL_ACCESS_TOKEN") or _refresh_gmail_access_token()


def _decode_b64url(data: str) -> str:
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")


def _walk_parts(payload: Dict[str, Any], text_out: List[str], html_out: List[str]) -> None:
    mime = (payload.get("mimeType") or "").lower()
    body = payload.get("body") or {}
    raw = body.get("data")
    if raw:
        try:
            content = _decode_b64url(raw)
        except Exception:
            content = ""
        if mime == "text/plain" and content:
            text_out.append(content)
        elif mime == "text/html" and content:
            html_out.append(content)
        elif not mime.startswith("multipart/") and content and not text_out and not html_out:
            text_out.append(content)

    for part in payload.get("parts") or []:
        _walk_parts(part, text_out, html_out)


def _message_to_email_dict(message: Dict[str, Any]) -> Dict[str, Any]:
    payload = message.get("payload") or {}
    headers_list = payload.get("headers") or []
    headers = {h.get("name", "").lower(): h.get("value", "") for h in headers_list}

    text_parts: List[str] = []
    html_parts: List[str] = []
    _walk_parts(payload, text_parts, html_parts)

    internal_date = message.get("internalDate")
    date_str = headers.get("date") or ""
    if not date_str and internal_date:
        try:
            from datetime import datetime, timezone

            ms = int(internal_date) / 1000.0
            date_str = datetime.fromtimestamp(ms, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        except (ValueError, OSError):
            date_str = str(internal_date)

    return {
        "subject": headers.get("subject", ""),
        "from": headers.get("from", ""),
        "to": headers.get("to", ""),
        "date": date_str,
        "html": "\n".join(html_parts).strip(),
        "text": "\n".join(text_parts).strip(),
        "gmailMessageId": message.get("id"),
        "threadId": message.get("threadId"),
    }


def fetch_latest_inbox_message_as_email() -> Dict[str, Any]:
    """Return the newest message in the inbox as the same shape as ``get_sample_email()``."""
    token = _gmail_access_token()
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    list_resp = requests.get(
        f"{GMAIL_API_BASE}/users/me/messages",
        headers=headers,
        params={"maxResults": 1, "labelIds": "INBOX"},
        timeout=30,
    )
    if list_resp.status_code == 401:
        token = _refresh_gmail_access_token()
        headers["Authorization"] = f"Bearer {token}"
        list_resp = requests.get(
            f"{GMAIL_API_BASE}/users/me/messages",
            headers=headers,
            params={"maxResults": 1, "labelIds": "INBOX"},
            timeout=30,
        )

    try:
        list_resp.raise_for_status()
    except requests.HTTPError as exc:
        detail = list_resp.text[:2000]
        raise requests.HTTPError(f"{exc}. Gmail list: {detail}", response=list_resp) from exc

    msg_ids = (list_resp.json() or {}).get("messages") or []
    if not msg_ids:
        raise ValueError("No messages found in Gmail INBOX.")

    msg_id = msg_ids[0]["id"]
    get_resp = requests.get(
        f"{GMAIL_API_BASE}/users/me/messages/{msg_id}",
        headers=headers,
        params={"format": "full"},
        timeout=30,
    )
    if get_resp.status_code == 401:
        token = _refresh_gmail_access_token()
        headers["Authorization"] = f"Bearer {token}"
        get_resp = requests.get(
            f"{GMAIL_API_BASE}/users/me/messages/{msg_id}",
            headers=headers,
            params={"format": "full"},
            timeout=30,
        )

    try:
        get_resp.raise_for_status()
    except requests.HTTPError as exc:
        detail = get_resp.text[:2000]
        raise requests.HTTPError(f"{exc}. Gmail get message: {detail}", response=get_resp) from exc

    return _message_to_email_dict(get_resp.json())


def validate_gmail_auth() -> Dict[str, Any]:
    """Light validation: can list profile or one message."""
    token = _gmail_access_token()
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    r = requests.get(f"{GMAIL_API_BASE}/users/me/profile", headers=headers, timeout=30)
    if r.status_code == 401:
        token = _refresh_gmail_access_token()
        headers["Authorization"] = f"Bearer {token}"
        r = requests.get(f"{GMAIL_API_BASE}/users/me/profile", headers=headers, timeout=30)
    r.raise_for_status()
    return r.json()
