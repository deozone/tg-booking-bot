"""Прогон живого диалога без Telegram.

Сессия бота подменена заглушкой: она запоминает вызовы API и возвращает правдоподобные
ответы. Так проверяется вся цепочка — фильтры, callback-данные, хендлеры, хранилище —
на настоящих объектах aiogram.
"""

from datetime import datetime, timedelta

import pytest
from aiogram.client.session.base import BaseSession
from aiogram.methods import TelegramMethod
from aiogram.types import CallbackQuery, Chat, Contact, Message, Update, User

from bot.app import get_dispatcher
from bot.booking import save_client, user_bookings
from bot.config import get_config
from bot.keyboards import ConfirmCB, DayCB, MasterCB, MenuCB, ServiceCB, TimeCB
from bot.schedule import minutes_of, parse_hhmm, shift_starts
from bot.storage import JsonStore

USER = User(id=777, is_bot=False, first_name="Миша", last_name="Б", username="misha")
CHAT = Chat(id=777, type="private")


class StubSession(BaseSession):
    """Ничего не шлёт наружу, только записывает, что бот собирался отправить."""

    def __init__(self):
        super().__init__()
        self.calls: list[tuple[str, dict]] = []
        self._message_id = 100

    async def close(self):
        return None

    async def stream_content(self, *args, **kwargs):  # pragma: no cover - не используется
        yield b""

    async def make_request(self, bot, method: TelegramMethod, timeout=None):
        name = type(method).__name__
        self.calls.append((name, method.model_dump(exclude_none=True)))
        if name in {"SendMessage", "EditMessageText"}:
            self._message_id += 1
            return Message(
                message_id=self._message_id,
                date=datetime.now(),
                chat=CHAT,
                from_user=User(id=1, is_bot=True, first_name="bot"),
                text=method.text,
            ).as_(bot)
        return True

    def texts_of(self, *methods: str) -> list[str]:
        return [call["text"] for name, call in self.calls if name in methods]

    @property
    def last_markup(self) -> dict:
        for _name, call in reversed(self.calls):
            if "reply_markup" in call:
                return call["reply_markup"]
        return {}

    def buttons(self) -> list[dict]:
        rows = self.last_markup.get("inline_keyboard", [])
        return [button for row in rows for button in row]

    def time_buttons(self) -> list[str]:
        return [b["text"] for b in self.buttons() if len(b["text"]) == 5 and ":" in b["text"]]

    def asked_for_contact(self) -> bool:
        for _name, call in self.calls:
            rows = (call.get("reply_markup") or {}).get("keyboard", [])
            if any(button.get("request_contact") for row in rows for button in row):
                return True
        return False


@pytest.fixture
def bot():
    from aiogram import Bot
    from aiogram.client.default import DefaultBotProperties
    from aiogram.enums import ParseMode

    return Bot(
        "42:TEST",
        session=StubSession(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


@pytest.fixture
def store(tmp_path):
    return JsonStore(tmp_path / "bookings.json")


@pytest.fixture
def dispatcher(store):
    """Диспетчер один на процесс — как в проде; хранилище едет вместе с апдейтом."""
    dispatcher = get_dispatcher()
    original = dispatcher.feed_update

    async def feed(bot, update, **kwargs):
        return await original(bot, update, config=get_config(), store=store, **kwargs)

    dispatcher.feed_update = feed
    yield dispatcher
    dispatcher.feed_update = original


def message_update(text: str, contact: Contact | None = None) -> Update:
    return Update(
        update_id=1,
        message=Message(
            message_id=1,
            date=datetime.now(),
            chat=CHAT,
            from_user=USER,
            text=text,
            contact=contact,
        ),
    )


def callback_update(data: str) -> Update:
    return Update(
        update_id=2,
        callback_query=CallbackQuery(
            id="cb",
            from_user=USER,
            chat_instance="ci",
            data=data,
            message=Message(
                message_id=100,
                date=datetime.now(),
                chat=CHAT,
                from_user=User(id=1, is_bot=True, first_name="bot"),
                text="предыдущее сообщение",
            ),
        ),
    )


def next_workday(config, master_id: str, service_id: str):
    """Ближайший день, когда мастер работает: на «сегодня» слоты могут кончиться."""
    master = config.master(master_id)
    service = config.service(service_id)
    today = datetime.now(config.tz).date()
    for offset in range(1, config.booking_depth_days):
        day = today + timedelta(days=offset)
        if shift_starts(master, day, service, config):
            return day
    raise AssertionError("не нашёл рабочий день")


def time_cb(day, start: str) -> str:
    return TimeCB(
        service="haircut", master="anton", day=day.isoformat(), start=minutes_of(parse_hhmm(start))
    ).pack()


async def pick_first_free_time(dispatcher, bot, day) -> str:
    await dispatcher.feed_update(
        bot, callback_update(DayCB(service="haircut", master="anton", day=day.isoformat()).pack())
    )
    times = bot.session.time_buttons()
    assert times, "не показалось ни одного свободного времени"
    await dispatcher.feed_update(bot, callback_update(time_cb(day, times[0])))
    return times[0]


async def test_start_shows_menu(dispatcher, bot):
    await dispatcher.feed_update(bot, message_update("/start"))
    assert any("здравствуйте" in text for text in bot.session.texts_of("SendMessage"))
    assert any("Записаться" in b["text"] for b in bot.session.buttons())


async def test_full_booking_path(dispatcher, bot, store):
    config = get_config()
    day = next_workday(config, "anton", "haircut")

    await dispatcher.feed_update(bot, callback_update(MenuCB(action="book").pack()))
    assert any("Мужская стрижка" in b["text"] for b in bot.session.buttons())

    await dispatcher.feed_update(bot, callback_update(ServiceCB(service="haircut").pack()))
    assert any(b["text"] == "Антон" for b in bot.session.buttons())

    await dispatcher.feed_update(
        bot, callback_update(MasterCB(service="haircut", master="anton").pack())
    )
    day_button = DayCB(service="haircut", master="anton", day=day.isoformat()).pack()
    assert any(b["callback_data"] == day_button for b in bot.session.buttons())

    start = await pick_first_free_time(dispatcher, bot, day)
    assert any("Проверьте запись" in t for t in bot.session.texts_of("EditMessageText"))

    # Телефона ещё не знаем — бот обязан попросить контакт, а не записать втихую.
    await dispatcher.feed_update(bot, callback_update(ConfirmCB().pack()))
    assert bot.session.asked_for_contact()
    assert await user_bookings(store, config, USER.id, datetime.now(config.tz)) == []

    contact = Contact(phone_number="+79001234567", first_name="Миша", user_id=USER.id)
    await dispatcher.feed_update(bot, message_update("", contact=contact))

    bookings = await user_bookings(store, config, USER.id, datetime.now(config.tz))
    assert len(bookings) == 1
    assert bookings[0].start == start
    assert bookings[0].phone == "+79001234567"
    assert any("Записал вас" in t for t in bot.session.texts_of("SendMessage"))


async def test_taken_time_is_not_double_booked(dispatcher, bot, store):
    """Кнопка со временем могла устареть: к моменту нажатия слот уже занят."""
    config = get_config()
    day = next_workday(config, "anton", "haircut")
    await save_client(store, USER.id, "Миша", "+79001234567")

    start = await pick_first_free_time(dispatcher, bot, day)
    await dispatcher.feed_update(bot, callback_update(ConfirmCB().pack()))
    assert len(await user_bookings(store, config, USER.id, datetime.now(config.tz))) == 1

    # Второй заход на то же время — предлагаем выбрать другое, а не пишем вторую запись.
    await dispatcher.feed_update(bot, callback_update(time_cb(day, start)))
    assert start not in bot.session.time_buttons()
    assert len(await user_bookings(store, config, USER.id, datetime.now(config.tz))) == 1


async def test_second_booking_skips_phone_question(dispatcher, bot, store):
    config = get_config()
    day = next_workday(config, "anton", "haircut")
    await save_client(store, USER.id, "Миша", "+79001234567")

    await pick_first_free_time(dispatcher, bot, day)
    await dispatcher.feed_update(bot, callback_update(ConfirmCB().pack()))

    assert not bot.session.asked_for_contact()
    assert len(await user_bookings(store, config, USER.id, datetime.now(config.tz))) == 1


async def test_cancel_from_my_bookings(dispatcher, bot, store):
    config = get_config()
    day = next_workday(config, "anton", "haircut")
    await save_client(store, USER.id, "Миша", "+79001234567")

    await pick_first_free_time(dispatcher, bot, day)
    await dispatcher.feed_update(bot, callback_update(ConfirmCB().pack()))

    await dispatcher.feed_update(bot, callback_update(MenuCB(action="my").pack()))
    cancel_button = next(b for b in bot.session.buttons() if b["text"].startswith("❌"))

    await dispatcher.feed_update(bot, callback_update(cancel_button["callback_data"]))
    assert await user_bookings(store, config, USER.id, datetime.now(config.tz)) == []


async def test_foreign_contact_is_rejected(dispatcher, bot, store):
    config = get_config()
    contact = Contact(phone_number="+79990000000", first_name="Чужой", user_id=999)
    await dispatcher.feed_update(bot, message_update("", contact=contact))
    assert any("Нужен ваш номер" in t for t in bot.session.texts_of("SendMessage"))
    assert await user_bookings(store, config, USER.id, datetime.now(config.tz)) == []


async def test_unknown_text_returns_menu(dispatcher, bot):
    await dispatcher.feed_update(bot, message_update("здравствуйте, а можно завтра?"))
    assert any("кнопками ниже" in t for t in bot.session.texts_of("SendMessage"))
