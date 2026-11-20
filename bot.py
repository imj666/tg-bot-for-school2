# -*- coding: utf-8 -*-
"""
🎓 ПИ1 Расписание — Telegram-бот с расписанием пар.

Возможности:
  • выбор группы (1 / 2), запоминается для каждого пользователя
  • расписание на сегодня и на завтра (по реальной дате, МСК)
  • полное расписание (по дням и вся неделя, чётная / нечётная)
  • вкладка «Время пар» со звонками и перемен

Чётность недели считается автоматически и меняется в субботу в 00:00 по МСК.
Опорная точка: неделя с 16.11.2026 (Пн) – чётная, с 21.11.2026 (Сб) уже нечётная.

Бот работает в режиме polling и параллельно поднимает лёгкий HTTP-сервер,
который отвечает «200 OK» на любые запросы на порту из переменной PORT
(нужно для хостингов с health-check: Render, Railway, Koyeb и т.п.).

Запуск:
    pip install -r requirements.txt
    export BOT_TOKEN="9144967869:AAGS9QSZfigLWUK4dTrLuHrMRwxpyI-xthU"      # Windows (PowerShell): $env:BOT_TOKEN="..."
    export PORT=8080                      # по умолчанию 8080
    python bot.py
"""
from __future__ import annotations

import asyncio
import html
import logging
import os
import sqlite3
from datetime import date, datetime, time, timedelta, timezone
from typing import List, Optional, Tuple

from aiohttp import web
from aiogram import Bot, Dispatcher, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

# ─────────────────────────────── НАСТРОЙКИ ───────────────────────────────

BOT_NAME = "ПИ1 Расписание"
BOT_TOKEN = "9144967869:AAGS9QSZfigLWUK4dTrLuHrMRwxpyI-xthU"
DB_PATH = os.getenv("DB_PATH", "bot.db")
PORT = int(os.getenv("PORT", "8080"))

# Москва — UTC+3 без перехода на летнее время
MSK = timezone(timedelta(hours=3), "MSK")

# Понедельник ЧЁТНОЙ недели — точка отсчёта (сегодня 20.11.2026 — чётная неделя)
ANCHOR_MONDAY = date(2026, 11, 16)

# ─────────────────────────────── ВРЕМЯ ПАР ───────────────────────────────

PAIRS: List[Tuple[time, time]] = [
    (time(8, 0), time(9, 30)),
    (time(9, 40), time(11, 10)),
    (time(11, 25), time(12, 55)),
    (time(13, 5), time(14, 35)),
]

# ─────────────────────────────── РАСПИСАНИЕ ──────────────────────────────
# Урок: (название, кабинет или None). Нет пары: None.
# День: список из 4 пар, каждая пара = (урок группы 1, урок группы 2).

Lesson = Optional[Tuple[str, Optional[str]]]
Day = List[Tuple[Lesson, Lesson]]


def L(name: str, room: Optional[str] = None) -> Lesson:
    return (name, room)


def same(lesson: Lesson) -> Tuple[Lesson, Lesson]:
    """Одинаковая пара для обеих групп."""
    return (lesson, lesson)


NO: Lesson = None

# ── Общие уроки ──
MOLD = L("Молдавский язык", "201А")
UKR = L("Украинский язык", "202А")
PHYS = L("Физическая культура")
HIST_LEC = L("История России (лекция)", "214В")
HIST_PR = L("История России (практика)", "208В")
INTRO_215 = L("Введение в проф. деятельность", "215В")
INTRO_208 = L("Введение в проф. деятельность", "208В")
MATH_PR = L("Математика (практика)", "211В")
MATH_LEC = L("Математика (лекция)", "214В")
ORG_LEC = L("ОРГ (лекция)", "214В")
ORG_PR = L("ОРГ (практика)", "206В")
OP_LEC = L("ОП (лекция)", "214В")
OP_309 = L("ОП", "309В")
SKV_316 = L("СКВ", "316В")
SKV = L("СКВ")
ENG_301 = L("Английский язык", "301А")
ENG_207 = L("Английский язык", "207В")
ENG = L("Английский язык")
PRACT_306 = L("Практикум по решению задач", "306В")
PRACT = L("Практикум по решению задач")

# ── ЧЁТНАЯ неделя ──
EVEN: List[Day] = [
    # Понедельник
    [
        (MOLD, UKR),
        same(PHYS),
        same(HIST_LEC),
        same(NO),
    ],
    # Вторник
    [
        same(NO),
        same(NO),
        (PRACT_306, NO),
        (NO, L("ОП (Кирсанова)", "208В")),
    ],
    # Среда
    [
        same(INTRO_215),
        same(HIST_LEC),
        same(HIST_PR),
        same(MATH_PR),
    ],
    # Четверг
    [
        same(NO),
        same(ORG_LEC),
        (OP_309, SKV_316),
        (PRACT, OP_309),
    ],
    # Пятница
    [
        same(MATH_LEC),
        same(ORG_PR),
        (SKV, ENG_301),
        (ENG, NO),
    ],
]

# ── НЕЧЁТНАЯ неделя ──
ODD: List[Day] = [
    # Понедельник
    [
        (MOLD, UKR),
        same(PHYS),
        same(HIST_LEC),
        same(INTRO_208),
    ],
    # Вторник
    [
        same(NO),
        same(NO),
        same(NO),
        (NO, PRACT_306),
    ],
    # Среда
    [
        same(NO),
        same(HIST_LEC),
        (ENG_207, PRACT_306),
        same(MATH_PR),
    ],
    # Четверг
    [
        same(NO),
        same(ORG_LEC),
        (OP_309, SKV_316),
        (ENG_207, OP_309),
    ],
    # Пятница
    [
        same(OP_LEC),
        same(ORG_PR),
        (SKV, ENG_301),
        (NO, ENG_301),
    ],
]

SCHEDULE = {"odd": ODD, "even": EVEN}

# ─────────────────────────────── ОФОРМЛЕНИЕ ──────────────────────────────

DAYS = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
DAYS_SHORT = ["Пн", "Вт", "Ср", "Чт", "Пт"]
DAY_EMOJI = ["🟦", "🟪", "🟩", "🟨", "🟧"]
NUM = ["1️⃣", "2️⃣", "3️⃣", "4️⃣"]
WEEK_TITLE = {"odd": "🔵 Нечётная неделя", "even": "🟣 Чётная неделя"}
WEEK_SHORT = {"odd": "Нечётная", "even": "Чётная"}
DIVIDER = "━━━━━━━━━━━━━━━━━━"

# Эмодзи к предметам (по началу названия)
EMOJI_RULES = [
    ("молдав", "🇲🇩"),
    ("украин", "🇺🇦"),
    ("англ", "🇬🇧"),
    ("физическая", "🏃"),
    ("истори", "🏛"),
    ("математ", "📐"),
    ("практикум", "🧩"),
    ("введение", "📘"),
    ("оп", "💻"),
    ("орг", "🗂"),
    ("скв", "🛠"),
]


def lesson_emoji(name: str) -> str:
    low = name.lower()
    for key, emoji in EMOJI_RULES:
        if low.startswith(key):
            return emoji
    return "📖"


def fmt_t(t: time) -> str:
    return t.strftime("%H:%M")


# ─────────────────────────────── ЛОГИКА НЕДЕЛЬ ───────────────────────────


def now_msk() -> datetime:
    return datetime.now(MSK)


def week_type(d: date) -> str:
    """
    'odd' / 'even' для даты d.
    Суббота и воскресенье уже относятся к СЛЕДУЮЩЕЙ неделе
    (чётность меняется в субботу в 00:00 по МСК).
    """
    monday = d - timedelta(days=d.weekday())
    if d.weekday() >= 5:
        monday += timedelta(days=7)
    n = (monday - ANCHOR_MONDAY).days // 7
    return "even" if n % 2 == 0 else "odd"


def get_lessons(week: str, day: int, group: int) -> List[Lesson]:
    return [pair[group - 1] for pair in SCHEDULE[week][day]]


def current_pair_index(now: datetime) -> Optional[int]:
    if now.weekday() >= 5:
        return None
    t = now.time()
    for i, (s, e) in enumerate(PAIRS):
        if s <= t < e:
            return i
    return None


# ─────────────────────────────── ТЕКСТЫ ──────────────────────────────────


def lesson_lines(lesson: Lesson) -> List[str]:
    if lesson is None:
        return ["      😴 <i>Нет пары</i>"]
    name, room = lesson
    out = [f"      {lesson_emoji(name)} <b>{html.escape(name)}</b>"]
    if room:
        out.append(f"      🚪 Кабинет <b>{html.escape(room)}</b>")
    return out


def render_day(week: str, day: int, group: int, heading: str, highlight: Optional[int] = None) -> str:
    lessons = get_lessons(week, day, group)
    lines = [
        f"<b>{heading}</b>",
        f"{WEEK_TITLE[week]}  ·  👥 Группа {group}",
        DIVIDER,
        "",
    ]
    if all(l is None for l in lessons):
        lines += ["🎉 <b>Пар нет — можно отдыхать!</b>", "", "😎 Заслуженный выходной от учёбы"]
        return "\n".join(lines)

    for i, lesson in enumerate(lessons):
        s, e = PAIRS[i]
        head = f"{NUM[i]} <b>{fmt_t(s)} – {fmt_t(e)}</b>"
        if highlight == i:
            head += "   🟢 <i>идёт сейчас</i>"
        lines.append(head)
        lines += lesson_lines(lesson)
        lines.append("")
    return "\n".join(lines).rstrip()


def render_weekend(d: date, label: str, group: int) -> str:
    wk = week_type(d)
    return "\n".join(
        [
            f"<b>{label} · {DAYS[d.weekday()]}, {d:%d.%m}</b>",
            DIVIDER,
            "",
            "🎉 <b>Выходной — пар нет!</b>",
            "😴 Отдыхай, высыпайся и набирайся сил",
            "",
            f"📆 Ближайшая учебная неделя: {WEEK_TITLE[wk].lower()}",
            f"👥 Твоя группа: {group}",
        ]
    )


def render_week(week: str, group: int, is_current: bool) -> str:
    cur = "  ·  <i>текущая</i>" if is_current else ""
    lines = [
        "🗓 <b>Полное расписание</b>",
        f"{WEEK_TITLE[week]}{cur}  ·  👥 Группа {group}",
        DIVIDER,
        "",
    ]
    for d in range(5):
        lines.append(f"{DAY_EMOJI[d]} <b>{DAYS[d]}</b>")
        lessons = get_lessons(week, d, group)
        if all(l is None for l in lessons):
            lines.append("      🎉 <i>Пар нет</i>")
        else:
            for i, lesson in enumerate(lessons):
                if lesson is None:
                    lines.append(f"      {NUM[i]} —")
                else:
                    name, room = lesson
                    row = f"      {NUM[i]} {lesson_emoji(name)} {html.escape(name)}"
                    if room:
                        row += f"  ·  🚪 {html.escape(room)}"
                    lines.append(row)
        lines.append("")
    return "\n".join(lines).rstrip()


def render_times(now: datetime) -> str:
    lines = ["⏰ <b>Время пар</b>", DIVIDER, ""]
    for i, (s, e) in enumerate(PAIRS):
        lines.append(f"{NUM[i]} <b>{fmt_t(s)} – {fmt_t(e)}</b>")
        if i < len(PAIRS) - 1:
            gap = (
                datetime.combine(date.today(), PAIRS[i + 1][0])
                - datetime.combine(date.today(), e)
            ).seconds // 60
            lines.append(f"      ☕ <i>перемена {gap} мин</i>")
    lines += ["", DIVIDER, render_status(now)]
    return "\n".join(lines)


def render_status(now: datetime) -> str:
    if now.weekday() >= 5:
        return "🎉 Сегодня выходной"
    t = now.time()
    if t < PAIRS[0][0]:
        return f"🌅 Пары ещё не начались — первая в {fmt_t(PAIRS[0][0])}"
    if t >= PAIRS[-1][1]:
        return "🌙 Пары на сегодня закончились"
    for i, (s, e) in enumerate(PAIRS):
        if s <= t < e:
            left = (datetime.combine(now.date(), e) - datetime.combine(now.date(), t)).seconds // 60
            return f"🟢 Сейчас идёт <b>{i + 1}-я пара</b> · до конца ~{left} мин"
    for i in range(len(PAIRS) - 1):
        if PAIRS[i][1] <= t < PAIRS[i + 1][0]:
            return f"☕ Сейчас перемена · следующая пара в {fmt_t(PAIRS[i + 1][0])}"
    return ""


# ─────────────────────────────── БАЗА ДАННЫХ ─────────────────────────────

db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, grp INTEGER NOT NULL)")
db.commit()


def get_group(user_id: int) -> Optional[int]:
    row = db.execute("SELECT grp FROM users WHERE user_id = ?", (user_id,)).fetchone()
    return row[0] if row else None


def set_group(user_id: int, group: int) -> None:
    db.execute(
        "INSERT INTO users (user_id, grp) VALUES (?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET grp = excluded.grp",
        (user_id, group),
    )
    db.commit()


# ─────────────────────────────── КЛАВИАТУРЫ ──────────────────────────────


def btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def kb_menu(group: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [btn("📅 Сегодня", "today"), btn("🌅 Завтра", "tomorrow")],
            [btn("🗓 Полное расписание", "full")],
            [btn("⏰ Время пар", "times")],
            [btn(f"👥 Сменить группу (сейчас {group})", "chgroup")],
        ]
    )


def kb_group(with_back: bool) -> InlineKeyboardMarkup:
    rows = [[btn("1️⃣ Группа 1", "setg:1"), btn("2️⃣ Группа 2", "setg:2")]]
    if with_back:
        rows.append([btn("⬅️ Назад в меню", "menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_today(week: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [btn("🌅 Завтра", "tomorrow"), btn("🗓 Вся неделя", f"f:{week}:all")],
            [btn("🔄 Обновить", "today"), btn("🏠 Меню", "menu")],
        ]
    )


def kb_tomorrow(week: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [btn("📅 Сегодня", "today"), btn("🗓 Вся неделя", f"f:{week}:all")],
            [btn("🏠 Меню", "menu")],
        ]
    )


def kb_full(week: str, day: str) -> InlineKeyboardMarkup:
    # Переключатель недели
    week_row = [
        btn(("✅ " if week == w else "") + WEEK_SHORT[w], f"f:{w}:{day}")
        for w in ("odd", "even")
    ]
    # Дни недели
    day_row = [
        btn(f"•{DAYS_SHORT[i]}•" if day == str(i) else DAYS_SHORT[i], f"f:{week}:{i}")
        for i in range(5)
    ]
    all_row = [btn("✅ 📋 Вся неделя" if day == "all" else "📋 Вся неделя", f"f:{week}:all")]
    return InlineKeyboardMarkup(
        inline_keyboard=[week_row, day_row, all_row, [btn("🏠 Меню", "menu")]]
    )


def kb_times() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[btn("🔄 Обновить", "times"), btn("🏠 Меню", "menu")]]
    )


# ─────────────────────────────── ЭКРАНЫ ──────────────────────────────────

Screen = Tuple[str, InlineKeyboardMarkup]


def screen_group(first: bool) -> Screen:
    if first:
        text = (
            f"👋 <b>Привет! Я — {BOT_NAME}</b> 🎓\n\n"
            "Покажу пары, кабинеты и время звонков.\n"
            "Чётная и нечётная недели меняются автоматически 🔄\n\n"
            "Для начала выбери свою группу 👇"
        )
    else:
        text = "👥 <b>Выбор группы</b>\n\nВыбери свою группу 👇"
    return text, kb_group(with_back=not first)


def screen_menu(group: int) -> Screen:
    now = now_msk()
    wk = week_type(now.date())
    text = (
        f"🎓 <b>{BOT_NAME}</b>\n"
        f"{DIVIDER}\n"
        f"👥 Группа: <b>{group}</b>\n"
        f"📆 Сейчас: <b>{WEEK_TITLE[wk].split(' ', 1)[1].lower()}</b>\n"
        f"🕒 {DAYS[now.weekday()]}, {now:%d.%m.%Y}\n"
        f"{DIVIDER}\n\n"
        "Выбери, что показать 👇"
    )
    return text, kb_menu(group)


def screen_today(group: int) -> Screen:
    now = now_msk()
    d = now.date()
    wk = week_type(d)
    if d.weekday() >= 5:
        return render_weekend(d, "📅 Сегодня", group), kb_today(wk)
    heading = f"📅 Сегодня · {DAYS[d.weekday()]}, {d:%d.%m}"
    text = render_day(wk, d.weekday(), group, heading, highlight=current_pair_index(now))
    return text, kb_today(wk)


def screen_tomorrow(group: int) -> Screen:
    d = now_msk().date() + timedelta(days=1)
    wk = week_type(d)
    if d.weekday() >= 5:
        return render_weekend(d, "🌅 Завтра", group), kb_tomorrow(wk)
    heading = f"🌅 Завтра · {DAYS[d.weekday()]}, {d:%d.%m}"
    return render_day(wk, d.weekday(), group, heading), kb_tomorrow(wk)


def screen_full(group: int, week: str, day: str) -> Screen:
    current = week_type(now_msk().date())
    if day == "all":
        text = render_week(week, group, is_current=(week == current))
    else:
        d = int(day)
        heading = f"{DAY_EMOJI[d]} {DAYS[d]}"
        text = render_day(week, d, group, heading)
    return text, kb_full(week, day)


def screen_times() -> Screen:
    return render_times(now_msk()), kb_times()


def build_screen(action: str, user_id: int, args: Tuple[str, ...] = ()) -> Screen:
    group = get_group(user_id)

    if action == "times":
        return screen_times()
    if action == "chgroup":
        return screen_group(first=group is None)
    if group is None:
        return screen_group(first=True)

    if action == "today":
        return screen_today(group)
    if action == "tomorrow":
        return screen_tomorrow(group)
    if action == "full":
        wk = week_type(now_msk().date())
        return screen_full(group, wk, "all")
    if action == "f" and len(args) == 2 and args[0] in SCHEDULE:
        day = args[1]
        if day == "all" or (day.isdigit() and 0 <= int(day) <= 4):
            return screen_full(group, args[0], day)
    return screen_menu(group)


# ─────────────────────────────── ХЕНДЛЕРЫ ────────────────────────────────

router = Router()


async def show(event, text: str, kb: InlineKeyboardMarkup, toast: Optional[str] = None) -> None:
    if isinstance(event, CallbackQuery):
        try:
            await event.message.edit_text(text, reply_markup=kb)
        except TelegramBadRequest as e:
            if "message is not modified" not in str(e):
                raise
        await event.answer(toast)
    else:
        await event.answer(text, reply_markup=kb)


@router.message(CommandStart())
@router.message(Command("menu"))
async def cmd_menu(message: Message) -> None:
    text, kb = build_screen("menu", message.from_user.id)
    await show(message, text, kb)


@router.message(Command("today"))
async def cmd_today(message: Message) -> None:
    text, kb = build_screen("today", message.from_user.id)
    await show(message, text, kb)


@router.message(Command("tomorrow"))
async def cmd_tomorrow(message: Message) -> None:
    text, kb = build_screen("tomorrow", message.from_user.id)
    await show(message, text, kb)


@router.message(Command("times"))
async def cmd_times(message: Message) -> None:
    text, kb = build_screen("times", message.from_user.id)
    await show(message, text, kb)


@router.callback_query()
async def on_callback(cb: CallbackQuery) -> None:
    if cb.message is None:
        await cb.answer()
        return

    parts = (cb.data or "").split(":")
    action, args = parts[0], tuple(parts[1:])
    uid = cb.from_user.id

    # Выбор группы
    if action == "setg" and args and args[0] in ("1", "2"):
        set_group(uid, int(args[0]))
        text, kb = screen_menu(int(args[0]))
        await show(cb, text, kb, toast=f"✅ Группа {args[0]} выбрана")
        return

    text, kb = build_screen(action, uid, args)
    await show(cb, text, kb)


# ─────────────────────────────── HTTP-СЕРВЕР ─────────────────────────────


async def health(_request: web.Request) -> web.Response:
    return web.Response(text="OK", status=200)


async def start_http_server() -> web.AppRunner:
    """Лёгкий сервер: на любой путь и метод отвечает 200 OK."""
    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", health)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=PORT)
    await site.start()
    logging.info("HTTP-сервер запущен на порту %s", PORT)
    return runner


# ─────────────────────────────── ЗАПУСК ──────────────────────────────────


async def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit("❌ Не задан BOT_TOKEN. Получи токен у @BotFather и задай переменную окружения BOT_TOKEN.")

    logging.basicConfig(level=logging.INFO)

    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)

    await bot.set_my_commands(
        [
            BotCommand(command="menu", description="🏠 Главное меню"),
            BotCommand(command="today", description="📅 Пары на сегодня"),
            BotCommand(command="tomorrow", description="🌅 Пары на завтра"),
            BotCommand(command="times", description="⏰ Время пар"),
        ]
    )

    runner = await start_http_server()
    try:
        await dp.start_polling(bot)  # polling и HTTP-сервер работают параллельно
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
