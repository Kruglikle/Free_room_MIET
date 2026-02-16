from __future__ import annotations
from .rooms import load_rooms, ensure_rooms

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import date, timedelta
from math import ceil

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from dotenv import load_dotenv

from .cache import TTLCache
from .groups import load_groups, refresh_groups
from .miet_api import MietAPI
from .rooms import ensure_rooms, load_rooms
from .scheduler import (
    Scheduler,
    corpus_prefixes,
    filter_rooms_by_prefix,
    paginate,
    parse_date,
    parse_time,
    time_to_code,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
logger = logging.getLogger(__name__)

PAGE_SIZE = 40


class UserFlow(StatesGroup):
    choosing_day = State()
    choosing_date = State()
    choosing_pair = State()
    choosing_time = State()
    choosing_corpus = State()


@dataclass
class AppContext:
    scheduler: Scheduler


router = Router()
ctx: AppContext | None = None


def day_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Сегодня", callback_data="day:today"),
                InlineKeyboardButton(text="Завтра", callback_data="day:tomorrow"),
            ],
            [InlineKeyboardButton(text="Выбрать дату", callback_data="day:pick")],
        ]
    )


def pairs_keyboard(times: list[dict[str, str]]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for entry in times:
        code = entry.get("code")
        label = entry.get("label") or ""
        if not code:
            continue
        text = f"{code}. {label}" if label else f"Пара {code}"
        row.append(InlineKeyboardButton(text=text, callback_data=f"pair:{code}"))
        if len(row) >= 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="Ввести время", callback_data="pair:time")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def corpus_keyboard(prefixes: list[str]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for prefix in prefixes:
        row.append(InlineKeyboardButton(text=f"{prefix}xx", callback_data=f"corpus:{prefix}"))
        if len(row) >= 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="Все", callback_data="corpus:all")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def results_keyboard(page: int, total_pages: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    nav_row: list[InlineKeyboardButton] = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="⬅️", callback_data=f"page:{page - 1}"))
    if page + 1 < total_pages:
        nav_row.append(InlineKeyboardButton(text="➡️", callback_data=f"page:{page + 1}"))
    if nav_row:
        rows.append(nav_row)
    rows.append(
        [
            InlineKeyboardButton(text="Обновить", callback_data="action:refresh"),
            InlineKeyboardButton(text="Изменить день", callback_data="action:change_day"),
            InlineKeyboardButton(text="Изменить пару", callback_data="action:change_pair"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def prompt_day(target: Message | CallbackQuery) -> None:
    if isinstance(target, CallbackQuery):
        await target.message.answer("Выберите день:", reply_markup=day_keyboard())
        await target.answer()
    else:
        await target.answer("Выберите день:", reply_markup=day_keyboard())


async def prompt_pairs(target: Message | CallbackQuery, state: FSMContext) -> None:
    if not ctx:
        return
    if not ctx.scheduler.groups:
        text = "Список групп пуст. Запустите /refresh_groups или scripts/build_groups.py."
        if isinstance(target, CallbackQuery):
            await target.message.answer(text)
            await target.answer()
        else:
            await target.answer(text)
        return
    times = await ctx.scheduler.get_times()
    if not times:
        text = "Не удалось загрузить список пар. Попробуйте позже."
        if isinstance(target, CallbackQuery):
            await target.message.answer(text)
            await target.answer()
        else:
            await target.answer(text)
        return
    await state.set_state(UserFlow.choosing_pair)
    if isinstance(target, CallbackQuery):
        await target.message.answer("Выберите пару или введите время:", reply_markup=pairs_keyboard(times))
        await target.answer()
    else:
        await target.answer("Выберите пару или введите время:", reply_markup=pairs_keyboard(times))


async def prompt_corpus(target: Message | CallbackQuery, state: FSMContext) -> None:
    if not ctx:
        return

    # Автогенерация rooms, если пусто
    if not ctx.scheduler.rooms:
        ctx.scheduler.rooms = await ensure_rooms()

    if not ctx.scheduler.rooms:
        text = "Список аудиторий пуст (не удалось загрузить rooms). Попробуйте позже."
        if isinstance(target, CallbackQuery):
            await target.message.answer(text)
            await target.answer()
        else:
            await target.answer(text)
        return

    prefixes = corpus_prefixes(ctx.scheduler.rooms)
    await state.set_state(UserFlow.choosing_corpus)
    if isinstance(target, CallbackQuery):
        await target.message.answer("Выберите корпус:", reply_markup=corpus_keyboard(prefixes))
        await target.answer()
    else:
        await target.answer("Выберите корпус:", reply_markup=corpus_keyboard(prefixes))


async def show_results(
    target: Message | CallbackQuery,
    state: FSMContext,
    *,
    page: int = 0,
    refresh: bool = False,
) -> None:
    if not ctx:
        return

    # Автогенерация rooms, если пусто
    if not ctx.scheduler.rooms:
        ctx.scheduler.rooms = await ensure_rooms()

    if not ctx.scheduler.rooms:
        text = "Список аудиторий пуст (не удалось загрузить rooms). Попробуйте позже."
        if isinstance(target, CallbackQuery):
            await target.message.answer(text)
            await target.answer()
        else:
            await target.answer(text)
        return

    data = await state.get_data()
    day_name = data.get("day_name")
    day_number = data.get("day_number")
    time_code = data.get("time_code")
    target_date = data.get("target_date")
    corpus_prefix = data.get("corpus_prefix", "all")

    if not (day_name and time_code):
        await prompt_day(target)
        return

    if refresh or "rooms_list" not in data:
        occupied, success = await ctx.scheduler.aggregate_occupied(day_name, day_number, str(time_code))
        if success == 0:
            text = "API недоступен. Попробуйте позже."
            if isinstance(target, CallbackQuery):
                await target.message.answer(text)
                await target.answer()
            else:
                await target.answer(text)
            return
        free_rooms = ctx.scheduler.free_rooms(occupied)
        filtered = filter_rooms_by_prefix(free_rooms, corpus_prefix)
        await state.update_data(rooms_list=filtered)
    else:
        filtered = data.get("rooms_list", [])

    total_pages = max(1, ceil(len(filtered) / PAGE_SIZE))
    page = max(0, min(page, total_pages - 1))
    page_items = paginate(filtered, page, PAGE_SIZE)

    header = [
        "Свободные аудитории",
        f"День: {day_name} ({target_date})",
        f"Пара: {time_code}",
    ]
    if corpus_prefix and corpus_prefix != "all":
        header.append(f"Корпус: {corpus_prefix}xx")
    header.append(f"Всего: {len(filtered)}")
    body = "\n".join(page_items) if page_items else "Нет свободных аудиторий."
    text = "\n".join(header) + "\n\n" + body

    keyboard = results_keyboard(page, total_pages)
    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, reply_markup=keyboard)
        await target.answer()
    else:
        await target.answer(text, reply_markup=keyboard)


@router.message(CommandStart())
async def start_handler(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(UserFlow.choosing_day)
    await message.answer("Привет! Я помогу найти свободные аудитории МИЭТ.")
    await prompt_day(message)


@router.message(Command("refresh_groups"))
async def refresh_groups_handler(message: Message) -> None:
    if not ctx:
        return
    groups = await refresh_groups()
    if not groups:
        await message.answer("Не удалось обновить список групп.")
        return
    ctx.scheduler.groups = groups
    await message.answer(f"Группы обновлены: {len(groups)}")


@router.message(Command("refresh_rooms"))
async def refresh_rooms_handler(message: Message) -> None:
    if not ctx:
        return

    rooms = await ensure_rooms()
    if not rooms:
        await message.answer("Не удалось собрать rooms (rooms.json всё ещё пуст). Попробуйте позже.")
        return

    ctx.scheduler.rooms = rooms
    await message.answer(f"Аудитории обновлены: {len(rooms)}")


@router.callback_query(F.data.startswith("day:"))
async def day_choice(callback: CallbackQuery, state: FSMContext) -> None:
    if not ctx:
        return
    action = callback.data.split(":", 1)[1]
    if action == "pick":
        await state.set_state(UserFlow.choosing_date)
        await callback.message.answer("Введите дату (YYYY-MM-DD или DD.MM):")
        await callback.answer()
        return
    target_date = date.today() if action == "today" else date.today() + timedelta(days=1)
    day_name, day_number = await ctx.scheduler.map_date(target_date)
    await state.update_data(
        target_date=target_date.isoformat(),
        day_name=day_name,
        day_number=day_number,
    )
    await prompt_pairs(callback, state)


@router.message(UserFlow.choosing_date)
async def date_input(message: Message, state: FSMContext) -> None:
    if not ctx:
        return
    target_date = parse_date(message.text or "")
    if not target_date:
        await message.answer("Не распознал дату. Пример: 2025-02-15 или 15.02.")
        return
    day_name, day_number = await ctx.scheduler.map_date(target_date)
    await state.update_data(
        target_date=target_date.isoformat(),
        day_name=day_name,
        day_number=day_number,
    )
    await prompt_pairs(message, state)


@router.callback_query(F.data.startswith("pair:"))
async def pair_choice(callback: CallbackQuery, state: FSMContext) -> None:
    action = callback.data.split(":", 1)[1]
    if action == "time":
        await state.set_state(UserFlow.choosing_time)
        await callback.message.answer("Введите время (HH:MM):")
        await callback.answer()
        return
    await state.update_data(time_code=action)
    await prompt_corpus(callback, state)


@router.message(UserFlow.choosing_time)
async def time_input(message: Message, state: FSMContext) -> None:
    if not ctx:
        return
    parsed_time = parse_time(message.text or "")
    if not parsed_time:
        await message.answer("Не распознал время. Пример: 12:10")
        return
    times = await ctx.scheduler.get_times()
    if not times:
        await message.answer("Не удалось загрузить список пар. Попробуйте позже.")
        return
    code = time_to_code(times, parsed_time)
    if not code:
        await message.answer("Время вне диапазона пар.")
        return
    await state.update_data(time_code=code)
    await prompt_corpus(message, state)


@router.callback_query(F.data.startswith("corpus:"))
async def corpus_choice(callback: CallbackQuery, state: FSMContext) -> None:
    prefix = callback.data.split(":", 1)[1]
    await state.update_data(corpus_prefix=prefix)
    await show_results(callback, state, refresh=True)


@router.callback_query(F.data.startswith("page:"))
async def page_choice(callback: CallbackQuery, state: FSMContext) -> None:
    page = int(callback.data.split(":", 1)[1])
    await show_results(callback, state, page=page)


@router.callback_query(F.data.startswith("action:"))
async def action_choice(callback: CallbackQuery, state: FSMContext) -> None:
    action = callback.data.split(":", 1)[1]
    if action == "refresh":
        await show_results(callback, state, refresh=True)
        return
    if action == "change_day":
        await state.set_state(UserFlow.choosing_day)
        await prompt_day(callback)
        return
    if action == "change_pair":
        await prompt_pairs(callback, state)
        return


async def init_context() -> AppContext:
    groups = load_groups()
    if not groups:
        groups = await refresh_groups(allow_guess=False)
        if not groups:
            logger.warning("groups.json not found or empty; use /refresh_groups or scripts/build_groups.py")

    # ВАЖНО: rooms генерим автоматически, если пусто
    rooms = await ensure_rooms()
    if not rooms:
        logger.warning("rooms.json not found or empty; rooms features will be unavailable until built")

    cache_ttl = int(os.getenv("MIET_CACHE_TTL", "120"))
    max_concurrency = int(os.getenv("MIET_MAX_CONCURRENCY", "10"))
    api = MietAPI(concurrency=max_concurrency, cache=TTLCache(ttl_seconds=cache_ttl))
    scheduler = Scheduler(api=api, groups=groups, rooms=rooms)
    return AppContext(scheduler=scheduler)


async def main() -> None:
    load_dotenv()
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN is not set")

    global ctx
    ctx = await init_context()

    bot = Bot(token=token)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
