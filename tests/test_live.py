from __future__ import annotations

from datetime import datetime, timezone

from mof1.live_timing.client import LiveTimingState

UTC = timezone.utc


def _base_live_payload() -> dict[str, object]:
    return {
        "SessionInfo": {
            "Meeting": {
                "Name": "Australian Grand Prix",
            },
            "SessionStatus": "Started",
            "Name": "Practice 1",
            "StartDate": "2026-03-08T15:00:00",
            "GmtOffset": "11:00:00",
            "Path": "2026/2026-03-08_Australian_Grand_Prix/2026-03-08_Practice_1/",
        },
        "TrackStatus": {
            "Message": "AllClear",
        },
        "ExtrapolatedClock": {
            "Remaining": "00:24:30",
        },
        "LapCount": {
            "CurrentLap": 18,
            "TotalLaps": 58,
        },
        "WeatherData": {
            "AirTemp": "25.0",
            "TrackTemp": "34.5",
            "Rainfall": "0",
            "WindSpeed": "1.9",
        },
        "DriverList": {
            "_kf": True,
            "1": {
                "Tla": "VER",
                "TeamName": "Red Bull Racing",
                "Line": 2,
            },
            "16": {
                "Tla": "LEC",
                "TeamName": "Ferrari",
                "Line": 1,
            },
        },
        "TimingData": {
            "Lines": {
                "_kf": True,
                "1": {
                    "Position": "2",
                    "Line": 2,
                    "LastLapTime": {
                        "Value": "1:19.500",
                        "OverallFastest": True,
                        "PersonalFastest": True,
                    },
                    "BestLapTime": {
                        "Value": "1:19.500",
                        "Lap": 8,
                    },
                    "Sectors": [
                        {
                            "Value": "24.100",
                            "OverallFastest": True,
                            "PersonalFastest": True,
                            "Segments": [
                                {"Status": 2048},
                                {"Status": 1},
                            ],
                        },
                        {
                            "Value": "27.200",
                            "OverallFastest": False,
                            "PersonalFastest": True,
                            "Segments": [
                                {"Status": 2},
                                {"Status": 2064},
                            ],
                        },
                        {
                            "Value": "28.200",
                            "OverallFastest": False,
                            "PersonalFastest": False,
                            "Segments": [
                                {"Status": 2048},
                                {},
                            ],
                        },
                    ],
                },
                "16": {
                    "Position": "1",
                    "Line": 1,
                    "LastLapTime": {
                        "Value": "1:20.100",
                        "OverallFastest": False,
                        "PersonalFastest": True,
                    },
                    "BestLapTime": {
                        "Value": "1:19.900",
                        "Lap": 7,
                    },
                    "Sectors": [
                        {
                            "Value": "24.300",
                            "OverallFastest": False,
                            "PersonalFastest": False,
                        },
                        {
                            "Value": "27.400",
                            "OverallFastest": False,
                            "PersonalFastest": False,
                        },
                        {
                            "Value": "28.200",
                            "OverallFastest": False,
                            "PersonalFastest": True,
                        },
                    ],
                },
            }
        },
        "TimingAppData": {
            "Lines": {
                "1": {
                    "Stints": [
                        {
                            "Compound": "SOFT",
                            "New": "true",
                            "TotalLaps": 7,
                        },
                        {
                            "Compound": "MEDIUM",
                            "New": "false",
                            "TotalLaps": 11,
                        },
                    ],
                },
                "16": {
                    "Stints": [
                        {
                            "Compound": "HARD",
                            "New": "true",
                            "TotalLaps": 10,
                        }
                    ],
                },
            }
        },
        "TimingStats": {
            "Lines": {
                "_kf": True,
                "1": {
                    "PersonalBestLapTime": {
                        "Value": "1:19.500",
                        "Lap": 8,
                        "Position": 1,
                    },
                    "BestSectors": [
                        {
                            "Value": "24.100",
                            "Position": 1,
                        },
                        {
                            "Value": "27.200",
                            "Position": 1,
                        },
                        {
                            "Value": "28.150",
                            "Position": 2,
                        },
                    ],
                },
                "16": {
                    "PersonalBestLapTime": {
                        "Value": "1:19.900",
                        "Lap": 7,
                        "Position": 2,
                    },
                    "BestSectors": [
                        {
                            "Value": "24.250",
                            "Position": 2,
                        },
                        {
                            "Value": "27.350",
                            "Position": 2,
                        },
                        {
                            "Value": "28.100",
                            "Position": 1,
                        },
                    ],
                },
            }
        },
        "RaceControlMessages": {
            "Messages": [
                {
                    "Message": "TRACK CLEAR",
                    "Lap": 12,
                }
            ]
        },
    }


def test_live_timing_snapshot_builds_current_panel_data() -> None:
    state = LiveTimingState()
    state.apply_snapshot(
        _base_live_payload(),
        received_at_utc=datetime(2026, 3, 9, 11, 0, tzinfo=UTC),
    )

    snapshot = state.build_snapshot()

    assert snapshot is not None
    assert snapshot.title == "2026 Australian Grand Prix"
    assert snapshot.badge == "STARTED"
    assert len(snapshot.drivers) == 2
    assert [driver.code for driver in snapshot.drivers] == ["VER", "LEC"]
    assert [driver.position for driver in snapshot.drivers] == [1, 2]
    assert snapshot.drivers[0].current_sectors == ("24.100", "27.200", "28.200")
    assert snapshot.drivers[0].best_sectors == ("24.100", "27.200", "28.150")
    assert snapshot.drivers[0].current_sector_statuses == ("P", "P", "Y")
    assert snapshot.drivers[0].best_sector_statuses == ("P", "P", "G")
    assert snapshot.drivers[0].current_mini_sector_statuses == (
        ("Y", "G"),
        ("P", "R"),
        ("Y", "-"),
    )
    assert snapshot.drivers[0].current_lap_status == "P"
    assert snapshot.drivers[0].current_tyre == "M"
    assert snapshot.drivers[0].current_tyre_new is False
    assert snapshot.drivers[0].current_tyre_laps == 11
    assert snapshot.drivers[0].used_tyre_sets == 2
    assert snapshot.drivers[0].used_tyre_compounds == ("S", "M")
    assert snapshot.drivers[0].used_tyre_stints == (("S", 7), ("M", 11))
    assert snapshot.drivers[1].best_lap_status == "G"
    assert (
        snapshot.summary_lines[0]
        == "Session: Started | Track: AllClear | Remain 00:24:30 | Laps 18/58 (-40)"
    )
    assert snapshot.summary_lines[1] == "Weather: air 25.0C | track 34.5C | rain no | wind 1.9m/s"
    assert "Order by single-lap pace" in snapshot.summary_lines[2]
    assert "Best sectors:" in snapshot.summary_lines[3]
    assert "RC: Lap 12: TRACK CLEAR" in snapshot.summary_lines[3]


def test_live_timing_state_merges_partial_sector_updates() -> None:
    state = LiveTimingState()
    state.apply_snapshot(
        _base_live_payload(),
        received_at_utc=datetime(2026, 3, 9, 11, 0, tzinfo=UTC),
    )
    state.apply_topic(
        "TimingData",
        {
            "Lines": {
                "1": {
                    "Sectors": {
                        "2": {
                            "Value": "28.150",
                            "OverallFastest": False,
                            "PersonalFastest": True,
                        }
                    },
                    "LastLapTime": {
                        "Value": "1:19.650",
                        "OverallFastest": False,
                        "PersonalFastest": False,
                    },
                }
            }
        },
        received_at_utc=datetime(2026, 3, 9, 11, 0, 1, tzinfo=UTC),
    )

    snapshot = state.build_snapshot()

    assert snapshot is not None
    assert snapshot.drivers[0].current_sectors == ("24.100", "27.200", "28.150")
    assert snapshot.drivers[0].current_sector_statuses == ("P", "P", "G")
    assert snapshot.drivers[0].current_lap == "1:19.650"
    assert snapshot.drivers[0].current_lap_status == "Y"
