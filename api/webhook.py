"""Точка входа на Vercel: Telegram шлёт сюда апдейты.

Функция просыпается на каждый апдейт, обрабатывает его и засыпает — поэтому в ней
нет ни фонового цикла, ни памяти между вызовами: состояние диалога живёт в кнопках,
данные — в хранилище.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aiogram.types import Update  # noqa: E402

from bot.app import build_bot, get_dispatcher  # noqa: E402
from bot.config import get_config  # noqa: E402
from bot.storage import create_store  # noqa: E402


async def process(payload: dict) -> None:
    bot = build_bot()
    store = create_store()
    try:
        await get_dispatcher().feed_update(
            bot,
            Update.model_validate(payload, context={"bot": bot}),
            config=get_config(),
            store=store,
        )
    finally:
        await store.close()
        await bot.session.close()


class handler(BaseHTTPRequestHandler):
    def _reply(self, code: int, body: str = "ok") -> None:
        payload = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # проверка «жив ли эндпоинт» из браузера
        self._reply(200, "bot webhook is up")

    def do_POST(self) -> None:
        secret = os.getenv("WEBHOOK_SECRET")
        if secret and self.headers.get("X-Telegram-Bot-Api-Secret-Token") != secret:
            self._reply(403, "forbidden")
            return

        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            self._reply(400, "bad json")
            return

        try:
            asyncio.run(process(payload))
        except Exception:  # Telegram не должен ретраить апдейт бесконечно
            import traceback

            traceback.print_exc()
        self._reply(200)
