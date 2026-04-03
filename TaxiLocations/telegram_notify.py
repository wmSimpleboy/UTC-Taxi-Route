from __future__ import annotations

import os
from typing import Iterable

import requests


def _get_bot_token() -> str:
    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set in environment/.env.")
    return token


def _parse_chat_ids(raw: str) -> list[int]:
    ids: list[int] = []
    for part in (raw or "").split(","):
        p = part.strip()
        if not p:
            continue
        ids.append(int(p))
    return ids


def get_user_chat_ids() -> list[int]:
    return _parse_chat_ids(os.environ.get("TELEGRAM_CHAT_IDS") or "")


def get_superuser_chat_ids() -> list[int]:
    return _parse_chat_ids(os.environ.get("TELEGRAM_SUPERUSER_CHAT_IDS") or "")


def get_all_chat_ids() -> list[int]:
    return get_user_chat_ids() + get_superuser_chat_ids()


def _send_message(text: str, chat_ids: Iterable[int]) -> None:
    token = _get_bot_token()
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    for chat_id in chat_ids:
        resp = requests.post(
            url,
            json={"chat_id": chat_id, "text": text},
            timeout=15,
        )
        resp.raise_for_status()


def send_message_to_chats(*, text: str, chat_ids: Iterable[int] | None = None) -> None:
    """Send a plain Telegram message to all configured chat ids (users + superusers)."""
    ids = list(chat_ids) if chat_ids is not None else get_all_chat_ids()
    if not ids:
        raise RuntimeError("No TELEGRAM_CHAT_IDS or TELEGRAM_SUPERUSER_CHAT_IDS configured.")
    _send_message(text, ids)


def send_message_to_users(*, text: str) -> None:
    """Send a message only to regular user chat ids."""
    ids = get_user_chat_ids()
    if ids:
        _send_message(text, ids)


def send_message_to_superusers(*, text: str) -> None:
    """Send a message only to superuser chat ids."""
    ids = get_superuser_chat_ids()
    if ids:
        _send_message(text, ids)
