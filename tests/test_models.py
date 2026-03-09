from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd

from mof1.models import DriverSnapshot, SessionSelection, format_timedelta, split_drivers
from mof1.service import FastF1Service


def test_format_timedelta_for_minute_values() -> None:
    assert format_timedelta("0 days 00:01:22.345000") == "1:22.345"


def test_split_drivers_keeps_order() -> None:
    drivers = [
        DriverSnapshot(
            i,
            f"D{i}",
            "Team",
            "-",
            "-",
            ("-", "-", "-"),
            ("-", "-", "-"),
            ("-", "-", "-"),
            ("-", "-", "-"),
            "-",
            "-",
            "",
        )
        for i in range(1, 6)
    ]
    left, right = split_drivers(drivers)

    assert [driver.code for driver in left] == ["D1", "D2", "D3"]
    assert [driver.code for driver in right] == ["D4", "D5"]


def test_practice_sessions_sort_by_best_lap() -> None:
    service = FastF1Service()
    selection = SessionSelection(
        key="2026:1:Practice 1",
        year=2026,
        round_number=1,
        event_name="Australian Grand Prix",
        session_name="Practice 1",
        start_utc=datetime.now(UTC),
    )
    results = pd.DataFrame(
        [
            {"Abbreviation": "VER", "TeamName": "Red Bull", "Position": 2, "Status": "Finished"},
            {"Abbreviation": "LEC", "TeamName": "Ferrari", "Position": 1, "Status": "Finished"},
        ]
    )
    laps = pd.DataFrame(
        [
            {
                "Driver": "VER",
                "Team": "Red Bull",
                "LapNumber": 10,
                "Time": pd.Timedelta(minutes=20),
                "LapTime": pd.Timedelta(minutes=1, seconds=19, milliseconds=500),
                "Sector1Time": pd.Timedelta(seconds=24, milliseconds=100),
                "Sector2Time": pd.Timedelta(seconds=27, milliseconds=200),
                "Sector3Time": pd.Timedelta(seconds=28, milliseconds=200),
                "Deleted": False,
                "IsAccurate": True,
                "IsPersonalBest": True,
                "Position": 2,
            },
            {
                "Driver": "LEC",
                "Team": "Ferrari",
                "LapNumber": 12,
                "Time": pd.Timedelta(minutes=21),
                "LapTime": pd.Timedelta(minutes=1, seconds=20, milliseconds=0),
                "Sector1Time": pd.Timedelta(seconds=24, milliseconds=400),
                "Sector2Time": pd.Timedelta(seconds=27, milliseconds=500),
                "Sector3Time": pd.Timedelta(seconds=28, milliseconds=100),
                "Deleted": False,
                "IsAccurate": True,
                "IsPersonalBest": True,
                "Position": 1,
            },
        ]
    )

    snapshots = service._build_driver_snapshots(selection, results, laps)

    assert [driver.code for driver in snapshots] == ["VER", "LEC"]
    assert [driver.position for driver in snapshots] == [1, 2]
    assert snapshots[0].current_sector_statuses == ("P", "P", "G")
    assert snapshots[0].best_sector_statuses == ("P", "P", "G")
    assert snapshots[0].current_lap_status == "P"
    assert snapshots[1].best_lap_status == "G"


def test_best_sectors_are_tracked_independently_from_best_lap() -> None:
    service = FastF1Service()
    selection = SessionSelection(
        key="2026:1:Practice 2",
        year=2026,
        round_number=1,
        event_name="Australian Grand Prix",
        session_name="Practice 2",
        start_utc=datetime.now(UTC),
    )
    results = pd.DataFrame(
        [
            {"Abbreviation": "VER", "TeamName": "Red Bull", "Position": 1, "Status": "Finished"},
        ]
    )
    laps = pd.DataFrame(
        [
            {
                "Driver": "VER",
                "Team": "Red Bull",
                "LapNumber": 5,
                "Time": pd.Timedelta(minutes=10),
                "LapTime": pd.Timedelta(minutes=1, seconds=20, milliseconds=0),
                "Sector1Time": pd.Timedelta(seconds=24, milliseconds=0),
                "Sector2Time": pd.Timedelta(seconds=27, milliseconds=500),
                "Sector3Time": pd.Timedelta(seconds=28, milliseconds=400),
                "Deleted": False,
                "IsAccurate": True,
                "IsPersonalBest": False,
                "Position": 1,
            },
            {
                "Driver": "VER",
                "Team": "Red Bull",
                "LapNumber": 6,
                "Time": pd.Timedelta(minutes=11),
                "LapTime": pd.Timedelta(minutes=1, seconds=19, milliseconds=500),
                "Sector1Time": pd.Timedelta(seconds=24, milliseconds=200),
                "Sector2Time": pd.Timedelta(seconds=27, milliseconds=200),
                "Sector3Time": pd.Timedelta(seconds=28, milliseconds=100),
                "Deleted": False,
                "IsAccurate": True,
                "IsPersonalBest": True,
                "Position": 1,
            },
        ]
    )

    snapshot = service._build_driver_snapshots(selection, results, laps)[0]

    assert snapshot.best_lap == "1:19.500"
    assert snapshot.best_sectors == ("24.000", "27.200", "28.100")
    assert snapshot.best_sector_statuses == ("P", "P", "P")


def test_latest_stream_positions_use_latest_sample() -> None:
    stream = pd.DataFrame(
        [
            {"Driver": "1", "Position": 2, "Time": pd.Timedelta(seconds=10)},
            {"Driver": "16", "Position": 3, "Time": pd.Timedelta(seconds=12)},
            {"Driver": "1", "Position": 1, "Time": pd.Timedelta(seconds=20)},
        ]
    )

    positions = FastF1Service._latest_stream_positions(stream)

    assert positions == {"16": 3, "1": 1}


def test_race_control_deleted_laps_are_applied() -> None:
    laps = pd.DataFrame(
        [
            {
                "DriverNumber": "1",
                "LapTime": pd.Timedelta(minutes=1, seconds=20),
                "IsPersonalBest": True,
                "Deleted": False,
            },
            {
                "DriverNumber": "1",
                "LapTime": pd.Timedelta(minutes=1, seconds=21),
                "IsPersonalBest": True,
                "Deleted": False,
            },
        ]
    )
    race_control = pd.DataFrame(
        [
            {"Message": "CAR 1 LAP TIME 1:20.000 DELETED - TRACK LIMITS 12:34:56"},
            {"Message": "CAR 1 LAP TIME 1:21.000 DELETED - TRACK LIMITS"},
            {"Message": "CAR 1 LAP TIME 1:21.000 REINSTATED"},
        ]
    )

    FastF1Service._apply_deleted_laps_from_race_control(laps, race_control)

    assert bool(laps.loc[0, "Deleted"]) is True
    assert bool(laps.loc[0, "IsPersonalBest"]) is False
    assert laps.loc[0, "DeletedReason"] == "TRACK LIMITS"
    assert bool(laps.loc[1, "Deleted"]) is False
