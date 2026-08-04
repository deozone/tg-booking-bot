"""Настройки салона и окружения.

Всё, что меняется под конкретного клиента, лежит в `salon.json`: услуги, мастера,
график работы, глубина записи. Код трогать не нужно.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

CONFIG_PATH = Path(__file__).with_name("salon.json")

WEEKDAY_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


@dataclass(frozen=True)
class Service:
    id: str
    title: str
    price: int
    duration: int  # минуты

    @property
    def price_label(self) -> str:
        return f"{self.price:,}".replace(",", " ") + " ₽"


@dataclass(frozen=True)
class Master:
    id: str
    name: str
    services: tuple[str, ...]
    schedule: dict[str, tuple[str, str] | None]

    def works_on(self, weekday: int) -> tuple[str, str] | None:
        """weekday — как в `datetime.weekday()`: 0 = понедельник."""
        return self.schedule.get(WEEKDAY_KEYS[weekday])

    def does(self, service_id: str) -> bool:
        return service_id in self.services


@dataclass(frozen=True)
class SalonConfig:
    title: str
    address: str
    phone: str
    timezone: str
    booking_depth_days: int
    slot_step_minutes: int
    min_lead_minutes: int
    services: tuple[Service, ...]
    masters: tuple[Master, ...]

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def service(self, service_id: str) -> Service | None:
        return next((s for s in self.services if s.id == service_id), None)

    def master(self, master_id: str) -> Master | None:
        return next((m for m in self.masters if m.id == master_id), None)

    def masters_for(self, service_id: str) -> tuple[Master, ...]:
        return tuple(m for m in self.masters if m.does(service_id))


def load_config(path: Path | None = None) -> SalonConfig:
    raw = json.loads((path or CONFIG_PATH).read_text(encoding="utf-8"))
    services = tuple(
        Service(id=s["id"], title=s["title"], price=int(s["price"]), duration=int(s["duration"]))
        for s in raw["services"]
    )
    known = {s.id for s in services}
    masters = []
    for m in raw["masters"]:
        unknown = set(m["services"]) - known
        if unknown:
            raise ValueError(f"мастер {m['id']}: неизвестные услуги {sorted(unknown)}")
        schedule = {
            key: (tuple(m["schedule"][key]) if m["schedule"].get(key) else None)
            for key in WEEKDAY_KEYS
        }
        masters.append(
            Master(id=m["id"], name=m["name"], services=tuple(m["services"]), schedule=schedule)
        )
    return SalonConfig(
        title=raw["title"],
        address=raw["address"],
        phone=raw["phone"],
        timezone=raw.get("timezone", "Europe/Moscow"),
        booking_depth_days=int(raw.get("booking_depth_days", 14)),
        slot_step_minutes=int(raw.get("slot_step_minutes", 30)),
        min_lead_minutes=int(raw.get("min_lead_minutes", 60)),
        services=services,
        masters=tuple(masters),
    )


@lru_cache(maxsize=1)
def get_config() -> SalonConfig:
    return load_config()


def admin_ids() -> set[int]:
    raw = os.getenv("ADMIN_IDS", "")
    return {int(part) for part in raw.replace(";", ",").split(",") if part.strip().isdigit()}
