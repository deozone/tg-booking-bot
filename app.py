"""Точка входа на Vercel.

Vercel ищет точку входа в файле со «стандартным» именем (`app.py`, `index.py`,
`main.py`…) и загружает из него переменную `app`. Поэтому оба адреса — вебхук
Telegram и утренний cron — обслуживает одно маленькое ASGI-приложение без фреймворка:

* `POST /api/webhook` — апдейты от Telegram;
* `GET  /api/cron`    — рассылка напоминаний, дёргается по расписанию из vercel.json;
* `GET  /api/webhook` — проверка «жив ли эндпоинт» из браузера.

Функция просыпается на каждый запрос и засыпает: ни фонового цикла, ни памяти между
вызовами здесь нет — состояние диалога живёт в кнопках, данные в хранилище.
"""

from __future__ import annotations

import json
import logging
import os
import traceback
from datetime import datetime

from aiogram.types import Update

from bot.app import build_bot, get_dispatcher
from bot.booking import day_bookings
from bot.config import get_config
from bot.storage import create_store
from bot import texts

log = logging.getLogger(__name__)


async def handle_update(payload: dict) -> None:
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


async def send_reminders() -> int:
    """Напоминание всем, кто записан на сегодня."""
    config = get_config()
    bot = build_bot()
    store = create_store()
    sent = 0
    try:
        today = datetime.now(config.tz).date()
        for booking in await day_bookings(store, today):
            try:
                await bot.send_message(booking.user_id, texts.reminder(config, booking))
                sent += 1
            except Exception:  # клиент мог заблокировать бота — не роняем рассылку
                log.warning("не доставлено напоминание %s", booking.id, exc_info=True)
    finally:
        await store.close()
        await bot.session.close()
    return sent


async def _read_body(receive) -> bytes:
    body = b""
    while True:
        message = await receive()
        body += message.get("body", b"")
        if not message.get("more_body"):
            return body


async def _respond(send, status: int, text: str) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"text/plain; charset=utf-8")],
        }
    )
    await send({"type": "http.response.body", "body": text.encode("utf-8")})


async def app(scope, receive, send) -> None:
    if scope["type"] != "http":
        return

    path = scope["path"].rstrip("/")
    method = scope["method"]
    headers = {key.decode().lower(): value.decode() for key, value in scope["headers"]}

    if path == "/api/cron":
        secret = os.getenv("CRON_SECRET")
        if secret and headers.get("authorization") != f"Bearer {secret}":
            await _respond(send, 403, "forbidden")
            return
        await _respond(send, 200, f"reminders sent: {await send_reminders()}")
        return

    if method != "POST":
        await _respond(send, 200, "bot webhook is up")
        return

    secret = os.getenv("WEBHOOK_SECRET")
    if secret and headers.get("x-telegram-bot-api-secret-token") != secret:
        await _respond(send, 403, "forbidden")
        return

    try:
        payload = json.loads((await _read_body(receive)).decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        await _respond(send, 400, "bad json")
        return

    try:
        await handle_update(payload)
    except Exception:
        # Отвечаем 200 в любом случае: иначе Telegram будет слать этот апдейт по кругу.
        traceback.print_exc()
    await _respond(send, 200, "ok")
