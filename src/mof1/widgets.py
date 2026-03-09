from __future__ import annotations

from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .models import DriverSnapshot, SessionSnapshot, split_drivers


def render_summary(snapshot: SessionSnapshot) -> Panel:
    body = Group(
        Text(snapshot.subtitle, style="bold white"),
        Text(f"{snapshot.badge} | {snapshot.note}", style="cyan"),
        *[Text(line, style="white") for line in snapshot.summary_lines],
    )
    border_style = "red" if snapshot.error else "bright_blue"
    return Panel(body, title=snapshot.title, border_style=border_style)


def render_driver_panels(
    snapshot: SessionSnapshot,
    *,
    compact: bool = False,
) -> tuple[Panel, Panel]:
    left_drivers, right_drivers = split_drivers(snapshot.drivers)
    return (
        _render_driver_panel(
            _panel_title(left_drivers, "Left Column"),
            left_drivers,
            snapshot.error,
            compact=compact,
        ),
        _render_driver_panel(
            _panel_title(right_drivers, "Right Column"),
            right_drivers,
            snapshot.error,
            compact=compact,
        ),
    )


def _render_driver_panel(
    title: str,
    drivers: list[DriverSnapshot],
    error: str | None,
    *,
    compact: bool,
) -> Panel:
    table = Table(expand=True, box=None, pad_edge=False)
    table.add_column("Pos", justify="right", width=3, style="bold white", no_wrap=True)

    if compact:
        table.add_column("Entry", ratio=1, style="white")
    else:
        table.add_column("Driver", width=22, style="white")
        table.add_column("Timing", ratio=1, style="white")

    if not drivers:
        message = error or "No driver timing data for this selection."
        if compact:
            table.add_row("-", message)
        else:
            table.add_row("-", message, "")
    else:
        for driver in drivers:
            position = str(driver.position) if driver.position is not None else "-"
            if compact:
                table.add_row(position, _compact_entry(driver))
            else:
                table.add_row(position, _driver_identity(driver), _timing_block(driver))

    return Panel(Group(table, _legend()), title=title, border_style="white")


def _compact_entry(driver: DriverSnapshot) -> Text:
    content = _driver_identity(driver, team_width=28)
    content.append("\n")
    content.append_text(
        _timing_line(
            "Now",
            driver.current_sectors,
            driver.current_sector_statuses,
            driver.current_lap,
            driver.current_lap_status,
        )
    )
    content.append("\n")
    content.append_text(
        _timing_line(
            "Best",
            driver.best_sectors,
            driver.best_sector_statuses,
            driver.best_lap,
            driver.best_lap_status,
        )
    )
    return content


def _driver_identity(driver: DriverSnapshot, *, team_width: int = 17) -> Text:
    line = Text()
    line.append(driver.code, style="bold cyan")
    line.append("  ")
    line.append(_shorten(driver.team, team_width), style="white")
    return line


def _timing_block(driver: DriverSnapshot) -> Text:
    block = _timing_line(
        "Now",
        driver.current_sectors,
        driver.current_sector_statuses,
        driver.current_lap,
        driver.current_lap_status,
    )
    block.append("\n")
    block.append_text(
        _timing_line(
            "Best",
            driver.best_sectors,
            driver.best_sector_statuses,
            driver.best_lap,
            driver.best_lap_status,
        )
    )
    return block


def _timing_line(
    label: str,
    sectors: tuple[str, str, str],
    sector_statuses: tuple[str, str, str],
    lap: str,
    lap_status: str,
) -> Text:
    line = Text()
    line.append(f"{label:<5}", style="bold white")
    for index, value in enumerate(sectors, start=1):
        if index > 1:
            line.append(" | ", style="dim")
        _append_metric(line, value, sector_statuses[index - 1])
    line.append(" | ", style="dim")
    line.append("Lap ", style="dim")
    _append_metric(line, lap, lap_status)
    return line


def _append_metric(line: Text, value: str, status: str) -> None:
    line.append(value, style=_timing_style(status))


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
    if status == "Y":
        return "bold bright_yellow"
    return "dim"


def _shorten(value: str, width: int) -> str:
    if len(value) <= width:
        return value
    return value[: width - 1] + "..."


def _panel_title(drivers: list[DriverSnapshot], fallback: str) -> str:
    positions = [driver.position for driver in drivers if driver.position is not None]
    if not positions:
        return fallback
    return f"Positions {min(positions)}-{max(positions)}"
