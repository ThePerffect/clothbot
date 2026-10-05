"""Минимальный асинхронный клиент Telegram Bot API на httpx."""
from __future__ import annotations

import json
import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)


class TelegramError(Exception):
    pass


class Telegram:
    def __init__(self, token: str, client: httpx.AsyncClient | None = None):
        self.token = token
        self.base = f"https://api.telegram.org/bot{token}/"
        self.file_base = f"https://api.telegram.org/file/bot{token}/"
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(60, connect=10))

    async def call(self, method: str, files: dict | None = None, **params: Any) -> Any:
        data = {k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v)
                for k, v in params.items() if v is not None}
        if files:
            r = await self._client.post(self.base + method, data=data, files=files)
        else:
            r = await self._client.post(self.base + method, json={
                k: v for k, v in params.items() if v is not None})
        payload = r.json()
        if not payload.get("ok"):
            raise TelegramError(f"{method}: {payload.get('description')}")
        return payload["result"]

    async def download(self, file_id: str) -> bytes:
        info = await self.call("getFile", file_id=file_id)
        r = await self._client.get(self.file_base + info["file_path"])
        r.raise_for_status()
        return r.content

    # Удобные обёртки
    async def send(self, chat_id: int, text: str, markup: dict | None = None, **kw) -> dict:
        return await self.call("sendMessage", chat_id=chat_id, text=text, parse_mode="HTML",
                               reply_markup=markup, **kw)

    async def send_photo(self, chat_id: int, photo: bytes, caption: str = "",
                         markup: dict | None = None, filename: str = "photo.jpg") -> dict:
        return await self.call("sendPhoto", files={"photo": (filename, photo)},
                               chat_id=chat_id, caption=caption, parse_mode="HTML",
                               reply_markup=markup)

    async def edit_caption(self, chat_id: int, message_id: int, caption: str,
                           markup: dict | None = None) -> None:
        try:
            await self.call("editMessageCaption", chat_id=chat_id, message_id=message_id,
                            caption=caption, parse_mode="HTML", reply_markup=markup)
        except TelegramError as e:
            if "not modified" not in str(e):
                raise

    async def edit_text(self, chat_id: int, message_id: int, text: str,
                        markup: dict | None = None) -> None:
        try:
            await self.call("editMessageText", chat_id=chat_id, message_id=message_id,
                            text=text, parse_mode="HTML", reply_markup=markup)
        except TelegramError as e:
            if "not modified" not in str(e):
                raise

    async def answer(self, callback_id: str, text: str | None = None) -> None:
        try:
            await self.call("answerCallbackQuery", callback_query_id=callback_id, text=text)
        except TelegramError:
            log.debug("answerCallbackQuery failed", exc_info=True)

    async def updates(self, offset: int | None, timeout: int = 50) -> list[dict]:
        return await self.call("getUpdates", offset=offset, timeout=timeout,
                               allowed_updates=["message", "callback_query"])
