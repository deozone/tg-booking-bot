"""Тексты сообщений. Вынесены отдельно, чтобы правки под клиента не лезли в логику."""

from __future__ import annotations

from datetime import date

from .booking import Booking
from .config import SalonConfig
from .schedule import fmt_date


def plural(count: int, one: str, few: str, many: str) -> str:
    """«1 запись», «2 записи», «5 записей» — обычные русские правила."""
    tail_two, tail_one = count % 100, count % 10
    if 11 <= tail_two <= 14:
        return many
    if tail_one == 1:
        return one
    if 2 <= tail_one <= 4:
        return few
    return many


def greeting(config: SalonConfig, name: str) -> str:
    return (
        f"<b>{config.title}</b>\n\n"
        f"{name}, здравствуйте! Здесь можно записаться за минуту — без звонков.\n"
        f"Запись открыта на {config.booking_depth_days} дней вперёд."
    )


def contacts(config: SalonConfig) -> str:
    return (
        f"<b>{config.title}</b>\n\n"
        f"📍 {config.address}\n"
        f"☎️ {config.phone}\n\n"
        f"Часы работы зависят от смены мастера — свободное время видно при записи."
    )


def choose_service() -> str:
    return "Выберите услугу:"


def choose_master(service_title: str) -> str:
    return f"<b>{service_title}</b>\nК кому записываемся?"


def choose_day(service_title: str, master_label: str) -> str:
    return f"<b>{service_title}</b> · {master_label}\nВыберите дату:"


def choose_time(service_title: str, master_label: str, day: date) -> str:
    return f"<b>{service_title}</b> · {master_label}\n{fmt_date(day)} — свободное время:"


def no_time(day: date) -> str:
    return (
        f"На {fmt_date(day)} свободного времени не осталось.\n"
        f"Выберите другую дату или другого мастера."
    )


def confirm_card(config: SalonConfig, booking_draft: dict) -> str:
    service = config.service(booking_draft["service"])
    master = config.master(booking_draft["master"])
    day = date.fromisoformat(booking_draft["day"])
    # Длительность в карточке не показываем: она нужна расписанию, чтобы не сажать
    # следующего клиента внахлёст, но клиенту это выглядело бы как обещание уложиться
    # ровно в час — а мастер такого не обещал.
    return (
        "<b>Проверьте запись</b>\n\n"
        f"Услуга: {service.title}\n"
        f"Мастер: {master.name}\n"
        f"Когда: {fmt_date(day)}, {booking_draft['start']}\n"
        f"Стоимость: {service.price_label}"
    )


def ask_phone() -> str:
    return (
        "Остался один шаг — телефон, чтобы мастер мог связаться, если что-то изменится.\n"
        "Нажмите кнопку ниже, номер подставится автоматически."
    )


def booked(config: SalonConfig, booking: Booking) -> str:
    service = config.service(booking.service_id)
    master = config.master(booking.master_id)
    return (
        "✅ <b>Записал вас</b>\n\n"
        f"{service.title} · {master.name}\n"
        f"{fmt_date(booking.date)}, {booking.start}\n"
        f"{config.address}\n\n"
        f"Напомню утром в день визита. Отменить или перенести можно в «Мои записи»."
    )


def slot_taken() -> str:
    return "Это время только что заняли 😔 Выберите, пожалуйста, другое."


def my_bookings(config: SalonConfig, bookings: list[Booking]) -> str:
    if not bookings:
        return "У вас пока нет активных записей."
    lines = ["<b>Ваши записи</b>", ""]
    for index, booking in enumerate(bookings, start=1):
        service = config.service(booking.service_id)
        master = config.master(booking.master_id)
        lines.append(
            f"{index}. {fmt_date(booking.date)}, {booking.start} — "
            f"{service.title} · {master.name} · {service.price_label}"
        )
    return "\n".join(lines)


def cancelled(config: SalonConfig, booking: Booking) -> str:
    service = config.service(booking.service_id)
    return f"Запись отменена: {service.title}, {fmt_date(booking.date)} в {booking.start}."


def admin_new_booking(config: SalonConfig, booking: Booking) -> str:
    service = config.service(booking.service_id)
    master = config.master(booking.master_id)
    contact = f"@{booking.username}" if booking.username else "—"
    return (
        "🆕 <b>Новая запись</b>\n\n"
        f"{fmt_date(booking.date)}, {booking.start}\n"
        f"{service.title} · {master.name} · {service.price_label}\n"
        f"Клиент: {booking.user_name} ({contact})\n"
        f"Телефон: {booking.phone}"
    )


def admin_cancelled(config: SalonConfig, booking: Booking) -> str:
    service = config.service(booking.service_id)
    master = config.master(booking.master_id)
    return (
        "🚫 <b>Отмена</b>\n\n"
        f"{fmt_date(booking.date)}, {booking.start}\n"
        f"{service.title} · {master.name}\n"
        f"Клиент: {booking.user_name} · {booking.phone}"
    )


def admin_day(config: SalonConfig, day: date, bookings: list[Booking]) -> str:
    if not bookings:
        return f"<b>{fmt_date(day)}</b>\n\nЗаписей нет."
    lines = [f"<b>{fmt_date(day)}</b>", ""]
    revenue = 0
    for booking in bookings:
        service = config.service(booking.service_id)
        master = config.master(booking.master_id)
        revenue += service.price
        lines.append(
            f"{booking.start} — {service.title} · {master.name}\n"
            f"    {booking.user_name}, {booking.phone}"
        )
    lines.append("")
    word = plural(len(bookings), "запись", "записи", "записей")
    lines.append(f"Итого: {len(bookings)} {word} на {revenue:,} ₽".replace(",", " "))
    return "\n".join(lines)


def reminder(config: SalonConfig, booking: Booking) -> str:
    service = config.service(booking.service_id)
    master = config.master(booking.master_id)
    return (
        f"⏰ Напоминаю: сегодня в <b>{booking.start}</b> вы записаны — "
        f"{service.title} · {master.name}.\n{config.address}"
    )
