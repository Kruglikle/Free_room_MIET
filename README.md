# Free Room MIET Telegram Bot

Telegram bot that finds free MIET rooms for a selected day and pair/time by aggregating schedules of all groups.

## Features
- Aggregates occupancy across all groups (not a single group).
- Builds groups list from MIET schedule API.
- Builds rooms catalog from all group schedules.
- TTL cache for schedule requests.
- Day/time mapping and time-to-pair conversion.
- Pagination for long room lists.

## Requirements
- Python 3.11+

Install dependencies:

```bash
python -m pip install -r requirements.txt
```

## Setup
Create `.env` based on `.env.example`:

```bash
copy .env.example .env
```

Set `BOT_TOKEN` in `.env`. If MIET is only reachable via your Wi-Fi interface, set `MIET_LOCAL_ADDR` to your local IP.

## Build data
```bash
python scripts/build_groups.py
python scripts/build_rooms.py
```

## Run bot
```bash
python -m src.bot
```

## Bot commands
- `/start`
- `/refresh_groups` (rebuilds groups list)
- `/refresh_rooms` (reloads rooms.json into memory)

## Notes
- If requests to `miet.ru` time out, check network restrictions and set `MIET_LOCAL_ADDR`.
- Rooms data is generated; `groups.json` and `rooms.json` are not committed.
