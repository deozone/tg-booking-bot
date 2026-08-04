from datetime import date, datetime, time

import pytest

from bot.booking import (
    cancel_booking,
    create_booking,
    day_bookings,
    free_starts,
    free_starts_by_master,
    user_bookings,
)
from bot.config import get_config
from bot.schedule import fmt_hhmm
from bot.storage import JsonStore

MONDAY = date(2026, 8, 3)


@pytest.fixture
def config():
    return get_config()


@pytest.fixture
def store(tmp_path):
    return JsonStore(tmp_path / "bookings.json")


@pytest.fixture
def now(config):
    return datetime(2026, 8, 3, 9, 0, tzinfo=config.tz)


async def book(store, config, now, *, service="combo", master="anton", start=time(12, 0), user=1):
    return await create_booking(
        store,
        config,
        user_id=user,
        user_name="Тест",
        phone="+79000000000",
        username="test",
        service=config.service(service),
        master=config.master(master),
        day=MONDAY,
        start=start,
        now=now,
    )


async def test_booking_blocks_its_own_time(store, config, now):
    assert await book(store, config, now) is not None
    free = await free_starts(store, config, config.master("anton"), MONDAY, config.service("combo"), now)
    assert "12:00" not in [fmt_hhmm(t) for t in free]


async def test_long_service_blocks_overlapping_starts(store, config, now):
    """Комбо 12:00–13:30 закрывает и 11:00 (наехало бы), и 13:00 (наехало бы)."""
    await book(store, config, now)
    free = {
        fmt_hhmm(t)
        for t in await free_starts(
            store, config, config.master("anton"), MONDAY, config.service("combo"), now
        )
    }
    assert "11:00" not in free  # 11:00 + 90 мин задело бы занятое
    assert "13:00" not in free
    assert "10:00" in free
    assert "13:30" in free


async def test_second_booking_on_same_slot_is_rejected(store, config, now):
    assert await book(store, config, now, user=1) is not None
    assert await book(store, config, now, user=2) is None


async def test_other_master_stays_free(store, config, now):
    """Занятость Антона не должна закрывать время у Ильи (Марина в пн выходная)."""
    await book(store, config, now, master="anton")
    slots = await free_starts_by_master(
        store, config, config.masters_for("combo"), MONDAY, config.service("combo"), now
    )
    assert "anton" not in slots["12:00"]
    assert "ilya" in slots["12:00"]


async def test_cancel_releases_time(store, config, now):
    booking = await book(store, config, now)
    assert await cancel_booking(store, config, booking.id) is not None
    free = await free_starts(store, config, config.master("anton"), MONDAY, config.service("combo"), now)
    assert "12:00" in [fmt_hhmm(t) for t in free]
    assert await user_bookings(store, config, 1, now) == []
    assert await day_bookings(store, MONDAY) == []


async def test_indexes_see_the_booking(store, config, now):
    booking = await book(store, config, now, user=42)
    assert [b.id for b in await user_bookings(store, config, 42, now)] == [booking.id]
    assert [b.id for b in await day_bookings(store, MONDAY)] == [booking.id]


async def test_past_bookings_are_hidden_from_user(store, config, now):
    await book(store, config, now, start=time(12, 0))
    later = datetime(2026, 8, 3, 20, 0, tzinfo=config.tz)
    assert await user_bookings(store, config, 1, later) == []
    assert len(await user_bookings(store, config, 1, later, upcoming_only=False)) == 1
