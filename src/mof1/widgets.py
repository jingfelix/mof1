from __future__ import annotations

import re

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .models import DriverSnapshot, SessionSnapshot

TEAM_COLOR_MAP: dict[str, tuple[str, ...]] = {
    "mclaren": ("#FF8000",),
    "ferrari": ("#FF2800",),
    "mercedes": ("#00A19B",),
    "red bull racing": ("#1E41FF",),
    "williams": ("#005AFF",),
    "racing bulls": ("#FFFFFF", "#1F5BFF"),
    "audi": ("#8A8D8F", "#D5001C"),
    "alpine": ("#0050FF", "#FF4FA3"),
    "cadillac": ("#4A4A4A", "#FFFFFF"),
    "aston martin": ("#00665E",),
    "haas": ("#E10600",),
}

TYRE_STYLE_MAP: dict[str, str] = {
    "S": "bold white on #E10600",
    "M": "bold black on #FFD12E",
    "H": "bold black on #F5F5F5",
    "I": "bold black on #43B02A",
    "W": "bold white on #0067C6",
}


def render_summary(snapshot: SessionSnapshot) -> Panel:
    renderables: list[RenderableType] = [
        Text(snapshot.subtitle, style="bold white"),
        _summary_meta(snapshot),
    ]
    renderables.extend(_summary_line_renderable(line) for line in snapshot.summary_lines)
    if snapshot.error and not any(
        line.startswith("Load error:") for line in snapshot.summary_lines
    ):
        renderables.append(_summary_alert_line(snapshot.error))
    body = Group(*renderables)
    border_style = "red" if snapshot.error else "bright_blue"
    return Panel(body, title=snapshot.title, border_style=border_style)


def render_driver_panel(
    snapshot: SessionSnapshot,
    *,
    panel_width: int = 0,
    compact: bool = False,
    team_display_mode: str = "hide",
) -> Panel:
    return _render_driver_panel(
        _panel_title(list(snapshot.drivers), "Drivers"),
        list(snapshot.drivers),
        snapshot.error,
        panel_width=panel_width,
        compact=compact,
        team_display_mode=team_display_mode,
    )


def _render_driver_panel(
    title: str,
    drivers: list[DriverSnapshot],
    error: str | None,
    *,
    panel_width: int,
    compact: bool,
    team_display_mode: str,
) -> Panel:
    table = Table(expand=True, box=None, pad_edge=False)
    table.add_column("Pos", justify="right", width=3, style="bold white", no_wrap=True)

    if compact:
        table.add_column("Entry", ratio=1, style="white")
    else:
        driver_width = 24 if team_display_mode == "show" else 8
        table.add_column("Driver", width=driver_width, style="white")
        table.add_column("S1", width=14, no_wrap=True, style="white")
        table.add_column("S2", width=14, no_wrap=True, style="white")
        table.add_column("S3", width=14, no_wrap=True, style="white")
        table.add_column("Lap", ratio=1, style="white")

    if not drivers:
        message = error or "No driver timing data for this selection."
        if compact:
            table.add_row("-", message)
        else:
            table.add_row("-", message, "", "", "", "")
    else:
        for driver in drivers:
            position = str(driver.position) if driver.position is not None else "-"
            if compact:
                table.add_row(
                    position,
                    _compact_entry(
                        driver,
                        team_display_mode=team_display_mode,
                    ),
                )
            else:
                table.add_row(
                    position,
                    _driver_identity(driver, team_display_mode=team_display_mode),
                    _sector_cell(driver, 0),
                    _sector_cell(driver, 1),
                    _sector_cell(driver, 2),
                    _lap_cell(driver),
                )

    return Panel(Group(table, _legend()), title=title, border_style="white")


def _compact_entry(driver: DriverSnapshot, *, team_display_mode: str) -> Group:
    return Group(
        _driver_identity(driver, team_display_mode=team_display_mode, team_width=28),
        _timing_block(driver),
    )


def _driver_identity(
    driver: DriverSnapshot,
    *,
    team_display_mode: str,
    team_width: int = 17,
) -> Text:
    line = Text()
    colors = _team_colors(driver.team)
    if colors:
        line.append_text(_team_swatches(colors))
        line.append(" ")
    line.append(driver.code, style="bold cyan")
    if team_display_mode == "show":
        line.append("  ")
        line.append(_shorten(driver.team, team_width), style="white")
    return line


def _timing_block(driver: DriverSnapshot) -> Table:
    grid = Table.grid(expand=False, padding=(0, 1))
    grid.add_column(ratio=1, no_wrap=True)
    grid.add_column(ratio=1, no_wrap=True)
    grid.add_column(ratio=1, no_wrap=True)
    grid.add_column(ratio=2, no_wrap=True)

    grid.add_row(
        _sector_pair_text(
            driver.current_sectors[0],
            driver.current_sector_statuses[0],
            driver.best_sectors[0],
            driver.best_sector_statuses[0],
        ),
        _sector_pair_text(
            driver.current_sectors[1],
            driver.current_sector_statuses[1],
            driver.best_sectors[1],
            driver.best_sector_statuses[1],
        ),
        _sector_pair_text(
            driver.current_sectors[2],
            driver.current_sector_statuses[2],
            driver.best_sectors[2],
            driver.best_sector_statuses[2],
        ),
        _lap_pair_text(driver),
    )
    grid.add_row(
        _mini_sector_strip(driver.current_mini_sector_statuses[0]),
        _mini_sector_strip(driver.current_mini_sector_statuses[1]),
        _mini_sector_strip(driver.current_mini_sector_statuses[2]),
        _tyre_cell_text(driver),
    )
    return grid


def _sector_cell(driver: DriverSnapshot, index: int) -> Group:
    return Group(
        _sector_pair_text(
            driver.current_sectors[index],
            driver.current_sector_statuses[index],
            driver.best_sectors[index],
            driver.best_sector_statuses[index],
        ),
        _mini_sector_strip(driver.current_mini_sector_statuses[index]),
    )


def _lap_cell(driver: DriverSnapshot) -> Group:
    return Group(
        _lap_pair_text(driver),
        _tyre_cell_text(driver),
    )


def _append_metric(line: Text, value: str, status: str) -> None:
    line.append(value, style=_timing_style(status))


def _sector_pair_text(current: str, current_status: str, best: str, best_status: str) -> Text:
    line = Text()
    _append_metric(line, current, current_status)
    line.append(" ", style="dim")
    line.append(best, style=_reference_timing_style(best_status))
    return line


def _lap_pair_text(driver: DriverSnapshot) -> Text:
    line = Text()
    _append_metric(line, driver.current_lap, driver.current_lap_status)
    line.append(" ", style="dim")
    line.append(driver.best_lap, style=_reference_timing_style(driver.best_lap_status))
    return line


def _tyre_cell_text(driver: DriverSnapshot) -> Text:
    used = _used_tyre_text(driver)
    if used is not None:
        return used
    return Text("-", style="dim")


def _legend() -> Text:
    legend = Text("Purple ", style=_timing_style("P"))
    legend.append("session best", style="dim")
    legend.append(" | ", style="dim")
    legend.append("Green ", style=_timing_style("G"))
    legend.append("driver best", style="dim")
    legend.append(" | ", style="dim")
    legend.append("Yellow ", style=_timing_style("Y"))
    legend.append("current only", style="dim")
    return legend


def _timing_style(status: str) -> str:
    if status == "P":
        return "bold bright_magenta"
    if status == "G":
        return "bold bright_green"
    if status == "R":
        return "bold bright_red"
    if status == "Y":
        return "bold bright_yellow"
    return "dim"


def _reference_timing_style(status: str) -> str:
    if status == "P":
        return "bold bright_magenta"
    return "dim #94a3b8"


def _mini_sector_strip(statuses: tuple[str, ...]) -> Text:
    if not statuses:
        return Text("-", style="dim")

    strip = Text()
    for status in statuses:
        strip.append(" ", style=_mini_sector_style(status))
    return strip


def _mini_sector_style(status: str) -> str:
    if status == "P":
        return "on #d946ef"
    if status == "G":
        return "on #22c55e"
    if status == "R":
        return "on #ef4444"
    if status == "Y":
        return "on #facc15"
    return "on #4b5563"


def _shorten(value: str, width: int) -> str:
    if len(value) <= width:
        return value
    return value[: width - 1] + "..."


def _panel_title(drivers: list[DriverSnapshot], fallback: str) -> str:
    positions = [driver.position for driver in drivers if driver.position is not None]
    if not positions:
        return fallback
    return f"Positions {min(positions)}-{max(positions)}"


def _team_colors(team: str) -> tuple[str, ...]:
    normalized = re.sub(r"[^a-z0-9]+", " ", team.lower()).strip()
    if not normalized:
        return ()

    if (
        "racing bulls" in normalized
        or normalized == "rb"
        or normalized.startswith("rb ")
        or "visa cash app" in normalized
        or "alphatauri" in normalized
    ):
        return TEAM_COLOR_MAP["racing bulls"]
    if "red bull" in normalized:
        return TEAM_COLOR_MAP["red bull racing"]
    if any(alias in normalized for alias in ("audi", "sauber", "kick", "stake")):
        return TEAM_COLOR_MAP["audi"]
    if "aston martin" in normalized:
        return TEAM_COLOR_MAP["aston martin"]
    if "mclaren" in normalized:
        return TEAM_COLOR_MAP["mclaren"]
    if "ferrari" in normalized:
        return TEAM_COLOR_MAP["ferrari"]
    if "mercedes" in normalized:
        return TEAM_COLOR_MAP["mercedes"]
    if "williams" in normalized:
        return TEAM_COLOR_MAP["williams"]
    if "alpine" in normalized:
        return TEAM_COLOR_MAP["alpine"]
    if "cadillac" in normalized:
        return TEAM_COLOR_MAP["cadillac"]
    if "haas" in normalized:
        return TEAM_COLOR_MAP["haas"]
    return ()


def _team_swatches(colors: tuple[str, ...]) -> Text:
    swatches = Text()
    if len(colors) == 1:
        swatches.append("  ", style=f"on {colors[0]}")
        return swatches

    for color in colors:
        swatches.append(" ", style=f"on {color}")
    return swatches


def _used_tyre_text(driver: DriverSnapshot) -> Text | None:
    if (
        driver.used_tyre_sets is None
        and not driver.used_tyre_stints
        and not driver.used_tyre_compounds
    ):
        return None

    text = Text()
    sets = (
        driver.used_tyre_sets if driver.used_tyre_sets is not None else len(driver.used_tyre_stints)
    )
    text.append(f"Sets {sets}", style="dim")
    for index, (compound, laps) in enumerate(driver.used_tyre_stints):
        text.append(" ", style="dim")
        text.append_text(
            _tyre_badge(
                compound,
                new=(
                    index == len(driver.used_tyre_stints) - 1
                    and compound == driver.current_tyre
                    and bool(driver.current_tyre_new)
                ),
            )
        )
        if laps is not None:
            text.append(f" {laps}L", style="dim cyan")
    if not driver.used_tyre_stints and driver.used_tyre_compounds:
        text.append(" ", style="dim")
        text.append("/".join(driver.used_tyre_compounds), style="dim cyan")
    return text


def _tyre_badge(compound: str, *, new: bool = False) -> Text:
    label = compound or "-"
    style = TYRE_STYLE_MAP.get(label, "bold white on #3a4a53")
    badge = Text()
    badge.append(f" {label} ", style=style)
    if new:
        badge.append("*", style="bold cyan")
    return badge


def _summary_meta(snapshot: SessionSnapshot) -> Text:
    meta = Text()
    meta.append(f" {snapshot.badge} ", style=_summary_badge_style(snapshot.badge, snapshot.error))
    meta.append("  ")
    meta.append(snapshot.note, style="dim #90a4b8")
    return meta


def _summary_line_renderable(line: str) -> Text | Table:
    if line.startswith("Session: ") or line.startswith("Track: ") or line.startswith("Remain "):
        return _summary_row(_status_summary_chips(line), ratios=(1, 1, 1, 1))
    if line.startswith("Weather: "):
        return _summary_row(_weather_summary_chips(line), ratios=(1, 1, 1, 1))
    if line.startswith("Classification leader: ") or line.startswith("Order by single-lap pace"):
        chips = _running_order_chips(line)
        positions = (0, 2) if len(chips) >= 2 else (0,)
        return _summary_row(chips, ratios=(1, 1, 1, 1), positions=positions)
    if line.startswith("Best sectors: "):
        return _summary_row(_sector_summary_chips(line), ratios=(1, 1, 1, 2))
    if line.startswith("Race control: "):
        return _summary_row([_race_control_chip(line.removeprefix("Race control: "))], ratios=(1,))
    if line.startswith("Load error: "):
        return _summary_alert_line(line.removeprefix("Load error: "))
    return Text(line, style="white")


def _summary_row(
    chips: list[Text],
    *,
    ratios: tuple[int, ...],
    positions: tuple[int, ...] | None = None,
) -> Table | Text:
    if not chips:
        return Text("")

    column_count = max(len(chips), len(ratios))
    table = Table.grid(expand=True, padding=(0, 1))
    for index in range(column_count):
        ratio = ratios[index] if index < len(ratios) else 1
        table.add_column(ratio=ratio)

    row = [Text("") for _ in range(column_count)]
    if positions is None:
        positions = tuple(range(len(chips)))
    for chip, position in zip(chips, positions, strict=False):
        if 0 <= position < column_count:
            row[position] = chip
    table.add_row(*row)
    return table


def _status_summary_chips(line: str) -> list[Text]:
    chips: list[Text] = []
    for part in line.split(" | "):
        if part.startswith("Session: "):
            value = part.removeprefix("Session: ")
            chips.append(
                _summary_chip(
                    "Session",
                    value,
                    label_style="bold white on #334155",
                    value_style=_session_value_style(value),
                )
            )
        elif part.startswith("Track: "):
            value = part.removeprefix("Track: ")
            chips.append(
                _summary_chip(
                    "Track",
                    value,
                    label_style="bold white on #475569",
                    value_style=_track_value_style(value),
                )
            )
        elif part.startswith("Remain "):
            chips.append(
                _summary_chip(
                    "Remain",
                    part.removeprefix("Remain "),
                    label_style="bold white on #1d4ed8",
                    value_style="bold #bfdbfe",
                )
            )
        elif part.startswith("Laps "):
            chips.append(
                _summary_chip(
                    "Laps",
                    part.removeprefix("Laps "),
                    label_style="bold white on #4b5563",
                    value_style="bold #e5e7eb",
                )
            )
    return chips


def _weather_summary_chips(line: str) -> list[Text]:
    chips: list[Text] = []
    for part in line.removeprefix("Weather: ").split(" | "):
        label, _, value = part.partition(" ")
        if not value:
            continue
        normalized = label.lower()
        if normalized == "air":
            chips.append(
                _summary_chip(
                    "Air",
                    value,
                    label_style="bold black on #fb923c",
                    value_style="bold #fdba74",
                )
            )
        elif normalized == "track":
            chips.append(
                _summary_chip(
                    "Track Temp",
                    value,
                    label_style="bold black on #f97316",
                    value_style="bold #fdba74",
                )
            )
        elif normalized == "rain":
            chips.append(
                _summary_chip(
                    "Rain",
                    value,
                    label_style="bold white on #0369a1",
                    value_style=_rain_value_style(value),
                )
            )
        elif normalized == "wind":
            chips.append(
                _summary_chip(
                    "Wind",
                    value,
                    label_style="bold white on #1d4ed8",
                    value_style="bold #93c5fd",
                )
            )
    return chips


def _running_order_chips(line: str) -> list[Text]:
    chips: list[Text] = []
    for part in line.split(" | "):
        if part == "Order by single-lap pace":
            chips.append(
                _summary_chip(
                    "Order",
                    "Single-lap pace",
                    label_style="bold black on #22d3ee",
                    value_style="bold #a5f3fc",
                )
            )
        elif part.startswith("Classification leader: "):
            chips.append(
                _summary_chip(
                    "Leader",
                    part.removeprefix("Classification leader: "),
                    label_style="bold black on #14b8a6",
                    value_style="bold #99f6e4",
                )
            )
        elif part.startswith("Fastest lap: "):
            chips.append(
                _summary_chip(
                    "Fastest",
                    part.removeprefix("Fastest lap: "),
                    label_style="bold white on #a21caf",
                    value_style="bold #f0abfc",
                )
            )
    return chips


def _sector_summary_chips(line: str) -> list[Text]:
    chips: list[Text] = []
    parts = line.split(" | ")
    if parts:
        parts[0] = parts[0].removeprefix("Best sectors: ")

    for part in parts:
        if re.match(r"^S[123]\s", part):
            label, _, value = part.partition(" ")
            chips.append(
                _summary_chip(
                    label,
                    value,
                    label_style="bold white on #6d28d9",
                    value_style="bold #ddd6fe",
                )
            )
        elif part.startswith("RC: "):
            chips.append(_race_control_chip(part.removeprefix("RC: ")))
    return chips


def _race_control_chip(message: str) -> Text:
    return _summary_chip(
        "RC",
        message,
        label_style="bold black on #f59e0b",
        value_style=_race_control_value_style(message),
    )


def _summary_alert_line(message: str) -> Text:
    return _summary_chip(
        "Error",
        message,
        label_style="bold white on #b91c1c",
        value_style="bold #fecaca",
    )


def _summary_chip(
    label: str,
    value: str,
    *,
    label_style: str,
    value_style: str,
) -> Text:
    chip = Text()
    chip.append(f" {label.upper()} ", style=label_style)
    chip.append(" ")
    chip.append(value, style=value_style)
    return chip


def _summary_badge_style(badge: str, error: str | None) -> str:
    if error:
        return "bold white on #b91c1c"
    normalized = badge.lower()
    if any(token in normalized for token in ("live", "started", "progress")):
        return "bold black on #34d399"
    if any(token in normalized for token in ("complete", "final", "ended")):
        return "bold black on #e5e7eb"
    if "upcoming" in normalized:
        return "bold white on #2563eb"
    if "unavailable" in normalized:
        return "bold white on #6b7280"
    return "bold white on #334155"


def _session_value_style(value: str) -> str:
    normalized = value.lower()
    if any(token in normalized for token in ("started", "running", "green", "live")):
        return "bold #86efac"
    if any(token in normalized for token in ("final", "complete", "ended", "finished")):
        return "bold #f8fafc"
    if any(token in normalized for token in ("red", "stopped", "aborted", "suspended")):
        return "bold #fca5a5"
    if any(token in normalized for token in ("yellow", "safety", "vsc")):
        return "bold #fde68a"
    return "bold #cbd5e1"


def _track_value_style(value: str) -> str:
    normalized = value.lower()
    if "clear" in normalized or "green" in normalized:
        return "bold #86efac"
    if any(token in normalized for token in ("yellow", "safety", "vsc", "sc")):
        return "bold #fde68a"
    if any(token in normalized for token in ("red", "blocked")):
        return "bold #fca5a5"
    return "bold #cbd5e1"


def _rain_value_style(value: str) -> str:
    normalized = value.lower()
    if normalized in {"yes", "true", "wet"}:
        return "bold #bfdbfe"
    if normalized in {"no", "false", "dry"}:
        return "bold #e2e8f0"
    return "bold #cbd5e1"


def _race_control_value_style(value: str) -> str:
    normalized = value.lower()
    if any(
        token in normalized for token in ("red", "stopped", "incident", "penalty", "investigation")
    ):
        return "bold #fecaca"
    if any(token in normalized for token in ("yellow", "vsc", "safety car")):
        return "bold #fde68a"
    if "clear" in normalized:
        return "bold #86efac"
    return "bold #fed7aa"
