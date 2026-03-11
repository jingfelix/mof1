from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, TypeVar

import pandas as pd

UTC = timezone.utc
_T = TypeVar("_T")


@dataclass(frozen=True)
class EventOption:
    key: str
    year: int
    round_number: int
    name: str
    location: str
    event_date_utc: datetime

    @property
    def label(self) -> str:
        return f"R{self.round_number:02d} {self.name}"


@dataclass(frozen=True)
class SessionSelection:
    key: str
    year: int
    round_number: int
    event_name: str
    session_name: str
    start_utc: datetime

    @property
    def label(self) -> str:
        return f"{self.session_name} ({format_datetime_utc(self.start_utc)})"


@dataclass(frozen=True)
class DriverSnapshot:
    position: int | None
    code: str
    team: str
    current_lap: str
    best_lap: str
    current_sectors: tuple[str, str, str]
    best_sectors: tuple[str, str, str]
    current_sector_statuses: tuple[str, str, str]
    best_sector_statuses: tuple[str, str, str]
    current_lap_status: str
    best_lap_status: str
    status: str
    current_tyre: str = "-"
    current_tyre_new: bool | None = None
    current_tyre_laps: int | None = None
    used_tyre_sets: int | None = None
    used_tyre_compounds: tuple[str, ...] = ()
    used_tyre_stints: tuple[tuple[str, int | None], ...] = ()
    current_mini_sector_statuses: tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]] = (
        (),
        (),
        (),
    )


@dataclass(frozen=True)
class SessionSnapshot:
    title: str
    subtitle: str
    badge: str
    note: str
    summary_lines: tuple[str, ...]
    drivers: tuple[DriverSnapshot, ...]
    loaded_at_utc: datetime
    error: str | None = None


@dataclass(frozen=True)
class CurrentContext:
    target: SessionSelection
    latest_started: SessionSelection | None
    next_session: SessionSelection | None
    badge: str
    note: str


@dataclass(frozen=True)
class BootstrapPayload:
    current_context: CurrentContext
    current_snapshot: SessionSnapshot
    history_year: int
    history_events: tuple[EventOption, ...]
    history_event_key: str
    history_sessions: tuple[SessionSelection, ...]
    history_session_key: str
    history_snapshot: SessionSnapshot


@dataclass(frozen=True)
class HistoryCatalogPayload:
    year: int
    events: tuple[EventOption, ...]
    event_key: str
    sessions: tuple[SessionSelection, ...]
    session_key: str
    snapshot: SessionSnapshot


@dataclass(frozen=True)
class HistoryEventPayload:
    year: int
    event_key: str
    sessions: tuple[SessionSelection, ...]
    session_key: str
    snapshot: SessionSnapshot


def format_timedelta(value: object) -> str:
    if value is None or pd.isna(value):
        return "-"

    delta = pd.Timedelta(value)
    total_milliseconds = int(round(delta.total_seconds() * 1000))
    if total_milliseconds < 0:
        total_milliseconds = 0

    minutes, milliseconds = divmod(total_milliseconds, 60_000)
    seconds, milliseconds = divmod(milliseconds, 1_000)
    hours, minutes = divmod(minutes, 60)

    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}.{milliseconds:03d}"
    if minutes:
        return f"{minutes}:{seconds:02d}.{milliseconds:03d}"
    return f"{seconds}.{milliseconds:03d}"


def format_datetime_utc(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")


def coerce_utc_datetime(value: object) -> datetime:
    timestamp = pd.Timestamp(cast_to_any(value))
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    else:
        timestamp = timestamp.tz_convert("UTC")
    return timestamp.to_pydatetime()


def split_drivers(
    drivers: Iterable[DriverSnapshot],
) -> tuple[list[DriverSnapshot], list[DriverSnapshot]]:
    driver_list = list(drivers)
    midpoint = (len(driver_list) + 1) // 2
    return driver_list[:midpoint], driver_list[midpoint:]


def uses_fastest_lap_order(session_name: str) -> bool:
    normalized = session_name.lower()
    return "practice" in normalized or "qualifying" in normalized or "sprint" in normalized


def as_triplet(values: Iterable[_T]) -> tuple[_T, _T, _T]:
    first, second, third = values
    return (first, second, third)


def cast_to_any(value: object) -> Any:
    return value
