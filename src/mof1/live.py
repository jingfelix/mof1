from __future__ import annotations

import copy
import json
import time
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from threading import Event
from typing import Any

import requests
from websocket import WebSocketTimeoutException, create_connection

from .models import (
    DriverSnapshot,
    SessionSnapshot,
    as_triplet,
    format_datetime_utc,
    uses_fastest_lap_order,
)

UTC = timezone.utc
_SIGNALR_SEPARATOR = "\x1e"
_NEGOTIATE_URL = "https://livetiming.formula1.com/signalrcore/negotiate?negotiateVersion=1"
_WEBSOCKET_URL = "wss://livetiming.formula1.com/signalrcore?id={token}"
_ORIGIN = "https://livetiming.formula1.com"
_LIVE_TOPICS = (
    "DriverList",
    "ExtrapolatedClock",
    "LapCount",
    "TimingAppData",
    "SessionInfo",
    "WeatherData",
    "TrackStatus",
    "TimingData",
    "TimingStats",
    "RaceControlMessages",
)


@dataclass
class LiveTimingState:
    topics: dict[str, Any] = field(default_factory=dict)
    received_at_utc: datetime | None = None

    def apply_snapshot(
        self, payload: dict[str, Any], *, received_at_utc: datetime | None = None
    ) -> None:
        for topic, data in payload.items():
            self.apply_topic(topic, data, received_at_utc=received_at_utc)

    def apply_topic(
        self, topic: str, data: Any, *, received_at_utc: datetime | None = None
    ) -> None:
        self.topics[topic] = _deep_merge(self.topics.get(topic), data)
        self.received_at_utc = received_at_utc or datetime.now(UTC)

    def build_snapshot(self) -> SessionSnapshot | None:
        session_info = _as_dict(self.topics.get("SessionInfo"))
        if not session_info:
            return None

        meeting = _as_dict(session_info.get("Meeting"))
        session_name = _non_empty_text(session_info.get("Name")) or "Current Session"
        event_name = _non_empty_text(meeting.get("Name")) or "Unknown Event"
        year = _session_year(session_info)
        start_utc = _session_start_utc(session_info)

        timing_data = _as_dict(self.topics.get("TimingData"))
        timing_lines = _as_dict(timing_data.get("Lines"))
        timing_app_data = _as_dict(self.topics.get("TimingAppData"))
        timing_app_lines = _as_dict(timing_app_data.get("Lines"))
        timing_stats = _as_dict(self.topics.get("TimingStats"))
        stats_lines = _as_dict(timing_stats.get("Lines"))
        driver_list = _as_dict(self.topics.get("DriverList"))

        raw_rows: list[dict[str, Any]] = []
        for driver_number in sorted(
            _data_keys(driver_list) | _data_keys(timing_lines) | _data_keys(stats_lines),
            key=lambda value: (_coerce_int(value) is None, _coerce_int(value) or 999, str(value)),
        ):
            info = _as_dict(driver_list.get(driver_number))
            timing = _as_dict(timing_lines.get(driver_number))
            timing_app = _as_dict(timing_app_lines.get(driver_number))
            stats = _as_dict(stats_lines.get(driver_number))
            stints = [_as_dict(stint) for stint in _ordered_items(timing_app.get("Stints"))]
            current_stint = stints[-1] if stints else {}
            used_compounds = tuple(
                compound
                for compound in (_short_compound(stint.get("Compound")) for stint in stints)
                if compound != "-"
            )
            used_stints = tuple(
                (
                    _short_compound(stint.get("Compound")),
                    _coerce_int(stint.get("TotalLaps")),
                )
                for stint in stints
                if _short_compound(stint.get("Compound")) != "-"
            )

            current_sector_metrics = _metric_triplet(timing.get("Sectors"))
            best_sector_metrics = _metric_triplet(stats.get("BestSectors"))
            current_lap_metric = timing.get("LastLapTime")
            best_lap_metric = stats.get("PersonalBestLapTime") or timing.get("BestLapTime")

            code = (
                _non_empty_text(info.get("Tla"))
                or _non_empty_text(info.get("BroadcastName"))
                or _non_empty_text(timing.get("RacingNumber"))
                or str(driver_number)
            )
            position = (
                _coerce_int(timing.get("Position"))
                or _coerce_int(timing.get("Line"))
                or _coerce_int(info.get("Line"))
            )

            raw_rows.append(
                {
                    "code": code,
                    "team": _non_empty_text(info.get("TeamName")) or "-",
                    "position": position,
                    "status": _driver_status(timing),
                    "best_lap_rank": _metric_rank(best_lap_metric),
                    "best_sector_ranks": tuple(
                        _metric_rank(metric) for metric in best_sector_metrics
                    ),
                    "current_lap_metric": current_lap_metric,
                    "best_lap_metric": best_lap_metric,
                    "current_lap_time": _metric_timedelta(current_lap_metric),
                    "best_lap_time": _metric_timedelta(best_lap_metric),
                    "current_sectors_metrics": current_sector_metrics,
                    "best_sectors_metrics": best_sector_metrics,
                    "current_mini_sector_statuses": _mini_sector_triplet(timing.get("Sectors")),
                    "current_sector_times": tuple(
                        _metric_timedelta(metric) for metric in current_sector_metrics
                    ),
                    "best_sector_times": tuple(
                        _metric_timedelta(metric) for metric in best_sector_metrics
                    ),
                    "current_tyre": _short_compound(current_stint.get("Compound")),
                    "current_tyre_laps": _coerce_int(current_stint.get("TotalLaps")),
                    "current_tyre_new": _coerce_bool(current_stint.get("New")),
                    "used_tyre_sets": len(stints) if stints else None,
                    "used_tyre_compounds": used_compounds,
                    "used_tyre_stints": used_stints,
                }
            )

        session_fastest_lap = _best_time(row["best_lap_time"] for row in raw_rows)
        session_fastest_sectors = as_triplet(
            _best_time(row["best_sector_times"][index] for row in raw_rows) for index in range(3)
        )

        if uses_fastest_lap_order(session_name):
            raw_rows.sort(
                key=lambda row: (
                    _fastest_order_key(row["best_lap_rank"], row["best_lap_time"]),
                    row["position"] if row["position"] is not None else 999,
                    row["code"],
                )
            )
        else:
            raw_rows.sort(
                key=lambda row: (
                    row["position"] if row["position"] is not None else 999,
                    row["code"],
                )
            )

        drivers: list[DriverSnapshot] = []
        for index, row in enumerate(raw_rows, start=1):
            display_position = index if uses_fastest_lap_order(session_name) else row["position"]
            drivers.append(
                DriverSnapshot(
                    position=display_position,
                    code=row["code"],
                    team=row["team"],
                    current_lap=_metric_text(row["current_lap_metric"]),
                    best_lap=_metric_text(row["best_lap_metric"]),
                    current_sectors=as_triplet(
                        _metric_text(metric) for metric in row["current_sectors_metrics"]
                    ),
                    best_sectors=as_triplet(
                        _metric_text(metric) for metric in row["best_sectors_metrics"]
                    ),
                    current_sector_statuses=as_triplet(
                        _current_metric_status(
                            row["current_sectors_metrics"][sector_index],
                            row["best_sector_times"][sector_index],
                            session_fastest_sectors[sector_index],
                        )
                        for sector_index in range(3)
                    ),
                    best_sector_statuses=as_triplet(
                        _best_metric_status(
                            row["best_sector_times"][sector_index],
                            session_fastest_sectors[sector_index],
                        )
                        for sector_index in range(3)
                    ),
                    current_lap_status=_current_metric_status(
                        row["current_lap_metric"],
                        row["best_lap_time"],
                        session_fastest_lap,
                    ),
                    best_lap_status=_best_metric_status(
                        row["best_lap_time"],
                        session_fastest_lap,
                    ),
                    status=row["status"],
                    current_tyre=row["current_tyre"],
                    current_tyre_new=row["current_tyre_new"],
                    current_tyre_laps=row["current_tyre_laps"],
                    used_tyre_sets=row["used_tyre_sets"],
                    used_tyre_compounds=row["used_tyre_compounds"],
                    used_tyre_stints=row["used_tyre_stints"],
                    current_mini_sector_statuses=row["current_mini_sector_statuses"],
                )
            )

        subtitle = session_name
        if start_utc is not None:
            subtitle = f"{session_name} | {format_datetime_utc(start_utc)}"

        summary_lines = _summary_lines(
            session_name=session_name,
            raw_rows=raw_rows,
            track_status=_as_dict(self.topics.get("TrackStatus")),
            session_info=session_info,
            extrapolated_clock=_as_dict(self.topics.get("ExtrapolatedClock")),
            lap_count=_as_dict(self.topics.get("LapCount")),
            weather=_as_dict(self.topics.get("WeatherData")),
            race_control=_as_dict(self.topics.get("RaceControlMessages")),
        )

        return SessionSnapshot(
            title=f"{year} {event_name}".strip(),
            subtitle=subtitle,
            badge=(_non_empty_text(session_info.get("SessionStatus")) or "LIVE").upper(),
            note=f"Live timing feed | Updated at {format_datetime_utc(self.received_at_utc or datetime.now(UTC))}.",
            summary_lines=summary_lines,
            drivers=tuple(drivers),
            loaded_at_utc=self.received_at_utc or datetime.now(UTC),
            error=None,
        )


class LiveTimingStream:
    def __init__(
        self,
        *,
        render_interval_seconds: float = 0.2,
        reconnect_delay_seconds: float = 3.0,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.render_interval_seconds = render_interval_seconds
        self.reconnect_delay_seconds = reconnect_delay_seconds
        self.timeout_seconds = timeout_seconds

    def run(
        self,
        stop_event: Event,
        *,
        on_snapshot: Callable[[SessionSnapshot], None],
        on_status: Callable[[str | None], None] | None = None,
    ) -> None:
        while not stop_event.is_set():
            try:
                self._run_once(stop_event, on_snapshot=on_snapshot, on_status=on_status)
                return
            except Exception as exc:
                if stop_event.is_set():
                    return
                if on_status is not None:
                    on_status(f"Current live feed unavailable: {exc}. Retrying...")
                if stop_event.wait(self.reconnect_delay_seconds):
                    return

    def _run_once(
        self,
        stop_event: Event,
        *,
        on_snapshot: Callable[[SessionSnapshot], None],
        on_status: Callable[[str | None], None] | None = None,
    ) -> None:
        if on_status is not None:
            on_status("Connecting current live feed...")

        session = requests.Session()
        websocket = None

        try:
            response = session.post(_NEGOTIATE_URL, timeout=self.timeout_seconds)
            response.raise_for_status()
            payload = response.json()
            token = payload["connectionToken"]
            cookie_header = "; ".join(
                f"{name}={value}" for name, value in session.cookies.get_dict().items()
            )
            headers = [f"Cookie: {cookie_header}"] if cookie_header else None

            websocket = create_connection(
                _WEBSOCKET_URL.format(token=urllib.parse.quote(token, safe="")),
                timeout=self.timeout_seconds,
                origin=_ORIGIN,
                header=headers,
            )
            websocket.settimeout(1.0)

            websocket.send(json.dumps({"protocol": "json", "version": 1}) + _SIGNALR_SEPARATOR)
            self._discard_handshake_ack(websocket)
            websocket.send(
                json.dumps(
                    {
                        "arguments": [list(_LIVE_TOPICS)],
                        "invocationId": "0",
                        "target": "Subscribe",
                        "type": 1,
                    }
                )
                + _SIGNALR_SEPARATOR
            )

            state = LiveTimingState()
            last_emit_monotonic = 0.0

            while not stop_event.is_set():
                try:
                    payload = websocket.recv()
                except WebSocketTimeoutException:
                    continue

                for message in _iter_signalr_messages(payload):
                    frame = json.loads(message)
                    frame_type = frame.get("type")

                    if frame_type == 6:
                        continue
                    if frame_type == 3 and frame.get("invocationId") == "0":
                        state.apply_snapshot(
                            _as_dict(frame.get("result")),
                            received_at_utc=datetime.now(UTC),
                        )
                        snapshot = state.build_snapshot()
                        if snapshot is not None:
                            on_snapshot(snapshot)
                            last_emit_monotonic = time.monotonic()
                            if on_status is not None:
                                on_status(None)
                        continue
                    if frame_type == 7:
                        raise RuntimeError(frame.get("error") or "Live feed closed by server.")
                    if frame_type != 1 or frame.get("target") != "feed":
                        continue

                    arguments = frame.get("arguments") or []
                    if len(arguments) != 3:
                        continue

                    topic, update, timestamp = arguments
                    state.apply_topic(
                        str(topic),
                        update,
                        received_at_utc=_coerce_feed_timestamp(timestamp),
                    )

                    now = time.monotonic()
                    if now - last_emit_monotonic < self.render_interval_seconds:
                        continue

                    snapshot = state.build_snapshot()
                    if snapshot is None:
                        continue
                    on_snapshot(snapshot)
                    last_emit_monotonic = now
                    if on_status is not None:
                        on_status(None)
        finally:
            if websocket is not None:
                websocket.close()
            session.close()

    @staticmethod
    def _discard_handshake_ack(websocket: Any) -> None:
        raw = websocket.recv()
        for message in _iter_signalr_messages(raw):
            if message == "{}":
                return


def _summary_lines(
    *,
    session_name: str,
    raw_rows: list[dict[str, Any]],
    track_status: dict[str, Any],
    session_info: dict[str, Any],
    extrapolated_clock: dict[str, Any],
    lap_count: dict[str, Any],
    weather: dict[str, Any],
    race_control: dict[str, Any],
) -> tuple[str, ...]:
    lines: list[str] = []

    session_state = _non_empty_text(session_info.get("SessionStatus"))
    track_message = _non_empty_text(track_status.get("Message"))
    progress_parts = []
    remaining = _non_empty_text(extrapolated_clock.get("Remaining"))
    if remaining and remaining != "00:00:00":
        progress_parts.append(f"Remain {remaining}")
    lap_progress = _lap_progress_text(lap_count)
    if lap_progress:
        progress_parts.append(lap_progress)

    if session_state or track_message or progress_parts:
        parts = []
        if session_state:
            parts.append(f"Session: {session_state}")
        if track_message:
            parts.append(f"Track: {track_message}")
        parts.extend(progress_parts)
        lines.append(" | ".join(parts))

    weather_line = _weather_text(weather)
    if weather_line:
        lines.append(weather_line)

    if raw_rows:
        if uses_fastest_lap_order(session_name):
            fastest = _best_lap_row(raw_rows)
            if fastest is not None:
                lines.append(
                    f"Order by single-lap pace | Fastest lap: {fastest['code']} {_metric_text(fastest['best_lap_metric'])}"
                )
        else:
            leader = next((row for row in raw_rows if row["position"] == 1), raw_rows[0])
            fastest = _best_lap_row(raw_rows)
            line = f"Classification leader: {leader['code']} ({leader['team']})"
            if fastest is not None:
                line = (
                    f"{line} | Fastest lap: {fastest['code']} "
                    f"{_metric_text(fastest['best_lap_metric'])}"
                )
            lines.append(line)

        sector_parts: list[str] = []
        for index in range(3):
            row = _best_sector_row(raw_rows, index)
            if row is None:
                continue
            sector_parts.append(
                f"S{index + 1} {row['code']} {_metric_text(row['best_sectors_metrics'][index])}"
            )
        if sector_parts:
            line = "Best sectors: " + " | ".join(sector_parts)
            latest_message = _latest_race_control_message(race_control)
            if latest_message is not None:
                lap = _coerce_int(latest_message.get("Lap"))
                prefix = f"Lap {lap}: " if lap is not None else ""
                message = _non_empty_text(latest_message.get("Message"))
                if message:
                    line = f"{line} | RC: {prefix}{message}"
            lines.append(line)

    if not lines:
        lines.append("Live timing feed connected, waiting for session data.")

    return tuple(lines[:4])


def _best_lap_row(raw_rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    valid_rows = [row for row in raw_rows if row["best_lap_time"] is not None]
    if not valid_rows:
        return None
    return min(
        valid_rows,
        key=lambda row: (
            _fastest_order_key(row["best_lap_rank"], row["best_lap_time"]),
            row["code"],
        ),
    )


def _best_sector_row(raw_rows: list[dict[str, Any]], index: int) -> dict[str, Any] | None:
    valid_rows = [row for row in raw_rows if row["best_sector_times"][index] is not None]
    if not valid_rows:
        return None
    return min(
        valid_rows,
        key=lambda row: (
            _sector_order_key(row["best_sector_ranks"][index], row["best_sector_times"][index]),
            row["code"],
        ),
    )


def _latest_race_control_message(data: dict[str, Any]) -> dict[str, Any] | None:
    messages = data.get("Messages")
    if isinstance(messages, list) and messages:
        last = messages[-1]
        return _as_dict(last)
    if isinstance(messages, dict) and messages:
        try:
            _, last = max(
                ((int(key), value) for key, value in messages.items()),
                key=lambda item: item[0],
            )
        except ValueError:
            last = next(reversed(messages.values()))
        return _as_dict(last)
    return None


def _current_metric_status(
    metric: Any,
    personal_best: timedelta | None,
    session_best: timedelta | None,
) -> str:
    value = _metric_timedelta(metric)
    if value is None:
        return "-"
    if _metric_flag(metric, "OverallFastest") or value == session_best:
        return "P"
    if _metric_flag(metric, "PersonalFastest") or value == personal_best:
        return "G"
    return "Y"


def _best_metric_status(value: timedelta | None, session_best: timedelta | None) -> str:
    if value is None:
        return "-"
    if value == session_best:
        return "P"
    return "G"


def _metric_text(metric: Any) -> str:
    if isinstance(metric, dict):
        value = metric.get("Value")
    else:
        value = metric
    text = _non_empty_text(value)
    return text or "-"


def _metric_timedelta(metric: Any) -> timedelta | None:
    if isinstance(metric, dict):
        metric = metric.get("Value")
    text = _non_empty_text(metric)
    if text is None:
        return None
    return _parse_live_timedelta(text)


def _metric_rank(metric: Any) -> int | None:
    if not isinstance(metric, dict):
        return None
    return _coerce_int(metric.get("Position"))


def _metric_flag(metric: Any, field: str) -> bool:
    return isinstance(metric, dict) and bool(metric.get(field))


def _metric_triplet(value: Any) -> tuple[Any, Any, Any]:
    items = _ordered_items(value)
    padded = list(items[:3])
    while len(padded) < 3:
        padded.append(None)
    return tuple(padded[:3])


def _mini_sector_triplet(
    value: Any,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    sectors = _metric_triplet(value)
    return as_triplet(_segment_statuses(sector) for sector in sectors)


def _segment_statuses(sector: Any) -> tuple[str, ...]:
    segment_items = _ordered_items(_as_dict(sector).get("Segments"))
    return tuple(_segment_status(segment) for segment in segment_items)


def _segment_status(segment: Any) -> str:
    raw_status = _coerce_int(_as_dict(segment).get("Status"))
    if raw_status is None:
        return "-"

    if raw_status & 2:
        return "P"
    if raw_status & 1:
        return "G"
    if raw_status & (4 | 16 | 32 | 512):
        return "R"
    if raw_status & 2048 or raw_status:
        return "Y"
    return "-"


def _ordered_items(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        indexed: list[tuple[int, Any]] = []
        for key, item in value.items():
            index = _coerce_int(key)
            if index is None:
                continue
            indexed.append((index, item))
        if indexed:
            indexed.sort(key=lambda entry: entry[0])
            return [item for _, item in indexed]
        return list(value.values())
    return []


def _driver_status(timing: dict[str, Any]) -> str:
    if timing.get("Retired"):
        return "Retired"
    if timing.get("Stopped"):
        return "Stopped"
    if timing.get("InPit"):
        return "In Pit"
    return ""


def _lap_progress_text(data: dict[str, Any]) -> str | None:
    current = _coerce_int(data.get("CurrentLap"))
    total = _coerce_int(data.get("TotalLaps"))
    if current is None and total is None:
        return None
    if current is not None and total is not None:
        remaining = max(total - current, 0)
        return f"Laps {current}/{total} (-{remaining})"
    if current is not None:
        return f"Lap {current}"
    return f"Total laps {total}"


def _weather_text(data: dict[str, Any]) -> str | None:
    air = _non_empty_text(data.get("AirTemp"))
    track = _non_empty_text(data.get("TrackTemp"))
    rain = _non_empty_text(data.get("Rainfall"))
    wind = _non_empty_text(data.get("WindSpeed"))

    parts = []
    if air:
        parts.append(f"air {air}C")
    if track:
        parts.append(f"track {track}C")
    if rain:
        parts.append(f"rain {'yes' if rain not in {'0', '0.0', 'false', 'False'} else 'no'}")
    if wind:
        parts.append(f"wind {wind}m/s")
    if not parts:
        return None
    return "Weather: " + " | ".join(parts)


def _session_start_utc(session_info: dict[str, Any]) -> datetime | None:
    start = _non_empty_text(session_info.get("StartDate"))
    if start is None:
        return None
    offset = _non_empty_text(session_info.get("GmtOffset")) or "+00:00:00"
    if offset[0] not in "+-":
        offset = f"+{offset}"
    try:
        return datetime.fromisoformat(f"{start}{offset}").astimezone(UTC)
    except ValueError:
        return None


def _session_year(session_info: dict[str, Any]) -> str:
    start_utc = _session_start_utc(session_info)
    if start_utc is not None:
        return str(start_utc.year)
    path = _non_empty_text(session_info.get("Path"))
    if path:
        return path.split("/", maxsplit=1)[0]
    return str(datetime.now(UTC).year)


def _coerce_feed_timestamp(value: Any) -> datetime:
    text = _non_empty_text(value)
    if text is None:
        return datetime.now(UTC)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(UTC)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _parse_live_timedelta(value: str) -> timedelta | None:
    text = value.strip()
    if not text or text.endswith("L"):
        return None
    if text.startswith("+"):
        text = text[1:]
    parts = text.split(":")
    try:
        if len(parts) == 1:
            return timedelta(seconds=float(parts[0]))
        if len(parts) == 2:
            return timedelta(minutes=int(parts[0]), seconds=float(parts[1]))
        if len(parts) == 3:
            return timedelta(
                hours=int(parts[0]),
                minutes=int(parts[1]),
                seconds=float(parts[2]),
            )
    except ValueError:
        return None
    return None


def _best_time(values: Any) -> timedelta | None:
    valid = [value for value in values if value is not None]
    if not valid:
        return None
    return min(valid)


def _fastest_order_key(rank: int | None, value: timedelta | None) -> tuple[int, Any]:
    if rank is not None:
        return (0, rank)
    if value is not None:
        return (1, value)
    return (2, timedelta.max)


def _sector_order_key(rank: int | None, value: timedelta | None) -> tuple[int, Any]:
    if rank is not None:
        return (0, rank)
    if value is not None:
        return (1, value)
    return (2, timedelta.max)


def _non_empty_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _coerce_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    text = _non_empty_text(value)
    if text is None:
        return None
    if text.lower() in {"true", "1", "yes"}:
        return True
    if text.lower() in {"false", "0", "no"}:
        return False
    return None


def _short_compound(value: Any) -> str:
    text = _non_empty_text(value)
    if text is None:
        return "-"
    normalized = text.upper()
    aliases = {
        "SOFT": "S",
        "MEDIUM": "M",
        "HARD": "H",
        "INTERMEDIATE": "I",
        "WET": "W",
    }
    if normalized in aliases:
        return aliases[normalized]
    if len(normalized) <= 3:
        return normalized
    return normalized[:3]


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {}


def _data_keys(value: dict[str, Any]) -> set[str]:
    return {key for key in value if not str(key).startswith("_")}


def _iter_signalr_messages(payload: str) -> list[str]:
    return [part for part in payload.split(_SIGNALR_SEPARATOR) if part]


def _deep_merge(existing: Any, update: Any) -> Any:
    if update is None:
        return None

    if isinstance(update, dict):
        if isinstance(existing, list):
            merged_list = list(existing)
            for key, value in update.items():
                index = _coerce_int(key)
                if index is None:
                    continue
                while len(merged_list) <= index:
                    merged_list.append(None)
                merged_list[index] = _deep_merge(merged_list[index], value)
            return merged_list

        merged = copy.deepcopy(existing) if isinstance(existing, dict) else {}
        for key, value in update.items():
            merged[key] = _deep_merge(merged.get(key), value)
        return merged

    if isinstance(update, list):
        merged = list(existing) if isinstance(existing, list) else []
        for index, value in enumerate(update):
            if index < len(merged):
                merged[index] = _deep_merge(merged[index], value)
            else:
                merged.append(copy.deepcopy(value))
        return merged

    return copy.deepcopy(update)
