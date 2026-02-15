from __future__ import annotations

import logging
import re
from datetime import date, datetime, time
from typing import Any, Iterable

from .miet_api import MietAPI


logger = logging.getLogger(__name__)

RU_WEEKDAYS = [
    "Понедельник",
    "Вторник",
    "Среда",
    "Четверг",
    "Пятница",
    "Суббота",
    "Воскресенье",
]

TIME_RANGE_RE = re.compile(r"(\d{1,2}:\d{2})\s*[-–]\s*(\d{1,2}:\d{2})")
TIME_VALUE_RE = re.compile(r"^(\d{1,2}):(\d{2})$")


def _parse_time_value(value: str | None) -> time | None:
    if not value:
        return None
    match = TIME_VALUE_RE.match(value.strip())
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2))
    if hour > 23 or minute > 59:
        return None
    return time(hour, minute)


def _parse_time_range(value: str | None) -> tuple[time, time] | None:
    if not value:
        return None
    match = TIME_RANGE_RE.search(value)
    if not match:
        return None
    start = _parse_time_value(match.group(1))
    end = _parse_time_value(match.group(2))
    if not start or not end:
        return None
    return start, end


def normalize_times(times: Iterable[dict[str, Any]] | None) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for raw in times or []:
        code = raw.get("Code") or raw.get("code") or raw.get("ID") or raw.get("Id")
        if code is None:
            continue
        code_str = str(code)
        label = raw.get("Time") or raw.get("Name") or ""
        begin = raw.get("Begin") or raw.get("Start") or raw.get("TimeStart")
        end = raw.get("End") or raw.get("Finish") or raw.get("TimeEnd")

        start_time = _parse_time_value(str(begin)) if begin else None
        end_time = _parse_time_value(str(end)) if end else None

        if (not start_time or not end_time) and label:
            parsed = _parse_time_range(str(label))
            if parsed:
                start_time, end_time = parsed

        if not label and start_time and end_time:
            label = f"{start_time.strftime('%H:%M')}-{end_time.strftime('%H:%M')}"

        normalized.append(
            {
                "code": code_str,
                "label": label,
                "start": start_time,
                "end": end_time,
            }
        )
    return normalized


def time_to_code(times: Iterable[dict[str, Any]] | None, target: time) -> str | None:
    for entry in normalize_times(times):
        start = entry.get("start")
        end = entry.get("end")
        if not start or not end:
            continue
        if start <= target <= end:
            return entry["code"]
    return None


def extract_room_name(item: dict[str, Any]) -> str | None:
    room = item.get("Room")
    if isinstance(room, dict):
        return room.get("Name")
    if isinstance(room, str):
        return room
    return None


def extract_time_code(item: dict[str, Any]) -> str | None:
    time_info = item.get("Time")
    if isinstance(time_info, dict):
        code = time_info.get("Code") or time_info.get("code")
        return str(code) if code is not None else None
    if isinstance(time_info, str):
        return time_info
    code = item.get("TimeCode") or item.get("TimeID")
    return str(code) if code is not None else None


def extract_day(item: dict[str, Any]) -> tuple[str | None, int | None]:
    day_name = item.get("Day")
    day_num = item.get("DayNumber") or item.get("DayNum")
    if day_num is not None:
        try:
            day_num = int(day_num)
        except (TypeError, ValueError):
            day_num = None
    return day_name, day_num


class DayMapper:
    def __init__(self) -> None:
        self._day_to_number: dict[str, int] = {}

    def update_from_items(self, items: Iterable[dict[str, Any]] | None) -> None:
        stats: dict[str, dict[int, int]] = {}
        for item in items or []:
            day_name, day_num = extract_day(item)
            if not day_name or day_num is None:
                continue
            stats.setdefault(day_name, {})
            stats[day_name][day_num] = stats[day_name].get(day_num, 0) + 1

        if not stats:
            return

        for day_name, counts in stats.items():
            best_num = max(counts.items(), key=lambda kv: kv[1])[0]
            self._day_to_number[day_name] = best_num

    def update_from_schedules(self, schedules: Iterable[dict[str, Any] | None]) -> None:
        for schedule in schedules:
            if schedule:
                self.update_from_items(schedule.get("Data"))

    def date_to_api_day(self, target_date: date) -> tuple[str, int]:
        day_name = RU_WEEKDAYS[target_date.weekday()]
        day_num = self._day_to_number.get(day_name, target_date.weekday() + 1)
        return day_name, day_num


class Scheduler:
    def __init__(
        self,
        api: MietAPI,
        groups: list[str],
        rooms: list[str],
        day_mapper: DayMapper | None = None,
    ) -> None:
        self.api = api
        self.groups = groups
        self.rooms = rooms
        self.day_mapper = day_mapper or DayMapper()

    async def get_reference_schedule(self) -> dict[str, Any] | None:
        if not self.groups:
            return None
        return await self.api.fetch_one(self.groups[0])

    async def get_times(self) -> list[dict[str, Any]]:
        schedule = await self.get_reference_schedule()
        if not schedule:
            return []
        return normalize_times(schedule.get("Times"))

    async def map_date(self, target_date: date) -> tuple[str, int]:
        schedule = await self.get_reference_schedule()
        if schedule:
            self.day_mapper.update_from_items(schedule.get("Data"))
        return self.day_mapper.date_to_api_day(target_date)

    async def aggregate_occupied(
        self,
        day_name: str,
        day_number: int | None,
        time_code: str,
    ) -> tuple[set[str], int]:
        schedules = await self.api.fetch_all(self.groups)
        self.day_mapper.update_from_schedules(schedules)
        occupied: set[str] = set()
        success = 0
        for schedule in schedules:
            if not schedule:
                continue
            success += 1
            for item in schedule.get("Data", []):
                item_day, item_day_num = extract_day(item)
                if item_day and item_day != day_name:
                    continue
                if day_number is not None and item_day_num is not None and item_day_num != day_number:
                    continue
                if extract_time_code(item) != str(time_code):
                    continue
                room_name = extract_room_name(item)
                if room_name:
                    occupied.add(room_name)
        return occupied, success

    def free_rooms(self, occupied: Iterable[str]) -> list[str]:
        occupied_set = {r for r in occupied if r}
        return sorted(set(self.rooms) - occupied_set)


def parse_date(value: str) -> date | None:
    value = value.strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d.%m"):
        try:
            parsed = datetime.strptime(value, fmt).date()
            if fmt == "%d.%m":
                parsed = parsed.replace(year=date.today().year)
            return parsed
        except ValueError:
            continue
    return None


def parse_time(value: str) -> time | None:
    value = value.strip()
    match = TIME_VALUE_RE.match(value)
    if not match:
        return None
    return _parse_time_value(value)


def filter_rooms_by_prefix(rooms: Iterable[str], prefix: str | None) -> list[str]:
    if not prefix or prefix == "all":
        return sorted(rooms)
    return sorted([room for room in rooms if room.startswith(prefix)])


def corpus_prefixes(rooms: Iterable[str]) -> list[str]:
    prefixes: set[str] = set()
    for room in rooms:
        if len(room) >= 2 and room[:2].isdigit():
            prefixes.add(room[:2])
    return sorted(prefixes)


def paginate(items: list[str], page: int, page_size: int) -> list[str]:
    start = page * page_size
    end = start + page_size
    return items[start:end]
