"""Утренние напоминания. Vercel дёргает этот адрес по расписанию из vercel.json."""

from __future__ import annotations

import asyncio
import os
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.app import build_bot  # noqa: E402
from bot.booking import day_bookings  # noqa: E402
from bot.config import get_config  # noqa: E402
from bot.storage import create_store  # noqa: E402
from bot import texts  # noqa: E402


async def send_reminders() -> int:
    config = get_config()
    bot = build_bot()
    store = create_store()
    sent = 0
    try:
        from datetime import datetime

        today = datetime.now(config.tz).date()
        for booking in await day_bookings(store, today):
            try:
                await bot.send_message(booking.user_id, texts.reminder(config, booking))
                sent += 1
            except Exception:
                pass  # клиент мог заблокировать бота
    finally:
        await store.close()
        await bot.session.close()
    return sent


class handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        secret = os.getenv("CRON_SECRET")
        if secret and self.headers.get("Authorization") != f"Bearer {secret}":
            self.send_response(403)
            self.end_headers()
            return

        sent = asyncio.run(send_reminders())
        body = f"reminders sent: {sent}".encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
