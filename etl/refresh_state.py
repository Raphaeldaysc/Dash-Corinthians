"""Controle de "uma atualização por dia" (dia do calendário no horário de Brasília)."""
from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta, timezone, tzinfo

from . import storage
from .config import TIMEZONE

STATE_KEY = "refresh_state"


def _local_tz() -> tzinfo:
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(TIMEZONE)
    except Exception:
        # Sem base IANA (Windows sem tzdata): Brasília não tem horário de verão desde 2019
        return timezone(timedelta(hours=-3))


LOCAL_TZ = _local_tz()


def now_local() -> datetime:
    return datetime.now(LOCAL_TZ)


def today_local() -> date:
    return now_local().date()


def next_allowed() -> datetime:
    return datetime.combine(today_local() + timedelta(days=1), time.min, tzinfo=LOCAL_TZ)


def read_state() -> dict[str, object]:
    raw = storage.read(STATE_KEY)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def refreshed_today() -> bool:
    return read_state().get("day") == today_local().isoformat()


def mark_refreshed(duration_s: float | None = None) -> None:
    state = {
        "day": today_local().isoformat(),
        "finished_at": now_local().isoformat(),
        "duration_s": round(duration_s, 1) if duration_s is not None else None,
    }
    storage.write(STATE_KEY, json.dumps(state))
