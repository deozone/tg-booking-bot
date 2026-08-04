"""Диалог бота.

Клиентский путь: меню → услуга → мастер → день → время → подтверждение → телефон.
Админский: уведомления о новых записях и отменах + расписание на день командой.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message

from . import texts
from .booking import (
    cancel_booking,
    create_booking,
    day_bookings,
    free_starts_by_master,
    get_client,
    pop_pending,
    save_client,
    save_pending,
    user_bookings,
)
from .config import SalonConfig, admin_ids
from .keyboards import (
    ANY_MASTER,
    CancelCB,
    ConfirmCB,
    DayCB,
    MasterCB,
    MenuCB,
    ServiceCB,
    TimeCB,
    back_kb,
    confirm_kb,
    days_kb,
    drop_reply_kb,
    main_menu,
    masters_kb,
    my_bookings_kb,
    phone_kb,
    services_kb,
    times_kb,
)
from .schedule import booking_days, fmt_hhmm, parse_hhmm, shift_starts, time_of_minutes
from .storage import Store

log = logging.getLogger(__name__)
router = Router()


def _now(config: SalonConfig) -> datetime:
    return datetime.now(config.tz)


async def _edit(callback: CallbackQuery, text: str, markup=None) -> None:
    """Правит текущее сообщение; если Telegram считает его неизменным — молчим."""
    try:
        await callback.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as error:
        if "message is not modified" not in str(error):
            raise


async def _notify_admins(bot: Bot, text: str) -> None:
    for admin_id in admin_ids():
        try:
            await bot.send_message(admin_id, text)
        except Exception:  # админ мог не начать диалог с ботом
            log.warning("не удалось уведомить админа %s", admin_id, exc_info=True)


# --- меню -------------------------------------------------------------------


@router.message(CommandStart())
async def cmd_start(message: Message, config: SalonConfig) -> None:
    await message.answer(
        texts.greeting(config, message.from_user.first_name),
        reply_markup=main_menu(message.from_user.id in admin_ids()),
    )


@router.callback_query(MenuCB.filter(F.action == "root"))
async def show_menu(callback: CallbackQuery, config: SalonConfig) -> None:
    await _edit(
        callback,
        texts.greeting(config, callback.from_user.first_name),
        main_menu(callback.from_user.id in admin_ids()),
    )
    await callback.answer()


@router.callback_query(MenuCB.filter(F.action == "info"))
async def show_contacts(callback: CallbackQuery, config: SalonConfig) -> None:
    await _edit(callback, texts.contacts(config), back_kb())
    await callback.answer()


@router.callback_query(MenuCB.filter(F.action == "book"))
async def show_services(callback: CallbackQuery, config: SalonConfig) -> None:
    await _edit(callback, texts.choose_service(), services_kb(config))
    await callback.answer()


# --- выбор услуги, мастера, дня, времени ------------------------------------


@router.callback_query(ServiceCB.filter())
async def show_masters(
    callback: CallbackQuery, callback_data: ServiceCB, config: SalonConfig
) -> None:
    service = config.service(callback_data.service)
    if service is None:
        await callback.answer("Услуга больше недоступна", show_alert=True)
        return
    await _edit(callback, texts.choose_master(service.title), masters_kb(config, service))
    await callback.answer()


@router.callback_query(MasterCB.filter())
async def show_days(callback: CallbackQuery, callback_data: MasterCB, config: SalonConfig) -> None:
    service = config.service(callback_data.service)
    if service is None:
        await callback.answer("Услуга больше недоступна", show_alert=True)
        return

    masters = (
        config.masters_for(service.id)
        if callback_data.master == ANY_MASTER
        else tuple(filter(None, [config.master(callback_data.master)]))
    )
    # Дни, в которые хоть кто-то из выбранных мастеров вообще выходит на смену.
    # Занятость здесь не проверяем: это десятки запросов в хранилище ради экрана,
    # который всё равно ведёт на список времени.
    days = [
        day
        for day in booking_days(config, _now(config))
        if any(shift_starts(master, day, service, config) for master in masters)
    ]
    label = "любой мастер" if callback_data.master == ANY_MASTER else masters[0].name if masters else "—"
    await _edit(
        callback,
        texts.choose_day(service.title, label),
        days_kb(service.id, callback_data.master, days),
    )
    await callback.answer()


@router.callback_query(DayCB.filter())
async def show_times(
    callback: CallbackQuery, callback_data: DayCB, config: SalonConfig, store: Store
) -> None:
    service = config.service(callback_data.service)
    if service is None:
        await callback.answer("Услуга больше недоступна", show_alert=True)
        return

    day = date.fromisoformat(callback_data.day)
    masters = (
        config.masters_for(service.id)
        if callback_data.master == ANY_MASTER
        else tuple(filter(None, [config.master(callback_data.master)]))
    )
    slots = await free_starts_by_master(store, config, masters, day, service, _now(config))
    if not slots:
        await _edit(
            callback,
            texts.no_time(day),
            days_kb(
                service.id,
                callback_data.master,
                [
                    d
                    for d in booking_days(config, _now(config))
                    if any(shift_starts(m, d, service, config) for m in masters)
                ],
            ),
        )
        await callback.answer()
        return

    label = "любой мастер" if callback_data.master == ANY_MASTER else masters[0].name
    await _edit(
        callback,
        texts.choose_time(service.title, label, day),
        times_kb(service.id, callback_data.master, day, list(slots)),
    )
    await callback.answer()


@router.callback_query(TimeCB.filter())
async def show_confirm(
    callback: CallbackQuery, callback_data: TimeCB, config: SalonConfig, store: Store
) -> None:
    service = config.service(callback_data.service)
    if service is None:
        await callback.answer("Услуга больше недоступна", show_alert=True)
        return

    day = date.fromisoformat(callback_data.day)
    masters = (
        config.masters_for(service.id)
        if callback_data.master == ANY_MASTER
        else tuple(filter(None, [config.master(callback_data.master)]))
    )
    start = fmt_hhmm(time_of_minutes(callback_data.start))
    slots = await free_starts_by_master(store, config, masters, day, service, _now(config))
    available = slots.get(start)
    if not available:
        await callback.answer(texts.slot_taken(), show_alert=True)
        await show_times(
            callback,
            DayCB(service=service.id, master=callback_data.master, day=callback_data.day),
            config,
            store,
        )
        return

    # «Любой мастер» превращается в конкретного человека уже здесь, чтобы клиент
    # видел в подтверждении имя, а не «кто-нибудь».
    draft = {
        "service": service.id,
        "master": available[0],
        "day": callback_data.day,
        "start": start,
    }
    await save_pending(store, callback.from_user.id, draft)
    await _edit(
        callback,
        texts.confirm_card(config, draft),
        confirm_kb(service.id, callback_data.master, day),
    )
    await callback.answer()


# --- подтверждение и бронь --------------------------------------------------


async def _finish_booking(
    *,
    bot: Bot,
    chat_id: int,
    user,
    draft: dict,
    phone: str,
    config: SalonConfig,
    store: Store,
) -> None:
    service = config.service(draft["service"])
    master = config.master(draft["master"])
    day = date.fromisoformat(draft["day"])
    booking = await create_booking(
        store,
        config,
        user_id=user.id,
        user_name=user.full_name,
        phone=phone,
        username=user.username or "",
        service=service,
        master=master,
        day=day,
        start=parse_hhmm(draft["start"]),
        now=_now(config),
    )
    if booking is None:
        await bot.send_message(chat_id, texts.slot_taken(), reply_markup=back_kb())
        return

    await bot.send_message(chat_id, texts.booked(config, booking), reply_markup=drop_reply_kb())
    await bot.send_message(chat_id, "Что-нибудь ещё?", reply_markup=main_menu(user.id in admin_ids()))
    await _notify_admins(bot, texts.admin_new_booking(config, booking))


@router.callback_query(ConfirmCB.filter())
async def confirm(callback: CallbackQuery, config: SalonConfig, store: Store, bot: Bot) -> None:
    draft = await pop_pending(store, callback.from_user.id)
    if draft is None:
        await callback.answer("Выбор устарел, начнём заново", show_alert=True)
        await show_services(callback, config)
        return

    client = await get_client(store, callback.from_user.id)
    if client is None:
        # Телефон ещё не знаем: возвращаем черновик в хранилище и просим контакт.
        await save_pending(store, callback.from_user.id, draft)
        await _edit(callback, texts.confirm_card(config, draft))
        await callback.message.answer(texts.ask_phone(), reply_markup=phone_kb())
        await callback.answer()
        return

    await callback.answer("Записываю…")
    await _edit(callback, texts.confirm_card(config, draft))
    await _finish_booking(
        bot=bot,
        chat_id=callback.message.chat.id,
        user=callback.from_user,
        draft=draft,
        phone=client["phone"],
        config=config,
        store=store,
    )


@router.message(F.contact)
async def got_contact(message: Message, config: SalonConfig, store: Store, bot: Bot) -> None:
    if message.contact.user_id != message.from_user.id:
        await message.answer(
            "Нужен ваш номер — нажмите кнопку «Отправить номер телефона».",
            reply_markup=phone_kb(),
        )
        return

    await save_client(store, message.from_user.id, message.from_user.full_name, message.contact.phone_number)
    draft = await pop_pending(store, message.from_user.id)
    if draft is None:
        await message.answer(
            "Спасибо! Номер сохранил. Выберите время — запись займёт минуту.",
            reply_markup=drop_reply_kb(),
        )
        await message.answer(texts.choose_service(), reply_markup=services_kb(config))
        return

    await _finish_booking(
        bot=bot,
        chat_id=message.chat.id,
        user=message.from_user,
        draft=draft,
        phone=message.contact.phone_number,
        config=config,
        store=store,
    )


# --- мои записи -------------------------------------------------------------


@router.callback_query(MenuCB.filter(F.action == "my"))
async def show_my(callback: CallbackQuery, config: SalonConfig, store: Store) -> None:
    bookings = await user_bookings(store, config, callback.from_user.id, _now(config))
    await _edit(
        callback,
        texts.my_bookings(config, bookings),
        my_bookings_kb([b.id for b in bookings]) if bookings else back_kb(),
    )
    await callback.answer()


@router.callback_query(CancelCB.filter())
async def cancel(
    callback: CallbackQuery, callback_data: CancelCB, config: SalonConfig, store: Store, bot: Bot
) -> None:
    booking = await cancel_booking(store, config, callback_data.booking)
    if booking is None:
        await callback.answer("Запись уже отменена", show_alert=True)
    else:
        await callback.answer("Отменил")
        await _notify_admins(bot, texts.admin_cancelled(config, booking))
    bookings = await user_bookings(store, config, callback.from_user.id, _now(config))
    await _edit(
        callback,
        (texts.cancelled(config, booking) + "\n\n" if booking else "")
        + texts.my_bookings(config, bookings),
        my_bookings_kb([b.id for b in bookings]) if bookings else back_kb(),
    )


# --- админ ------------------------------------------------------------------


@router.message(Command("today"))
async def cmd_today(message: Message, config: SalonConfig, store: Store) -> None:
    if message.from_user.id not in admin_ids():
        return
    day = _now(config).date()
    await message.answer(texts.admin_day(config, day, await day_bookings(store, day)))


@router.message(Command("week"))
async def cmd_week(message: Message, config: SalonConfig, store: Store) -> None:
    if message.from_user.id not in admin_ids():
        return
    today = _now(config).date()
    for offset in range(7):
        day = today + timedelta(days=offset)
        bookings = await day_bookings(store, day)
        if bookings:
            await message.answer(texts.admin_day(config, day, bookings))
    await message.answer("Это всё на ближайшую неделю.")


@router.message(Command("id"))
async def cmd_id(message: Message) -> None:
    await message.answer(f"Ваш Telegram ID: <code>{message.from_user.id}</code>")


@router.callback_query(F.data == "admin:today")
async def admin_today(callback: CallbackQuery, config: SalonConfig, store: Store) -> None:
    if callback.from_user.id not in admin_ids():
        await callback.answer()
        return
    day = _now(config).date()
    await _edit(callback, texts.admin_day(config, day, await day_bookings(store, day)), back_kb())
    await callback.answer()


# --- всё остальное ----------------------------------------------------------


@router.message()
async def fallback(message: Message, config: SalonConfig) -> None:
    await message.answer(
        "Записаться и посмотреть свои визиты можно кнопками ниже 👇",
        reply_markup=main_menu(message.from_user.id in admin_ids()),
    )
