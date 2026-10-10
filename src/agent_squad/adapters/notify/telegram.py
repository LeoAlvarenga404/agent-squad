"""Telegram notify adapter (stdlib only).

Notify adapter contract: a module with `send(text: str) -> bool` that delivers a plain-text message to the operator
and never raises (False = not delivered or not configured). Callers fire and forget.

This one posts through the Bot API with TELEGRAM_BOT_TOKEN to TELEGRAM_CHAT_ID (the same bot `squad bot` runs).
Plain text, no markup, so URLs stay clickable.
"""
import json
import os
import urllib.request

from ...config import load_dotenv

MAX_TEXT = 3500  # Telegram accepts 4096 per message


def call(token: str, method: str, **params):
    """One Bot API request; raises on network errors or a not-ok reply."""
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/{method}", data=json.dumps(params).encode(),
                                 headers={"Content-Type": "application/json"})
    # getUpdates holds the connection for `timeout` s; the slack keeps urlopen from giving up first
    with urllib.request.urlopen(req, timeout=params.get("timeout", 0) + 15) as r:
        body = json.load(r)
    if not body.get("ok"):
        raise RuntimeError(f"Telegram {method}: {body.get('description')}")
    return body["result"]


def send(text: str) -> bool:
    load_dotenv()
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat):
        return False
    try:
        call(token, "sendMessage", chat_id=chat, text=text[:MAX_TEXT])
        return True
    except Exception:
        return False
