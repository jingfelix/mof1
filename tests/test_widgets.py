from datetime import datetime, timezone
from typing import Any, cast

from rich.console import Group
from rich.table import Table
from rich.text import Text

from mof1.models import DriverSnapshot, SessionSnapshot
from mof1.widgets import (
    _driver_identity,
    _mini_sector_strip,
    _summary_line_renderable,
    _team_colors,
    _team_swatches,
    _timing_block,
    render_driver_panel,
    render_summary,
)

UTC = timezone.utc


def _sample_driver(
    team: str,
    *,
    current_tyre: str = "-",
    current_tyre_new: bool | None = None,
    current_tyre_laps: int | None = None,
    used_tyre_sets: int | None = None,
    used_tyre_compounds: tuple[str, ...] = (),
    used_tyre_stints: tuple[tuple[str, int | None], ...] = (),
    current_mini_sector_statuses: tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]] = (
        (),
        (),
        (),
    ),
) -> DriverSnapshot:
    return DriverSnapshot(
        1,
        "VER",
        team,
        "1:20.000",
        "1:19.500",
        ("24.100", "27.200", "28.200"),
        ("24.000", "27.100", "28.100"),
        ("P", "G", "Y"),
        ("P", "G", "P"),
        "P",
        "G",
        "",
        current_tyre,
        current_tyre_new,
        current_tyre_laps,
        used_tyre_sets,
        used_tyre_compounds,
        used_tyre_stints,
        current_mini_sector_statuses,
    )


def test_team_colors_use_requested_palette() -> None:
    assert _team_colors("McLaren Formula 1 Team") == ("#FF8000",)
    assert _team_colors("Scuderia Ferrari HP") == ("#FF2800",)
    assert _team_colors("Visa Cash App RB") == ("#FFFFFF", "#1F5BFF")
    assert _team_colors("Stake F1 Team Kick Sauber") == ("#8A8D8F", "#D5001C")


def test_driver_identity_can_hide_team_name_but_keep_swatches() -> None:
    identity = _driver_identity(
        _sample_driver("Ferrari"),
        team_display_mode="hide",
    )

    assert "Ferrari" not in identity.plain
    assert identity.plain.endswith("VER")
    assert "[" not in identity.plain
    assert any("#FF2800" in str(span.style) for span in identity.spans)


def test_driver_identity_can_show_team_name_after_swatches() -> None:
    identity = _driver_identity(
        _sample_driver("Ferrari"),
        team_display_mode="show",
    )

    assert "VER  Ferrari" in identity.plain
    assert "Ferrari" in identity.plain
    assert any("#FF2800" in str(span.style) for span in identity.spans)


def test_two_color_swatches_are_adjacent_single_cells() -> None:
    swatches = _team_swatches(("#FFFFFF", "#1F5BFF"))

    assert swatches.plain == "  "
    assert len(swatches.spans) == 2
    assert "#FFFFFF" in str(swatches.spans[0].style)
    assert "#1F5BFF" in str(swatches.spans[1].style)


def test_timing_block_groups_sectors_and_laps_into_two_rows() -> None:
    block = _timing_block(
        _sample_driver(
            "Ferrari",
            current_tyre="M",
            current_tyre_new=True,
            current_tyre_laps=18,
            used_tyre_sets=2,
            used_tyre_compounds=("S", "M"),
            used_tyre_stints=(("S", 12), ("M", 18)),
        ),
    )

    assert isinstance(block, Table)
    top = [cast(Text, column._cells[0]).plain for column in block.columns]
    bottom = [cast(Text, column._cells[1]).plain for column in block.columns]

    assert top[:3] == ["24.100 24.000", "27.200 27.100", "28.200 28.100"]
    assert top[3] == "1:20.000 1:19.500"
    assert bottom[:3] == ["-", "-", "-"]
    assert "Sets 2  S  12L  M * 18L" in bottom[3]


def test_timing_block_places_mini_sector_strips_below_sector_pairs() -> None:
    block = _timing_block(
        _sample_driver(
            "Ferrari",
            current_mini_sector_statuses=(("Y", "G"), ("P", "R"), ("-",)),
        ),
    )

    assert isinstance(block, Table)
    bottom = [cast(Text, column._cells[1]) for column in block.columns]

    assert bottom[0].plain == "  "
    assert bottom[1].plain == "  "
    assert bottom[2].plain == " "
    assert any("#facc15" in str(span.style) for span in bottom[0].spans)
    assert any("#22c55e" in str(span.style) for span in bottom[0].spans)
    assert any("#d946ef" in str(span.style) for span in bottom[1].spans)
    assert any("#ef4444" in str(span.style) for span in bottom[1].spans)
    assert any("#4b5563" in str(span.style) for span in bottom[2].spans)


def test_mini_sector_strip_uses_background_swatches() -> None:
    strip = _mini_sector_strip(("Y", "G", "P", "R", "-"))

    assert strip.plain == "     "
    assert len(strip.spans) == 5
    assert any("#facc15" in str(span.style) for span in strip.spans)
    assert any("#22c55e" in str(span.style) for span in strip.spans)
    assert any("#d946ef" in str(span.style) for span in strip.spans)
    assert any("#ef4444" in str(span.style) for span in strip.spans)
    assert any("#4b5563" in str(span.style) for span in strip.spans)


def test_render_driver_panel_uses_separate_sector_and_lap_columns() -> None:
    snapshot = SessionSnapshot(
        title="2026 Australian Grand Prix",
        subtitle="Practice 1 | 2026-03-08 04:00 UTC",
        badge="STARTED",
        note="Live timing feed",
        summary_lines=(),
        drivers=(
            _sample_driver(
                "Ferrari",
                current_tyre="M",
                current_tyre_new=True,
                current_tyre_laps=18,
                used_tyre_sets=2,
                used_tyre_compounds=("S", "M"),
                used_tyre_stints=(("S", 12), ("M", 18)),
                current_mini_sector_statuses=(("Y", "G"), ("P", "R"), ("-",)),
            ),
        ),
        loaded_at_utc=datetime(2026, 3, 9, 11, 0, tzinfo=UTC),
    )

    panel = render_driver_panel(snapshot, compact=False, team_display_mode="hide")
    body = cast(Group, panel.renderable).renderables
    table = cast(Table, body[0])

    assert [str(cast(Any, column.header)) for column in table.columns] == [
        "Pos",
        "Driver",
        "S1",
        "S2",
        "S3",
        "Lap",
    ]
    s1_cell = cast(Group, cast(Any, table.columns[2]._cells[0]))
    lap_cell = cast(Group, cast(Any, table.columns[5]._cells[0]))
    assert cast(Text, s1_cell.renderables[0]).plain == "24.100 24.000"
    assert cast(Text, lap_cell.renderables[0]).plain == "1:20.000 1:19.500"
    assert "Sets 2  S  12L  M * 18L" in cast(Text, lap_cell.renderables[1]).plain


def test_summary_line_renderable_groups_status_metrics() -> None:
    renderable = _summary_line_renderable(
        "Session: Started | Track: AllClear | Remain 00:24:30 | Laps 18/58 (-40)"
    )

    assert isinstance(renderable, Table)
    plains = [cast(Text, cell).plain for column in renderable.columns for cell in column._cells]
    assert any("SESSION" in item and "Started" in item for item in plains)
    assert any("TRACK" in item and "AllClear" in item for item in plains)
    assert any("REMAIN" in item and "00:24:30" in item for item in plains)
    assert any("LAPS" in item and "18/58 (-40)" in item for item in plains)


def test_summary_line_renderable_aligns_fastest_to_fixed_column() -> None:
    renderable = _summary_line_renderable(
        "Classification leader: VER (Red Bull Racing) | Fastest lap: VER 1:19.500"
    )

    assert isinstance(renderable, Table)
    plains = [cast(Text, column._cells[0]).plain for column in renderable.columns]
    assert "LEADER" in plains[0]
    assert plains[1] == ""
    assert "FASTEST" in plains[2]
    assert plains[3] == ""


def test_render_summary_uses_structured_rows() -> None:
    snapshot = SessionSnapshot(
        title="2026 Australian Grand Prix",
        subtitle="Race | 2026-03-08 05:00 UTC",
        badge="STARTED",
        note="Live timing feed | Updated at 2026-03-09 11:00 UTC.",
        summary_lines=(
            "Session: Started | Track: AllClear | Remain 00:24:30 | Laps 18/58 (-40)",
            "Weather: air 25.0C | track 34.5C | rain no | wind 1.9m/s",
            "Classification leader: VER (Red Bull Racing) | Fastest lap: VER 1:19.500",
            "Best sectors: S1 VER 24.100 | S2 VER 27.200 | S3 NOR 28.150 | RC: Lap 12: TRACK CLEAR",
        ),
        drivers=(),
        loaded_at_utc=datetime(2026, 3, 9, 11, 0, tzinfo=UTC),
    )

    panel = render_summary(snapshot)
    body = cast(Group, panel.renderable).renderables

    assert cast(Text, body[0]).plain == "Race | 2026-03-08 05:00 UTC"
    assert "STARTED" in cast(Text, body[1]).plain
    assert isinstance(body[2], Table)
    assert isinstance(body[3], Table)
    assert isinstance(body[4], Table)
    assert isinstance(body[5], Table)
