from datetime import datetime, timezone
from typing import cast

from rich.console import Group
from rich.table import Table
from rich.text import Text

from mof1.models import DriverSnapshot, SessionSnapshot
from mof1.widgets import (
    _driver_identity,
    _summary_line_renderable,
    _team_colors,
    _team_swatches,
    _timing_block,
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
    )


def test_team_colors_use_requested_palette() -> None:
    assert _team_colors("McLaren Formula 1 Team") == ("#FF8000",)
    assert _team_colors("Scuderia Ferrari HP") == ("#FF2800",)
    assert _team_colors("Visa Cash App RB") == ("#FFFFFF", "#1F5BFF")
    assert _team_colors("Stake F1 Team Kick Sauber") == ("#8A8D8F", "#D5001C")


def test_driver_identity_can_swap_team_name_for_swatches() -> None:
    identity = _driver_identity(
        _sample_driver("Ferrari"),
        team_display_mode="colors",
    )

    assert "Ferrari" not in identity.plain
    assert "[" not in identity.plain
    assert any("#FF2800" in str(span.style) for span in identity.spans)


def test_two_color_swatches_are_adjacent_single_cells() -> None:
    swatches = _team_swatches(("#FFFFFF", "#1F5BFF"))

    assert swatches.plain == "  "
    assert len(swatches.spans) == 2
    assert "#FFFFFF" in str(swatches.spans[0].style)
    assert "#1F5BFF" in str(swatches.spans[1].style)


def test_timing_block_can_inline_tyre_details() -> None:
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
        inline_tyre_details=True,
    )

    lines = block.plain.splitlines()

    assert "Sets 2  S  12L  M * 18L" in lines[0]
    assert "Sets" not in lines[1]
    assert "Tyre" not in block.plain
    assert "Tyres" not in block.plain


def test_timing_block_can_stack_tyre_details() -> None:
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
        inline_tyre_details=False,
    )

    lines = block.plain.splitlines()

    assert len(lines) == 3
    assert "Tyre" not in lines[0]
    assert "Sets" not in lines[1]
    assert lines[2] == "Sets 2  S  12L  M * 18L"


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
