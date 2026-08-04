"""Проверка прод-хранилища.

На Vercel данные идут через Redis по HTTP, и это единственный путь, который не
проверяется остальными тестами. Поднимаем игрушечный сервер, отвечающий как Upstash,
и гоняем через него тот же сценарий записи.
"""

from datetime import date, datetime, time

import pytest
from aiohttp import web

from bot.booking import cancel_booking, create_booking, free_starts, user_bookings
from bot.config import get_config
from bot.schedule import fmt_hhmm
from bot.storage import UpstashStore

MONDAY = date(2026, 8, 3)


class FakeRedis:
    def __init__(self):
        self.strings: dict[str, str] = {}
        self.sets: dict[str, list[str]] = {}

    def execute(self, command: list[str]):
        name, args = command[0].upper(), command[1:]
        if name == "GET":
            return self.strings.get(args[0])
        if name == "SET":
            key, value, *options = args
            if "NX" in [o.upper() for o in options] and key in self.strings:
                return None
            self.strings[key] = value
            return "OK"
        if name == "DEL":
            return sum(1 for key in args if self.strings.pop(key, None) is not None)
        if name == "SADD":
            members = self.sets.setdefault(args[0], [])
            if args[1] not in members:
                members.append(args[1])
            return 1
        if name == "SREM":
            members = self.sets.get(args[0], [])
            if args[1] in members:
                members.remove(args[1])
                return 1
            return 0
        if name == "SMEMBERS":
            return list(self.sets.get(args[0], []))
        raise AssertionError(f"хранилище прислало неизвестную команду: {name}")


@pytest.fixture
async def store(aiohttp_server):
    redis = FakeRedis()

    async def handle(request: web.Request):
        if request.headers.get("Authorization") != "Bearer test-token":
            return web.json_response({"error": "unauthorized"}, status=401)
        return web.json_response({"result": redis.execute(await request.json())})

    app = web.Application()
    app.router.add_post("/", handle)
    server = await aiohttp_server(app)
    store = UpstashStore(str(server.make_url("/")), "test-token")
    yield store
    await store.close()


@pytest.fixture
def config():
    return get_config()


async def test_basic_commands(store):
    assert await store.get("missing") is None
    await store.set("k", "v")
    assert await store.get("k") == "v"
    assert await store.set_nx("k", "other") is False
    assert await store.set_nx("fresh", "v") is True
    await store.sadd("s", "a")
    await store.sadd("s", "a")
    await store.sadd("s", "b")
    assert sorted(await store.smembers("s")) == ["a", "b"]
    await store.srem("s", "a")
    assert await store.smembers("s") == ["b"]
    assert await store.delete("k") == 1


async def test_booking_cycle_over_http(store, config):
    now = datetime(2026, 8, 3, 9, 0, tzinfo=config.tz)
    booking = await create_booking(
        store,
        config,
        user_id=5,
        user_name="Тест",
        phone="+79000000000",
        username="test",
        service=config.service("combo"),
        master=config.master("anton"),
        day=MONDAY,
        start=time(12, 0),
        now=now,
    )
    assert booking is not None

    free = [fmt_hhmm(t) for t in await free_starts(
        store, config, config.master("anton"), MONDAY, config.service("combo"), now
    )]
    assert "12:00" not in free and "13:00" not in free
    assert [b.id for b in await user_bookings(store, config, 5, now)] == [booking.id]

    await cancel_booking(store, config, booking.id)
    free = [fmt_hhmm(t) for t in await free_starts(
        store, config, config.master("anton"), MONDAY, config.service("combo"), now
    )]
    assert "12:00" in free
