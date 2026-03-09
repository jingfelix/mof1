from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd

from mof1.models import DriverSnapshot, SessionSelection, format_timedelta, split_drivers
from mof1.service import FastF1Service


def test_format_timedelta_for_minute_values() -> None:
    assert format_timedelta("0 days 00:01:22.345000") == "1:22.345"


def test_split_drivers_keeps_order() -> None:
    drivers = [
        DriverSnapshot(i, f"D{i}", "Team", "-", "-", "")
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
