from mof1.models import DriverSnapshot
from mof1.widgets import _driver_identity, _team_colors, _team_swatches


def _sample_driver(team: str) -> DriverSnapshot:
    return DriverSnapshot(
        1,
        "VER",
        team,
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
