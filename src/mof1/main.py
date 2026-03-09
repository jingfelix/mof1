from __future__ import annotations

from contextlib import suppress
from datetime import UTC, datetime
from functools import partial

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Header, Select, Static, TabPane, TabbedContent
from textual.worker import WorkerState

from .models import (
    BootstrapPayload,
    CurrentContext,
    EventOption,
    HistoryCatalogPayload,
    HistoryEventPayload,
    SessionSelection,
    SessionSnapshot,
)
from .service import FastF1Service
from .widgets import render_driver_panels, render_summary


class F1TimingApp(App[None]):
    CSS_PATH = "app.tcss"
    BINDINGS = [
        Binding("r", "refresh_current", "Refresh Current"),
        Binding("shift+r", "refresh_history", "Refresh History"),
        Binding("q", "quit", "Quit"),
        Binding("ctrl+c", "quit", show=False),
    ]

    LOADING_VALUE = "__loading__"

    def __init__(self) -> None:
        super().__init__()
        self.service = FastF1Service()
        self.year_options = [(str(year), year) for year in self.service.available_years()]
        self.current_context: CurrentContext | None = None
        self.history_events: dict[str, EventOption] = {}
        self.history_sessions: dict[str, SessionSelection] = {}
        self.current_snapshot: SessionSnapshot | None = None
        self.history_snapshot: SessionSnapshot | None = None
        self._history_syncing = False
        self._bootstrapping = True
        self._current_ready = False
        self._history_ready = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with TabbedContent(initial="current", id="tabs"):
            with TabPane("Current", id="current"):
                with Vertical(id="current-view"):
                    yield Static("Loading current session...", id="current-summary")
                    with Horizontal(id="current-panels"):
                        yield Static("Loading...", id="current-left")
                        yield Static("Loading...", id="current-right")
            with TabPane("History", id="history"):
                with Vertical(id="history-view"):
                    with Horizontal(id="history-controls"):
                        yield Select(
                            self.year_options,
                            prompt="Season",
                            allow_blank=False,
                            value=self.year_options[0][1],
                            id="history-year",
                        )
                        yield Select(
                            [("Loading...", self.LOADING_VALUE)],
                            prompt="Event",
                            allow_blank=False,
                            value=self.LOADING_VALUE,
                            id="history-event",
                        )
                        yield Select(
                            [("Loading...", self.LOADING_VALUE)],
                            prompt="Session",
                            allow_blank=False,
                            value=self.LOADING_VALUE,
                            id="history-session",
                        )
                    yield Static("Loading history session...", id="history-summary")
                    with Horizontal(id="history-panels"):
                        yield Static("Loading...", id="history-left")
                        yield Static("Loading...", id="history-right")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "mof1"
        self._set_status(None)

        self._render_message(
            "current",
            "Loading the latest session from FastF1...",
        )
        self._render_message(
            "history",
            "Loading the default historical session...",
        )
        self._set_driver_loading("current", True)
        self._set_driver_loading("history", True)

        self.run_worker(
            self._load_bootstrap,
            name="bootstrap",
            group="bootstrap",
            thread=True,
            exclusive=True,
            exit_on_error=False,
        )
        self.set_interval(90, self._poll_current_refresh)

    def action_refresh_current(self) -> None:
        self._start_current_refresh(manual=True)

    def action_refresh_history(self) -> None:
        selection = self._selected_history_session()
        if selection is None:
            return
        self._set_driver_loading("history", True)
        self._set_status(f"Refreshing history: {selection.event_name} {selection.session_name}")
        self.run_worker(
            partial(self.service.load_session_snapshot, selection),
            name="history-refresh",
            group="history-refresh",
            thread=True,
            exclusive=True,
            exit_on_error=False,
        )

    def on_select_changed(self, event: Select.Changed) -> None:
        if self._history_syncing or self._bootstrapping:
            return

        if event.select.id == "history-year":
            year = event.value
            if not isinstance(year, int):
                return
            self._set_driver_loading("history", True)
            self._set_status(f"Loading season {year}")
            self.run_worker(
                partial(self._load_history_catalog, year),
                name="history-year",
                group="history-catalog",
                thread=True,
                exclusive=True,
                exit_on_error=False,
            )
        elif event.select.id == "history-event":
            event_key = event.value
            if event_key == self.LOADING_VALUE or not isinstance(event_key, str):
                return
            year = self.query_one("#history-year", Select).value
            if not isinstance(year, int):
                return
            self._set_driver_loading("history", True)
            self._set_status(f"Loading event {event_key}")
            self.run_worker(
                partial(self._load_history_event, year, event_key),
                name="history-event",
                group="history-event",
                thread=True,
                exclusive=True,
                exit_on_error=False,
            )
        elif event.select.id == "history-session":
            session_key = event.value
            if session_key == self.LOADING_VALUE or not isinstance(session_key, str):
                return
            selection = self.history_sessions.get(session_key)
            if selection is None:
                return
            self._set_driver_loading("history", True)
            self._set_status(
                f"Loading history: {selection.event_name} {selection.session_name}"
            )
            self.run_worker(
                partial(self.service.load_session_snapshot, selection),
                name="history-refresh",
                group="history-refresh",
                thread=True,
                exclusive=True,
                exit_on_error=False,
            )

    def on_worker_state_changed(self, event) -> None:
        if event.state == WorkerState.ERROR:
            self._handle_worker_error(event.worker.name or "worker", event.worker.error)
            return

        if event.state != WorkerState.SUCCESS:
            return

        name = event.worker.name or ""
        result = event.worker.result
        if name == "bootstrap" and isinstance(result, BootstrapPayload):
            self._apply_bootstrap(result)
        elif name == "current-refresh" and isinstance(result, SessionSnapshot):
            self._apply_current_snapshot(result)
        elif name == "history-year" and isinstance(result, HistoryCatalogPayload):
            self._apply_history_catalog(result)
        elif name == "history-event" and isinstance(result, HistoryEventPayload):
            self._apply_history_event(result)
        elif name == "history-refresh" and isinstance(result, SessionSnapshot):
            self._apply_history_snapshot(result)

    def _load_bootstrap(self) -> BootstrapPayload:
        current_context = self.service.current_context()
        current_snapshot = self.service.load_session_snapshot(
            current_context.target,
            badge_override=current_context.badge,
            note_override=current_context.note,
        )

        history_year = current_context.target.year
        history_events = self.service.list_events(history_year)
        history_selection = current_context.target
        history_event_key = str(history_selection.round_number)
        history_sessions = self.service.list_sessions(history_year, history_selection.round_number)
        history_session_key = history_selection.key
        history_snapshot = self.service.load_session_snapshot(history_selection)

        return BootstrapPayload(
            current_context=current_context,
            current_snapshot=current_snapshot,
            history_year=history_year,
            history_events=history_events,
            history_event_key=history_event_key,
            history_sessions=history_sessions,
            history_session_key=history_session_key,
            history_snapshot=history_snapshot,
        )

    def _load_history_catalog(self, year: int) -> HistoryCatalogPayload:
        events = self.service.list_events(year)
        selection = self.service.default_history_selection(year, now=datetime.now(UTC))
        sessions = self.service.list_sessions(year, selection.round_number)
        snapshot = self.service.load_session_snapshot(selection)
        return HistoryCatalogPayload(
            year=year,
            events=events,
            event_key=str(selection.round_number),
            sessions=sessions,
            session_key=selection.key,
            snapshot=snapshot,
        )

    def _load_history_event(self, year: int, event_key: str) -> HistoryEventPayload:
        round_number = int(event_key)
        sessions = self.service.list_sessions(year, round_number)
        selection = sessions[-1]
        snapshot = self.service.load_session_snapshot(selection)
        return HistoryEventPayload(
            year=year,
            event_key=event_key,
            sessions=sessions,
            session_key=selection.key,
            snapshot=snapshot,
        )

    def _start_current_refresh(self, *, manual: bool = False) -> None:
        context = self.service.current_context()
        self.current_context = context
        note = context.note if not manual else f"{context.note} Manual refresh at {datetime.now(UTC).strftime('%H:%M:%S')} UTC."
        if not self._current_ready:
            self._set_driver_loading("current", True)
        self._set_status(
            f"Refreshing current: {context.target.event_name} {context.target.session_name}"
        )
        self.run_worker(
            partial(
                self.service.load_session_snapshot,
                context.target,
                badge_override=context.badge,
                note_override=note,
            ),
            name="current-refresh",
            group="current-refresh",
            thread=True,
            exclusive=True,
            exit_on_error=False,
        )

    def _poll_current_refresh(self) -> None:
        self._start_current_refresh(manual=False)

    def _apply_bootstrap(self, payload: BootstrapPayload) -> None:
        self.current_context = payload.current_context
        self._apply_current_snapshot(payload.current_snapshot)

        year_select = self.query_one("#history-year", Select)
        event_select = self.query_one("#history-event", Select)
        session_select = self.query_one("#history-session", Select)

        self._history_syncing = True
        try:
            year_select.value = payload.history_year
            self.history_events = {event.key: event for event in payload.history_events}
            event_select.set_options([(event.label, event.key) for event in payload.history_events])
            event_select.value = payload.history_event_key

            self.history_sessions = {
                session.key: session for session in payload.history_sessions
            }
            session_select.set_options(
                [(session.label, session.key) for session in payload.history_sessions]
            )
            session_select.value = payload.history_session_key
        finally:
            self._history_syncing = False

        self._bootstrapping = False
        self._apply_history_snapshot(payload.history_snapshot)

    def _apply_current_snapshot(self, snapshot: SessionSnapshot) -> None:
        self._current_ready = True
        self.current_snapshot = snapshot
        self._set_status(None)
        self._render_snapshot("current", snapshot)
        self._set_driver_loading("current", False)

    def _apply_history_catalog(self, payload: HistoryCatalogPayload) -> None:
        event_select = self.query_one("#history-event", Select)
        session_select = self.query_one("#history-session", Select)

        self.history_events = {event.key: event for event in payload.events}
        self.history_sessions = {
            session.key: session for session in payload.sessions
        }

        self._history_syncing = True
        try:
            event_select.set_options([(event.label, event.key) for event in payload.events])
            event_select.value = payload.event_key
            session_select.set_options(
                [(session.label, session.key) for session in payload.sessions]
            )
            session_select.value = payload.session_key
        finally:
            self._history_syncing = False

        self._apply_history_snapshot(payload.snapshot)

    def _apply_history_event(self, payload: HistoryEventPayload) -> None:
        session_select = self.query_one("#history-session", Select)
        self.history_sessions = {
            session.key: session for session in payload.sessions
        }

        self._history_syncing = True
        try:
            session_select.set_options(
                [(session.label, session.key) for session in payload.sessions]
            )
            session_select.value = payload.session_key
        finally:
            self._history_syncing = False

        self._apply_history_snapshot(payload.snapshot)

    def _apply_history_snapshot(self, snapshot: SessionSnapshot) -> None:
        self._history_ready = True
        self.history_snapshot = snapshot
        self._set_status(None)
        self._render_snapshot("history", snapshot)
        self._set_driver_loading("history", False)

    def _selected_history_session(self) -> SessionSelection | None:
        value = self.query_one("#history-session", Select).value
        if not isinstance(value, str) or value == self.LOADING_VALUE:
            return None
        return self.history_sessions.get(value)

    def _handle_worker_error(self, worker_name: str, error: Exception | None) -> None:
        message = str(error) if error else "Unknown worker error"
        if worker_name == "bootstrap":
            self._bootstrapping = False
            self._set_driver_loading("current", False)
            self._set_driver_loading("history", False)
            if self.current_snapshot is not None:
                self._apply_current_snapshot(self.current_snapshot)
            elif not self._current_ready:
                self._render_message("current", message)
            if self.history_snapshot is not None:
                self._apply_history_snapshot(self.history_snapshot)
            elif not self._history_ready:
                self._render_message("history", message)
        elif worker_name.startswith("current"):
            self._set_driver_loading("current", False)
            if self.current_snapshot is not None:
                self._apply_current_snapshot(self.current_snapshot)
            elif not self._current_ready:
                self._render_message("current", message)
        else:
            self._set_driver_loading("history", False)
            if self.history_snapshot is not None:
                self._apply_history_snapshot(self.history_snapshot)
            elif not self._history_ready:
                self._render_message("history", message)
        self._set_status(f"Error: {message}")
        self.notify(message, severity="error")

    def _render_message(self, target: str, message: str) -> None:
        summary = self.query_one(f"#{target}-summary", Static)
        left = self.query_one(f"#{target}-left", Static)
        right = self.query_one(f"#{target}-right", Static)
        summary.update(message)
        left.update(message)
        right.update(message)

    def _set_status(self, message: str | None) -> None:
        self.sub_title = "mof1" if not message else f"mof1 | {message}"

    def _set_driver_loading(self, target: str, loading: bool) -> None:
        self.query_one(f"#{target}-left", Static).loading = loading
        self.query_one(f"#{target}-right", Static).loading = loading

    def on_resize(self) -> None:
        if self.current_snapshot is not None:
            self._render_snapshot("current", self.current_snapshot)
        if self.history_snapshot is not None:
            self._render_snapshot("history", self.history_snapshot)

    def _render_snapshot(self, target: str, snapshot: SessionSnapshot) -> None:
        left_widget = self.query_one(f"#{target}-left", Static)
        right_widget = self.query_one(f"#{target}-right", Static)
        self.query_one(f"#{target}-summary", Static).update(render_summary(snapshot))
        panel_width = left_widget.size.width or max(1, self.size.width // 2 - 4)
        compact = self._use_compact_driver_layout(panel_width)
        left_panel, right_panel = render_driver_panels(snapshot, compact=compact)
        left_widget.update(left_panel)
        right_widget.update(right_panel)

    @staticmethod
    def _use_compact_driver_layout(panel_width: int) -> bool:
        return panel_width != 0 and panel_width < 84


def main() -> None:
    with suppress(KeyboardInterrupt):
        F1TimingApp().run()


if __name__ == "__main__":
    main()
