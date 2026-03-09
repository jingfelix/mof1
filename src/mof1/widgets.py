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


def render_driver_panels(snapshot: SessionSnapshot) -> tuple[Panel, Panel]:
    left_drivers, right_drivers = split_drivers(snapshot.drivers)
    return (
        _render_driver_panel(_panel_title(left_drivers, "Left Column"), left_drivers, snapshot.error),
        _render_driver_panel(_panel_title(right_drivers, "Right Column"), right_drivers, snapshot.error),
    )


def _render_driver_panel(
    title: str,
    drivers: list[DriverSnapshot],
    error: str | None,
) -> Panel:
    table = Table(expand=True, box=None, pad_edge=False)
    table.add_column("Pos", justify="right", width=3, style="bold white")
    table.add_column("Drv", width=5, style="bold cyan")
    table.add_column("Team", ratio=2, style="white")
    table.add_column("Current", justify="right", width=11, style="yellow")
    table.add_column("Best", justify="right", width=11, style="green")

    if not drivers:
        message = error or "No driver timing data for this selection."
        table.add_row("-", "-", message, "-", "-")
    else:
        for driver in drivers:
            table.add_row(
                str(driver.position) if driver.position is not None else "-",
                driver.code,
                _shorten(driver.team, 20),
                driver.current_lap,
                driver.best_lap,
            )

    subtitle = Text("Current = latest recorded lap", style="dim")
    return Panel(Group(table, subtitle), title=title, border_style="white")


def _shorten(value: str, width: int) -> str:
    if len(value) <= width:
        return value
    return value[: width - 1] + "..."


def _panel_title(drivers: list[DriverSnapshot], fallback: str) -> str:
    positions = [driver.position for driver in drivers if driver.position is not None]
    if not positions:
        return fallback
    return f"Positions {min(positions)}-{max(positions)}"
