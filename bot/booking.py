"""Операции над записями: что свободно, кто на что записан, бронь и отмена.

Хранилище — плоский Redis-подобный набор ключей:

* `booking:{id}`            — сама запись (JSON);
* `busy:{master}:{d}:{HH:MM}` — замок на клетку расписания, ставится через SETNX;
* `busyday:{master}:{d}`    — множество занятых клеток дня (одним запросом читаем день);
* `user:{tg_id}`            — записи клиента;
* `day:{d}`                 — записи дня (для админа и напоминаний);
* `client:{tg_id}`          — имя и телефон, чтобы не спрашивать повторно;
* `pending:{tg_id}`         — выбранный, но не подтверждённый слот, живёт 15 минут.

Замок на клетку — то место, где решается гонка: если два человека одновременно жмут
одно время, SETNX ляжет только у первого, второй получит «время только что заняли».
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import date, datetime, time

from .config import Master, SalonConfig, Service
from .schedule import busy_key, fmt_hhmm, parse_hhmm, is_bookable, shift_starts, slot_cells
from .storage import Store

PENDING_TTL = 15 * 60


@dataclass
class Booking:
    id: str
    user_id: int
    user_name: str
    phone: str
    username: str
    service_id: str
    master_id: str
    day: str  # YYYY-MM-DD
    start: str  # HH:MM
    created_at: str

    @property
    def date(self) -> date:
        return date.fromisoformat(self.day)

    @property
    def time(self) -> time:
        return parse_hhmm(self.start)

    def starts_at(self, config: SalonConfig) -> datetime:
        return datetime.combine(self.date, self.time, tzinfo=config.tz)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> "Booking":
        return cls(**json.loads(raw))


def busyday_key(master_id: str, day: date) -> str:
    return f"busyday:{master_id}:{day.isoformat()}"


async def busy_cells(store: Store, master_id: str, day: date) -> set[str]:
    return set(await store.smembers(busyday_key(master_id, day)))


async def free_starts(
    store: Store,
    config: SalonConfig,
    master: Master,
    day: date,
    service: Service,
    now: datetime,
) -> list[time]:
    """Время, когда этот мастер может взять эту услугу в этот день."""
    taken = await busy_cells(store, master.id, day)
    result = []
    for start in shift_starts(master, day, service, config):
        if not is_bookable(day, start, config, now):
            continue
        cells = slot_cells(start, service.duration, config.slot_step_minutes)
        if any(fmt_hhmm(cell) in taken for cell in cells):
            continue
        result.append(start)
    return result


async def free_starts_by_master(
    store: Store,
    config: SalonConfig,
    masters: tuple[Master, ...],
    day: date,
    service: Service,
    now: datetime,
) -> dict[str, list[str]]:
    """{'12:00': ['anton', 'ilya'], ...} — кто свободен в каждое время."""
    result: dict[str, list[str]] = {}
    for master in masters:
        for start in await free_starts(store, config, master, day, service, now):
            result.setdefault(fmt_hhmm(start), []).append(master.id)
    return dict(sorted(result.items()))


async def has_free_time(
    store: Store,
    config: SalonConfig,
    masters: tuple[Master, ...],
    day: date,
    service: Service,
    now: datetime,
) -> bool:
    for master in masters:
        if await free_starts(store, config, master, day, service, now):
            return True
    return False


async def create_booking(
    store: Store,
    config: SalonConfig,
    *,
    user_id: int,
    user_name: str,
    phone: str,
    username: str,
    service: Service,
    master: Master,
    day: date,
    start: time,
    now: datetime,
) -> Booking | None:
    """Занимает клетки расписания и сохраняет запись. None — время увели."""
    booking_id = uuid.uuid4().hex[:10]
    cells = slot_cells(start, service.duration, config.slot_step_minutes)

    locked: list[time] = []
    for cell in cells:
        if await store.set_nx(busy_key(master.id, day, cell), booking_id):
            locked.append(cell)
        else:
            await store.delete(*(busy_key(master.id, day, c) for c in locked))
            return None

    booking = Booking(
        id=booking_id,
        user_id=user_id,
        user_name=user_name,
        phone=phone,
        username=username,
        service_id=service.id,
        master_id=master.id,
        day=day.isoformat(),
        start=fmt_hhmm(start),
        created_at=now.isoformat(timespec="seconds"),
    )
    await store.set(f"booking:{booking_id}", booking.to_json())
    for cell in cells:
        await store.sadd(busyday_key(master.id, day), fmt_hhmm(cell))
    await store.sadd(f"user:{user_id}", booking_id)
    await store.sadd(f"day:{day.isoformat()}", booking_id)
    return booking


async def get_booking(store: Store, booking_id: str) -> Booking | None:
    raw = await store.get(f"booking:{booking_id}")
    return Booking.from_json(raw) if raw else None


async def cancel_booking(store: Store, config: SalonConfig, booking_id: str) -> Booking | None:
    booking = await get_booking(store, booking_id)
    if booking is None:
        return None
    service = config.service(booking.service_id)
    duration = service.duration if service else config.slot_step_minutes
    cells = slot_cells(booking.time, duration, config.slot_step_minutes)

    await store.delete(*(busy_key(booking.master_id, booking.date, c) for c in cells))
    for cell in cells:
        await store.srem(busyday_key(booking.master_id, booking.date), fmt_hhmm(cell))
    await store.srem(f"user:{booking.user_id}", booking_id)
    await store.srem(f"day:{booking.day}", booking_id)
    await store.delete(f"booking:{booking_id}")
    return booking


async def _collect(store: Store, index_key: str) -> list[Booking]:
    bookings = []
    for booking_id in await store.smembers(index_key):
        booking = await get_booking(store, booking_id)
        if booking is not None:
            bookings.append(booking)
        else:  # запись пропала — чистим индекс
            await store.srem(index_key, booking_id)
    return sorted(bookings, key=lambda b: (b.day, b.start))


async def user_bookings(
    store: Store, config: SalonConfig, user_id: int, now: datetime, *, upcoming_only: bool = True
) -> list[Booking]:
    bookings = await _collect(store, f"user:{user_id}")
    if not upcoming_only:
        return bookings
    return [b for b in bookings if b.starts_at(config) >= now]


async def day_bookings(store: Store, day: date) -> list[Booking]:
    return await _collect(store, f"day:{day.isoformat()}")


# --- профиль клиента и незавершённый выбор ---------------------------------


async def save_client(store: Store, user_id: int, name: str, phone: str) -> None:
    await store.set(f"client:{user_id}", json.dumps({"name": name, "phone": phone}, ensure_ascii=False))


async def get_client(store: Store, user_id: int) -> dict | None:
    raw = await store.get(f"client:{user_id}")
    return json.loads(raw) if raw else None


async def save_pending(store: Store, user_id: int, payload: dict) -> None:
    await store.set(f"pending:{user_id}", json.dumps(payload, ensure_ascii=False), ttl=PENDING_TTL)


async def pop_pending(store: Store, user_id: int) -> dict | None:
    raw = await store.get(f"pending:{user_id}")
    if raw is None:
        return None
    await store.delete(f"pending:{user_id}")
    return json.loads(raw)
