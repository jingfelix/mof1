import asyncio
from datetime import datetime, timezone
from typing import Any, cast

from textual.widgets import Static

from mof1.app import F1TimingApp
from mof1.core.models import (
    CurrentContext,
    DriverSnapshot,
    EventOption,
    SessionSelection,
    SessionSnapshot,
)

UTC = timezone.utc


def test_refresh_interval_label() -> None:
    assert F1TimingApp._refresh_interval_label(0) == "manual only"
    assert F1TimingApp._refresh_interval_label(30) == "every 30 seconds"


def test_refresh_settings_text_mentions_history_loading() -> None:
    text = F1TimingApp._refresh_settings_text(team_display_mode="hide")

    assert "anonymous F1 live timing feed" in text
    assert "swatches only" in text
    assert "History stays manual" in text
    assert "reconnect current" in text


class _FakeService:
    def __init__(self) -> None:
        self.current_context_calls = 0
        self.load_session_snapshot_calls = 0
        self.run_live_timing_calls = 0
        self.selection = SessionSelection(
            key="2026:1:Practice 1",
            year=2026,
            round_number=1,
            event_name="Australian Grand Prix",
            session_name="Practice 1",
            start_utc=datetime(2026, 3, 8, 4, 0, tzinfo=UTC),
        )
        self.snapshot = SessionSnapshot(
            title="2026 Australian Grand Prix",
            subtitle="Practice 1 | 2026-03-08 04:00 UTC",
            badge="SESSION",
            note="Loaded at 2026-03-09 11:00 UTC.",
            summary_lines=("Session: Started | Track: AllClear",),
            drivers=(
                DriverSnapshot(
                    1,
                    "VER",
                    "Red Bull Racing",
                    "1:19.500",
                    "1:19.500",
                    ("24.100", "27.200", "28.200"),
                    ("24.100", "27.200", "28.150"),
                    ("P", "P", "Y"),
                    ("P", "P", "G"),
                    "P",
                    "P",
                    "",
                ),
            ),
            loaded_at_utc=datetime(2026, 3, 9, 11, 0, tzinfo=UTC),
        )

    def available_years(self):
        return (2026,)

    def current_context(self) -> CurrentContext:
        self.current_context_calls += 1
        return CurrentContext(
            target=self.selection,
            latest_started=self.selection,
            next_session=None,
            badge="LIVE WINDOW",
            note="Monitoring Australian Grand Prix Practice 1.",
        )

    def load_session_snapshot(self, *_args, **_kwargs) -> SessionSnapshot:
        self.load_session_snapshot_calls += 1
        return self.snapshot

    def run_live_timing(self, *_args, **_kwargs) -> None:
        self.run_live_timing_calls += 1

    def list_events(self, year: int):
        return (
            EventOption(
                key="1",
                year=year,
                round_number=1,
                name="Australian Grand Prix",
                location="Melbourne",
                event_date_utc=datetime(2026, 3, 8, tzinfo=UTC),
            ),
        )

    def list_sessions(self, _year: int, _round_number: int):
        return (self.selection,)

    def default_history_selection(self, _year: int, *, now=None):
        return self.selection


def test_app_mounts_with_live_disabled() -> None:
    async def run() -> None:
        app = F1TimingApp(service=cast(Any, _FakeService()), enable_live_current=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.current_snapshot is not None
            assert app.history_snapshot is not None
            settings = app.query_one("#settings-summary", Static)
            assert "anonymous F1 live timing feed" in str(settings.render())

    asyncio.run(run())


def test_app_mounts_with_live_enabled_without_fastf1_current_bootstrap() -> None:
    async def run() -> None:
        service = cast(Any, _FakeService())
        app = F1TimingApp(service=service, enable_live_current=True)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert service.current_context_calls == 0
            assert service.load_session_snapshot_calls == 1
            assert service.run_live_timing_calls == 1
            assert app.current_snapshot is None
            assert app.history_snapshot is not None
            current_summary = app.query_one("#current-summary", Static)
            assert "Connecting current live timing feed" in str(current_summary.render())

    asyncio.run(run())
