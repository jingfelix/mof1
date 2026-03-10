from __future__ import annotations

import re
import shutil
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from threading import Event
from typing import Any

import fastf1
import fastf1._api as fastf1_api
import fastf1.req as fastf1_req
import pandas as pd
from platformdirs import user_cache_dir

from .live import LiveTimingStream
from .models import (
    CurrentContext,
    DriverSnapshot,
    EventOption,
    SessionSelection,
    SessionSnapshot,
    as_triplet,
    coerce_utc_datetime,
    format_datetime_utc,
    format_timedelta,
    uses_fastest_lap_order,
)

UTC = timezone.utc
_CACHE_READY = False
_RUNTIME_READY = False
_REQUESTS_PER_SECOND = 4
_APP_CACHE_NAME = "mof1"
_LEGACY_CACHE_NAME = "f1-tui"


def _configure_fastf1_runtime() -> None:
    global _RUNTIME_READY
    if _RUNTIME_READY:
        return

    # Textual owns the terminal; silence FastF1's console logger.
    fastf1.set_log_level("CRITICAL")

    min_interval = 1 / _REQUESTS_PER_SECOND
    rate_limits = {
        re.compile(r"^https?://(\w+\.)?ergast\.com.*"): [
            fastf1_req._MinIntervalLimitDelay(min_interval),
            fastf1_req._CallsPerIntervalLimitRaise(200, 60 * 60, "ergast.com: 200 calls/h"),
        ],
        re.compile(r"^https?://.+\..+"): [
            fastf1_req._MinIntervalLimitDelay(min_interval),
            fastf1_req._CallsPerIntervalLimitRaise(500, 60 * 60, "any API: 500 calls/h"),
        ],
    }
    fastf1_req._SessionWithRateLimiting._RATE_LIMITS = rate_limits
    fastf1_req._CachedSessionWithRateLimiting._RATE_LIMITS = rate_limits
    _RUNTIME_READY = True


def _enable_fastf1_cache() -> None:
    global _CACHE_READY
    if _CACHE_READY:
        return

    cache_dir = _migrate_legacy_cache()
    cache_dir.mkdir(parents=True, exist_ok=True)
    fastf1.Cache.enable_cache(str(cache_dir))
    _CACHE_READY = True


def _migrate_legacy_cache() -> Path:
    new_cache_dir = Path(user_cache_dir(_APP_CACHE_NAME)) / "fastf1"
    old_cache_dir = Path(user_cache_dir(_LEGACY_CACHE_NAME)) / "fastf1"

    if not old_cache_dir.exists() or old_cache_dir == new_cache_dir:
        return new_cache_dir

    new_cache_dir.parent.mkdir(parents=True, exist_ok=True)
    if not new_cache_dir.exists():
        shutil.move(str(old_cache_dir), str(new_cache_dir))
        _prune_empty_tree(old_cache_dir.parent)
        return new_cache_dir

    _merge_cache_tree(old_cache_dir, new_cache_dir)
    _prune_empty_tree(old_cache_dir.parent)
    return new_cache_dir


def _merge_cache_tree(source_root: Path, destination_root: Path) -> None:
    destination_root.mkdir(parents=True, exist_ok=True)

    for source in sorted(source_root.rglob("*")):
        relative = source.relative_to(source_root)
        destination = destination_root / relative

        if source.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
            continue

        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            shutil.move(str(source), str(destination))
            continue

        if _prefer_source_file(source, destination):
            destination.unlink()
            shutil.move(str(source), str(destination))


def _prefer_source_file(source: Path, destination: Path) -> bool:
    source_stat = source.stat()
    destination_stat = destination.stat()

    if source_stat.st_size != destination_stat.st_size:
        return source_stat.st_size > destination_stat.st_size
    return source_stat.st_mtime > destination_stat.st_mtime


def _prune_empty_tree(root: Path) -> None:
    if not root.exists():
        return

    for directory in sorted(
        (path for path in root.rglob("*") if path.is_dir()),
        key=lambda path: len(path.parts),
        reverse=True,
    ):
        try:
            directory.rmdir()
        except OSError:
            pass

    current = root
    while current.exists():
        try:
            current.rmdir()
        except OSError:
            break
        current = current.parent


class FastF1Service:
    def __init__(self) -> None:
        _configure_fastf1_runtime()
        _enable_fastf1_cache()

    @staticmethod
    @lru_cache(maxsize=8)
    def _schedule_for_year(year: int) -> pd.DataFrame:
        return fastf1.get_event_schedule(year, include_testing=False).copy()

    def get_schedule(self, year: int) -> pd.DataFrame:
        return self._schedule_for_year(year)

    def available_years(
        self, *, current_year: int | None = None, lookback: int = 6
    ) -> tuple[int, ...]:
        year = current_year or datetime.now(UTC).year
        return tuple(range(year, max(year - lookback, 2017), -1))

    def list_events(self, year: int) -> tuple[EventOption, ...]:
        schedule = self.get_schedule(year)
        events: list[EventOption] = []
        for _, row in schedule.iterrows():
            events.append(
                EventOption(
                    key=str(int(row["RoundNumber"])),
                    year=year,
                    round_number=int(row["RoundNumber"]),
                    name=str(row["EventName"]),
                    location=str(row["Location"]),
                    event_date_utc=coerce_utc_datetime(row["EventDate"]),
                )
            )
        return tuple(events)

    def list_sessions(self, year: int, round_number: int) -> tuple[SessionSelection, ...]:
        schedule = self.get_schedule(year)
        matches = schedule.loc[schedule["RoundNumber"] == round_number]
        if matches.empty:
            raise ValueError(f"No event found for year={year}, round={round_number}")

        row = matches.iloc[0]
        sessions: list[SessionSelection] = []
        for index in range(1, 6):
            name = row.get(f"Session{index}")
            start = row.get(f"Session{index}DateUtc")
            if not name or pd.isna(start):
                continue
            sessions.append(
                SessionSelection(
                    key=f"{year}:{round_number}:{name}",
                    year=year,
                    round_number=round_number,
                    event_name=str(row["EventName"]),
                    session_name=str(name),
                    start_utc=coerce_utc_datetime(start),
                )
            )
        return tuple(sessions)

    def default_history_selection(
        self, year: int, *, now: datetime | None = None
    ) -> SessionSelection:
        current_time = now or datetime.now(UTC)
        sessions = self.flatten_sessions(year)
        past_sessions = [session for session in sessions if session.start_utc <= current_time]
        if past_sessions:
            return past_sessions[-1]
        return sessions[0]

    def flatten_sessions(self, year: int) -> tuple[SessionSelection, ...]:
        sessions: list[SessionSelection] = []
        for event in self.list_events(year):
            sessions.extend(self.list_sessions(year, event.round_number))
        sessions.sort(key=lambda item: item.start_utc)
        return tuple(sessions)

    def current_context(self, *, now: datetime | None = None) -> CurrentContext:
        current_time = now or datetime.now(UTC)
        sessions = self.flatten_sessions(current_time.year)
        latest_started = None
        next_session = None

        for session in sessions:
            if session.start_utc <= current_time:
                latest_started = session
                continue
            next_session = session
            break

        if latest_started is None and next_session is None:
            raise RuntimeError("No sessions available in the current season.")

        target = latest_started or next_session
        assert target is not None

        if latest_started and current_time - latest_started.start_utc <= timedelta(hours=4):
            badge = "LIVE WINDOW"
            note = (
                f"Monitoring {latest_started.event_name} {latest_started.session_name}. "
                f"Started at {format_datetime_utc(latest_started.start_utc)}."
            )
        elif (
            latest_started
            and next_session
            and latest_started.round_number == next_session.round_number
        ):
            badge = "WEEKEND IN PROGRESS"
            note = (
                f"Showing the latest completed session. "
                f"Next up: {next_session.event_name} {next_session.session_name} "
                f"at {format_datetime_utc(next_session.start_utc)}."
            )
        elif latest_started and next_session:
            badge = "LATEST COMPLETE"
            note = (
                f"No active session right now. "
                f"Next session: {next_session.event_name} {next_session.session_name} "
                f"at {format_datetime_utc(next_session.start_utc)}."
            )
        elif latest_started:
            badge = "LATEST COMPLETE"
            note = "Season complete. Showing the most recent finished session."
        else:
            badge = "UPCOMING"
            assert next_session is not None
            note = (
                f"Season not started yet. Next session: {next_session.event_name} "
                f"{next_session.session_name} at {format_datetime_utc(next_session.start_utc)}."
            )

        return CurrentContext(
            target=target,
            latest_started=latest_started,
            next_session=next_session,
            badge=badge,
            note=note,
        )

    def run_live_timing(
        self,
        stop_event: Event,
        *,
        on_snapshot: Any,
        on_status: Any = None,
    ) -> None:
        LiveTimingStream().run(
            stop_event,
            on_snapshot=on_snapshot,
            on_status=on_status,
        )

    def load_session_snapshot(
        self,
        selection: SessionSelection,
        *,
        badge_override: str | None = None,
        note_override: str | None = None,
    ) -> SessionSnapshot:
        try:
            session = fastf1.get_session(
                selection.year,
                selection.round_number,
                selection.session_name,
            )
            if getattr(session, "f1_api_support", False):
                try:
                    results, laps, session_status, track_status, race_control = (
                        self._load_lightweight_session_data(session)
                    )
                except Exception:
                    results, laps, session_status, track_status, race_control = (
                        self._load_full_session_data(session)
                    )
            else:
                results, laps, session_status, track_status, race_control = (
                    self._load_full_session_data(session)
                )
        except Exception as exc:  # pragma: no cover - network/library failures
            return SessionSnapshot(
                title=f"{selection.year} {selection.event_name}",
                subtitle=selection.label,
                badge=badge_override or "UNAVAILABLE",
                note=note_override or "Session data could not be loaded.",
                summary_lines=(f"Load error: {exc}",),
                drivers=(),
                loaded_at_utc=datetime.now(UTC),
                error=str(exc),
            )

        drivers = self._build_driver_snapshots(selection, results, laps)
        summary_lines = self._build_summary_lines(
            selection,
            results,
            laps,
            session_status=session_status,
            track_status=track_status,
            race_control=race_control,
        )

        return SessionSnapshot(
            title=f"{selection.year} {selection.event_name}",
            subtitle=f"{selection.session_name} | {format_datetime_utc(selection.start_utc)}",
            badge=badge_override or self._session_badge(session_status),
            note=note_override or f"Loaded at {format_datetime_utc(datetime.now(UTC))}.",
            summary_lines=summary_lines,
            drivers=tuple(drivers),
            loaded_at_utc=datetime.now(UTC),
            error=None,
        )

    def _build_summary_lines(
        self,
        selection: SessionSelection,
        results: pd.DataFrame,
        laps: pd.DataFrame,
        *,
        session_status: pd.DataFrame | None = None,
        track_status: pd.DataFrame | None = None,
        race_control: pd.DataFrame | None = None,
    ) -> tuple[str, ...]:
        lines: list[str] = []

        session_state = self._last_value(session_status, "Status")
        track_message = self._last_value(track_status, "Message")
        if session_state or track_message:
            parts = []
            if session_state:
                parts.append(f"Session: {session_state}")
            if track_message:
                parts.append(f"Track: {track_message}")
            lines.append(" | ".join(parts))

        if self._uses_fastest_lap_order(selection.session_name):
            fastest_line = self._fastest_lap_line(laps)
            if fastest_line:
                lines.append(f"Order by single-lap pace | {fastest_line}")
        elif not results.empty:
            leader = sorted(
                results.to_dict("records"),
                key=lambda row: self._sort_position(
                    row.get("Position"),
                    fallback=row.get("Abbreviation", "ZZZ"),
                ),
            )[0]
            lines.append(
                f"Classification leader: {leader.get('Abbreviation', '-')} "
                f"({leader.get('TeamName', '-')})"
            )

        fastest_line = self._fastest_lap_line(laps)
        if fastest_line and not self._uses_fastest_lap_order(selection.session_name):
            lines.append(fastest_line)

        fastest_sector_line = self._fastest_sector_line(laps)
        if fastest_sector_line:
            lines.append(fastest_sector_line)

        if race_control is not None and not race_control.empty:
            latest_message = race_control.iloc[-1]
            lap = latest_message.get("Lap")
            prefix = f"Lap {self._coerce_int(lap)}: " if self._coerce_int(lap) else ""
            message = str(latest_message.get("Message", "")).strip()
            if message:
                lines.append(f"Race control: {prefix}{message}")

        if not lines:
            lines.append("Session loaded, but no timing summary was available.")

        return tuple(lines[:4])

    def _load_full_session_data(
        self,
        session: Any,
    ) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        session.load(laps=True, telemetry=False, weather=False, messages=True)
        return (
            self._copy_frame(session.results),
            self._copy_frame(session.laps),
            self._copy_frame(session.session_status),
            self._copy_frame(session.track_status),
            self._copy_frame(getattr(session, "race_control_messages", None)),
        )

    def _load_lightweight_session_data(
        self,
        session: Any,
    ) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        driver_info = session._drivers_from_f1_api()
        if driver_info is None or driver_info.empty:
            raise RuntimeError("FastF1 driver info is unavailable for this session.")

        raw_laps, stream_data, _ = fastf1_api._extended_timing_data(session.api_path)
        session_status = self._load_api_frame(fastf1_api.session_status_data, session.api_path)
        track_status = self._load_api_frame(fastf1_api.track_status_data, session.api_path)
        race_control = self._load_api_frame(
            fastf1_api.race_control_messages,
            session.api_path,
        )

        results = self._build_results_from_timing(driver_info, stream_data)
        laps = self._build_laps_from_timing(driver_info, raw_laps, race_control)
        return results, laps, session_status, track_status, race_control

    @staticmethod
    def _load_api_frame(
        loader: Any,
        api_path: str,
    ) -> pd.DataFrame:
        try:
            payload = loader(api_path)
        except Exception:
            return pd.DataFrame()
        return pd.DataFrame(payload)

    @staticmethod
    def _copy_frame(frame: pd.DataFrame | None) -> pd.DataFrame:
        if frame is None:
            return pd.DataFrame()
        return frame.copy()

    def _build_results_from_timing(
        self,
        driver_info: pd.DataFrame,
        stream_data: pd.DataFrame,
    ) -> pd.DataFrame:
        results = driver_info.copy().reset_index(drop=True)
        if "DriverNumber" in results.columns:
            results["DriverNumber"] = results["DriverNumber"].astype(str)

        latest_positions = self._latest_stream_positions(stream_data)
        if latest_positions:
            results["Position"] = results["DriverNumber"].map(latest_positions)
        else:
            results["Position"] = pd.NA

        if "Status" not in results.columns:
            results["Status"] = ""

        return results

    def _build_laps_from_timing(
        self,
        driver_info: pd.DataFrame,
        raw_laps: pd.DataFrame,
        race_control: pd.DataFrame,
    ) -> pd.DataFrame:
        laps = raw_laps.copy()
        if laps.empty:
            return laps

        laps.rename(
            columns={"Driver": "DriverNumber", "NumberOfLaps": "LapNumber"},
            inplace=True,
        )
        laps["DriverNumber"] = laps["DriverNumber"].astype(str)

        info = driver_info.copy()
        info["DriverNumber"] = info["DriverNumber"].astype(str)
        code_map = (
            info.set_index("DriverNumber")["Abbreviation"].to_dict()
            if "Abbreviation" in info.columns
            else {}
        )
        team_map = (
            info.set_index("DriverNumber")["TeamName"].to_dict()
            if "TeamName" in info.columns
            else {}
        )

        laps["Driver"] = laps["DriverNumber"].map(code_map).fillna(laps["DriverNumber"])
        laps["Team"] = laps["DriverNumber"].map(team_map).fillna("-")
        laps["Deleted"] = False
        self._apply_deleted_laps_from_race_control(laps, race_control)
        return laps

    @staticmethod
    def _latest_stream_positions(stream_data: pd.DataFrame) -> dict[str, int]:
        if (
            stream_data.empty
            or "Driver" not in stream_data.columns
            or "Position" not in stream_data.columns
        ):
            return {}

        latest = stream_data.copy()
        latest["Driver"] = latest["Driver"].astype(str)
        latest["Position"] = pd.to_numeric(latest["Position"], errors="coerce")
        latest = latest.dropna(subset=["Position"])
        if latest.empty:
            return {}

        if "Time" in latest.columns:
            latest = latest.sort_values(by="Time", kind="mergesort", na_position="last")
        latest = latest.groupby("Driver", sort=False).tail(1)
        return {str(row["Driver"]): int(row["Position"]) for _, row in latest.iterrows()}

    @staticmethod
    def _apply_deleted_laps_from_race_control(
        laps: pd.DataFrame,
        race_control: pd.DataFrame,
    ) -> None:
        if laps.empty or race_control.empty:
            return

        msg_pattern = re.compile(r"CAR (\d{1,2}) .* TIME (\d:\d\d\.\d\d\d) DELETED - (.*)")
        reinstated_pattern = re.compile(r"CAR (\d{1,2}) .* TIME (\d:\d\d\.\d\d\d) .*REINSTATED.*")
        timestamp_pattern = re.compile(r"\d\d:\d\d:\d\d")

        reinstated_laps: set[tuple[str, pd.Timedelta]] = set()
        for _, row in race_control.iterrows():
            message = str(row.get("Message", "")).strip()
            match = reinstated_pattern.match(message)
            if not match:
                continue
            reinstated_laps.add((match[1], FastF1Service._parse_rcm_lap_time(match[2])))

        for _, row in race_control.iterrows():
            message = str(row.get("Message", "")).strip()
            match = msg_pattern.match(message)
            if not match:
                continue

            driver_number = match[1]
            lap_time = FastF1Service._parse_rcm_lap_time(match[2])
            if (driver_number, lap_time) in reinstated_laps:
                continue

            reason = timestamp_pattern.sub("", match[3]).strip()
            mask = (laps["DriverNumber"] == driver_number) & (laps["LapTime"] == lap_time)
            laps.loc[mask, "Deleted"] = True
            if "IsPersonalBest" in laps.columns:
                laps.loc[mask, "IsPersonalBest"] = False
            if reason:
                laps.loc[mask, "DeletedReason"] = reason

    @staticmethod
    def _parse_rcm_lap_time(value: str) -> pd.Timedelta:
        minutes, seconds = value.split(":", maxsplit=1)
        return pd.Timedelta(minutes=int(minutes), seconds=float(seconds))

    def _build_driver_snapshots(
        self,
        selection: SessionSelection,
        results: pd.DataFrame,
        laps: pd.DataFrame,
    ) -> list[DriverSnapshot]:
        result_rows: dict[str, dict[str, Any]] = {}
        if not results.empty:
            for row in results.to_dict("records"):
                code = str(row.get("Abbreviation", "")).strip()
                if code:
                    result_rows[code] = row

        latest_laps: dict[str, pd.Series] = {}
        best_laps: dict[str, pd.Series] = {}
        driver_best_sectors: dict[str, tuple[object, object, object]] = {}
        if not laps.empty and "Driver" in laps.columns:
            for code, group in laps.groupby("Driver"):
                timed_group = group.dropna(subset=["LapTime"])
                if timed_group.empty:
                    continue

                if "Deleted" in timed_group.columns:
                    timed_group = timed_group.loc[timed_group["Deleted"].ne(True)]
                if timed_group.empty:
                    continue

                latest_group = timed_group
                if "IsAccurate" in latest_group.columns:
                    accurate_only = latest_group.loc[latest_group["IsAccurate"].eq(True)]
                    if not accurate_only.empty:
                        latest_group = accurate_only

                latest_laps[str(code)] = latest_group.sort_values(
                    by=["LapNumber", "Time"],
                    kind="mergesort",
                    na_position="last",
                ).iloc[-1]

                best_group = timed_group
                if "IsPersonalBest" in best_group.columns:
                    best_only = best_group.loc[best_group["IsPersonalBest"].eq(True)]
                    if not best_only.empty:
                        best_group = best_only

                best_laps[str(code)] = best_group.sort_values(
                    by=["LapTime", "Time"],
                    kind="mergesort",
                    na_position="last",
                ).iloc[0]

                driver_best_sectors[str(code)] = (
                    self._min_timedelta(timed_group.get("Sector1Time")),
                    self._min_timedelta(timed_group.get("Sector2Time")),
                    self._min_timedelta(timed_group.get("Sector3Time")),
                )

        fastest_sectors = self._session_fastest_sector_times(laps)
        session_fastest_lap = self._session_fastest_lap_time(laps)

        all_codes = sorted(set(result_rows) | set(latest_laps) | set(best_laps))
        raw_rows: list[dict[str, Any]] = []
        for code in all_codes:
            result_row = result_rows.get(code, {})
            latest_lap = latest_laps.get(code)
            best_lap = best_laps.get(code)

            team = self._first_non_empty(
                result_row.get("TeamName"),
                latest_lap.get("Team") if latest_lap is not None else None,
                "-",
            )
            position = self._first_position(
                result_row.get("Position"),
                latest_lap.get("Position") if latest_lap is not None else None,
            )
            status = str(result_row.get("Status", "")).strip()
            current_sector_times = (
                latest_lap.get("Sector1Time") if latest_lap is not None else None,
                latest_lap.get("Sector2Time") if latest_lap is not None else None,
                latest_lap.get("Sector3Time") if latest_lap is not None else None,
            )
            driver_sector_bests = driver_best_sectors.get(code, (None, None, None))
            raw_rows.append(
                {
                    "position": position,
                    "code": code,
                    "team": team,
                    "status": status,
                    "current_lap_time": latest_lap.get("LapTime")
                    if latest_lap is not None
                    else None,
                    "best_lap_time": best_lap.get("LapTime") if best_lap is not None else None,
                    "current_sectors": current_sector_times,
                    "best_sectors": driver_sector_bests,
                    "current_sector_statuses": tuple(
                        self._sector_status(
                            current_sector_times[index],
                            driver_sector_bests[index],
                            fastest_sectors[index],
                        )
                        for index in range(3)
                    ),
                    "best_sector_statuses": tuple(
                        self._best_marker_status(
                            driver_sector_bests[index],
                            fastest_sectors[index],
                        )
                        for index in range(3)
                    ),
                    "current_lap_status": self._lap_status(
                        latest_lap.get("LapTime") if latest_lap is not None else None,
                        best_lap.get("LapTime") if best_lap is not None else None,
                        session_fastest_lap,
                    ),
                    "best_lap_status": self._best_marker_status(
                        best_lap.get("LapTime") if best_lap is not None else None,
                        session_fastest_lap,
                    ),
                }
            )

        if self._uses_fastest_lap_order(selection.session_name):
            raw_rows.sort(
                key=lambda row: (
                    self._timedelta_sort_key(row["best_lap_time"]),
                    row["position"] if row["position"] is not None else 999,
                    row["code"],
                )
            )
            return [
                DriverSnapshot(
                    position=index,
                    code=row["code"],
                    team=row["team"],
                    current_lap=format_timedelta(row["current_lap_time"]),
                    best_lap=format_timedelta(row["best_lap_time"]),
                    current_sectors=as_triplet(
                        format_timedelta(value) for value in row["current_sectors"]
                    ),
                    best_sectors=as_triplet(
                        format_timedelta(value) for value in row["best_sectors"]
                    ),
                    current_sector_statuses=row["current_sector_statuses"],
                    best_sector_statuses=row["best_sector_statuses"],
                    current_lap_status=row["current_lap_status"],
                    best_lap_status=row["best_lap_status"],
                    status=row["status"],
                )
                for index, row in enumerate(raw_rows, start=1)
            ]

        raw_rows.sort(
            key=lambda row: (
                row["position"] if row["position"] is not None else 999,
                row["code"],
            )
        )
        return [
            DriverSnapshot(
                position=row["position"],
                code=row["code"],
                team=row["team"],
                current_lap=format_timedelta(row["current_lap_time"]),
                best_lap=format_timedelta(row["best_lap_time"]),
                current_sectors=as_triplet(
                    format_timedelta(value) for value in row["current_sectors"]
                ),
                best_sectors=as_triplet(format_timedelta(value) for value in row["best_sectors"]),
                current_sector_statuses=row["current_sector_statuses"],
                best_sector_statuses=row["best_sector_statuses"],
                current_lap_status=row["current_lap_status"],
                best_lap_status=row["best_lap_status"],
                status=row["status"],
            )
            for row in raw_rows
        ]

    @staticmethod
    def _fastest_lap_line(laps: pd.DataFrame) -> str | None:
        fastest = FastF1Service._fastest_lap_row(laps)
        if fastest is None:
            return None
        return (
            f"Fastest lap: {fastest.get('Driver', '-')} {format_timedelta(fastest.get('LapTime'))}"
        )

    @staticmethod
    def _weather_line(weather: pd.DataFrame | None) -> str | None:
        if weather is None or weather.empty:
            return None

        latest = weather.iloc[-1]
        rainfall = "yes" if bool(latest.get("Rainfall", False)) else "no"
        return (
            f"Weather: air {latest.get('AirTemp', '-')}C | "
            f"track {latest.get('TrackTemp', '-')}C | rain {rainfall}"
        )

    @staticmethod
    def _session_badge(session_status: pd.DataFrame | None) -> str:
        if session_status is None or session_status.empty:
            return "SESSION"
        return str(session_status.iloc[-1]["Status"]).upper()

    @staticmethod
    def _last_value(frame: pd.DataFrame | None, column: str) -> str | None:
        if frame is None or frame.empty or column not in frame.columns:
            return None
        value = frame.iloc[-1].get(column)
        if value is None or pd.isna(value):
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _sort_position(value: Any, *, fallback: str) -> tuple[int, str]:
        coerced = FastF1Service._coerce_int(value)
        return (coerced if coerced is not None else 999, fallback)

    @staticmethod
    def _timedelta_sort_key(value: Any) -> tuple[int, pd.Timedelta]:
        if value is None or pd.isna(value):
            return (1, pd.Timedelta.max)
        return (0, pd.Timedelta(value))

    @staticmethod
    def _min_timedelta(series: Any) -> object:
        if series is None:
            return None
        valid = series.dropna()
        if valid.empty:
            return None
        return valid.min()

    @staticmethod
    def _session_fastest_sector_times(laps: pd.DataFrame) -> tuple[object, object, object]:
        if laps.empty:
            return (None, None, None)
        rows = FastF1Service._session_fastest_sector_rows(laps)
        return as_triplet(
            row.get(f"Sector{index}Time") if row is not None else None
            for index, row in enumerate(rows, start=1)
        )

    @staticmethod
    def _session_fastest_sector_rows(
        laps: pd.DataFrame,
    ) -> tuple[pd.Series | None, pd.Series | None, pd.Series | None]:
        if laps.empty:
            return (None, None, None)
        return as_triplet(
            FastF1Service._fastest_sector_row(laps, column)
            for column in ("Sector1Time", "Sector2Time", "Sector3Time")
        )

    @staticmethod
    def _fastest_sector_row(laps: pd.DataFrame, column: str) -> pd.Series | None:
        if column not in laps.columns:
            return None
        valid = laps.dropna(subset=[column])
        if "Deleted" in valid.columns:
            valid = valid.loc[valid["Deleted"].ne(True)]
        if valid.empty:
            return None
        return valid.sort_values(by=[column, "Time"], kind="mergesort").iloc[0]

    @staticmethod
    def _fastest_lap_row(laps: pd.DataFrame) -> pd.Series | None:
        if laps.empty or "LapTime" not in laps.columns:
            return None
        valid = laps.dropna(subset=["LapTime"])
        if "Deleted" in valid.columns:
            valid = valid.loc[valid["Deleted"].ne(True)]
        if valid.empty:
            return None
        return valid.sort_values(by=["LapTime", "Time"], kind="mergesort").iloc[0]

    @staticmethod
    def _session_fastest_lap_time(laps: pd.DataFrame) -> object:
        fastest = FastF1Service._fastest_lap_row(laps)
        if fastest is None:
            return None
        return fastest.get("LapTime")

    @staticmethod
    def _sector_status(current_value: Any, personal_best: Any, session_best: Any) -> str:
        if current_value is None or pd.isna(current_value):
            return "-"

        current = pd.Timedelta(current_value)
        if (
            session_best is not None
            and not pd.isna(session_best)
            and current == pd.Timedelta(session_best)
        ):
            return "P"
        if (
            personal_best is not None
            and not pd.isna(personal_best)
            and current == pd.Timedelta(personal_best)
        ):
            return "G"
        return "Y"

    @staticmethod
    def _lap_status(current_value: Any, personal_best: Any, session_best: Any) -> str:
        return FastF1Service._sector_status(current_value, personal_best, session_best)

    @staticmethod
    def _best_marker_status(best_value: Any, session_best: Any) -> str:
        if best_value is None or pd.isna(best_value):
            return "-"

        best = pd.Timedelta(best_value)
        if (
            session_best is not None
            and not pd.isna(session_best)
            and best == pd.Timedelta(session_best)
        ):
            return "P"
        return "G"

    @staticmethod
    def _fastest_sector_line(laps: pd.DataFrame) -> str | None:
        rows = FastF1Service._session_fastest_sector_rows(laps)
        parts: list[str] = []
        for index, row in enumerate(rows, start=1):
            if row is None:
                continue
            parts.append(
                f"S{index} {row.get('Driver', '-')} {format_timedelta(row.get(f'Sector{index}Time'))}"
            )
        if not parts:
            return None
        return "Best sectors: " + " | ".join(parts)

    @staticmethod
    def _uses_fastest_lap_order(session_name: str) -> bool:
        return uses_fastest_lap_order(session_name)

    @staticmethod
    def _coerce_int(value: Any) -> int | None:
        if value is None or pd.isna(value):
            return None
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None

    @classmethod
    def _first_position(cls, *values: Any) -> int | None:
        for value in values:
            coerced = cls._coerce_int(value)
            if coerced is not None:
                return coerced
        return None

    @staticmethod
    def _first_non_empty(*values: Any) -> str:
        for value in values:
            if value is None or pd.isna(value):
                continue
            text = str(value).strip()
            if text:
                return text
        return "-"
