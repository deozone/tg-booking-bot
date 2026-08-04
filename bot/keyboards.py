"""Клавиатуры и разбор callback-данных.

Весь путь клиента (услуга → мастер → день → время) закодирован прямо в кнопках.
Это не украшение: на serverless-вебхуке нет процесса, который помнил бы, на каком
шаге находится диалог, — кнопка сама несёт весь контекст.
"""

from __future__ import annotations

from datetime import date

from aiogram.filters.callback_data import CallbackData
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .config import SalonConfig, Service
from .schedule import fmt_date, minutes_of, parse_hhmm

ANY_MASTER = "any"


class MenuCB(CallbackData, prefix="menu"):
    action: str  # root | book | my | info


class ServiceCB(CallbackData, prefix="srv"):
    service: str


class MasterCB(CallbackData, prefix="mst"):
    service: str
    master: str


class DayCB(CallbackData, prefix="day"):
    service: str
    master: str
    day: str


class TimeCB(CallbackData, prefix="tm"):
    service: str
    master: str
    day: str
    start: int  # минуты от полуночи: «10:30» в callback-данные не влезает, там ':' — разделитель


class ConfirmCB(CallbackData, prefix="ok"):
    pass


class CancelCB(CallbackData, prefix="cnl"):
    booking: str


def main_menu(is_admin: bool = False) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="📅 Записаться", callback_data=MenuCB(action="book"))
    kb.button(text="🗓 Мои записи", callback_data=MenuCB(action="my"))
    kb.button(text="📍 Адрес и контакты", callback_data=MenuCB(action="info"))
    kb.adjust(1)
    if is_admin:
        kb.row(InlineKeyboardButton(text="⚙️ Записи на сегодня", callback_data="admin:today"))
    return kb.as_markup()


def services_kb(config: SalonConfig) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for service in config.services:
        kb.button(
            text=f"{service.title} — {service.price_label}",
            callback_data=ServiceCB(service=service.id),
        )
    kb.button(text="‹ Назад", callback_data=MenuCB(action="root"))
    kb.adjust(1)
    return kb.as_markup()


def masters_kb(config: SalonConfig, service: Service) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(
        text="🎲 Любой мастер (больше свободных окон)",
        callback_data=MasterCB(service=service.id, master=ANY_MASTER),
    )
    for master in config.masters_for(service.id):
        kb.button(text=master.name, callback_data=MasterCB(service=service.id, master=master.id))
    kb.button(text="‹ К услугам", callback_data=MenuCB(action="book"))
    kb.adjust(1)
    return kb.as_markup()


def days_kb(service_id: str, master_id: str, days: list[date]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for day in days:
        kb.button(
            text=fmt_date(day),
            callback_data=DayCB(service=service_id, master=master_id, day=day.isoformat()),
        )
    kb.adjust(2)
    kb.row(InlineKeyboardButton(text="‹ К мастерам", callback_data=ServiceCB(service=service_id).pack()))
    return kb.as_markup()


def times_kb(service_id: str, master_id: str, day: date, starts: list[str]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for start in starts:
        kb.button(
            text=start,
            callback_data=TimeCB(
                service=service_id,
                master=master_id,
                day=day.isoformat(),
                start=minutes_of(parse_hhmm(start)),
            ),
        )
    kb.adjust(4)
    kb.row(
        InlineKeyboardButton(
            text="‹ К датам",
            callback_data=MasterCB(service=service_id, master=master_id).pack(),
        )
    )
    return kb.as_markup()


def confirm_kb(service_id: str, master_id: str, day: date) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Подтвердить запись", callback_data=ConfirmCB())
    kb.button(
        text="‹ Выбрать другое время",
        callback_data=DayCB(service=service_id, master=master_id, day=day.isoformat()),
    )
    kb.adjust(1)
    return kb.as_markup()


def my_bookings_kb(booking_ids: list[str]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for index, booking_id in enumerate(booking_ids, start=1):
        kb.button(text=f"❌ Отменить запись {index}", callback_data=CancelCB(booking=booking_id))
    kb.button(text="‹ В меню", callback_data=MenuCB(action="root"))
    kb.adjust(1)
    return kb.as_markup()


def back_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="‹ В меню", callback_data=MenuCB(action="root"))
    return kb.as_markup()


def phone_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Отправить номер телефона", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder="Нажмите кнопку ниже",
    )


def drop_reply_kb() -> ReplyKeyboardRemove:
    return ReplyKeyboardRemove()
