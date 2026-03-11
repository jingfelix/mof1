from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from datetime import datetime, timezone
from functools import partial
from threading import Event, Thread, current_thread
from typing import Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Footer, Header, Select, Static, TabbedContent, TabPane

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
from .widgets import render_driver_panel, render_summary

UTC = timezone.utc


class F1TimingApp(App[None]):
    CSS_PATH = "app.tcss"
    DEFAULT_TEAM_DISPLAY_MODE = "hide"
    TEAM_DISPLAY_OPTIONS = [
        ("Hide team names", "hide"),
        ("Show team names", "show"),
    ]
    BINDINGS = [
        Binding("r", "refresh_current", "Refresh Current"),
        Binding("shift+r", "refresh_history", "Refresh History"),
        Binding("q", "quit", "Quit"),
        Binding("ctrl+c", "quit", show=False),
    ]

    LOADING_VALUE = "__loading__"

    def __init__(
        self,
        service: FastF1Service | None = None,
        *,
        enable_live_current: bool = True,
    ) -> None:
        super().__init__()
        self.service = service or FastF1Service()
        self.enable_live_current = enable_live_current
        self.year_options = [(str(year), year) for year in self.service.available_years()]
        self.team_display_mode = self.DEFAULT_TEAM_DISPLAY_MODE
        self.current_context: CurrentContext | None = None
        self.history_events: dict[str, EventOption] = {}
        self.history_sessions: dict[str, SessionSelection] = {}
        self.current_snapshot: SessionSnapshot | None = None
        self.history_snapshot: SessionSnapshot | None = None
        self._current_live_thread: Thread | None = None
        self._current_live_stop: Event | None = None
        self._live_status_message: str | None = None
        self._transient_status_message: str | None = None
        self._history_syncing = False
        self._bootstrapping = True
        self._current_ready = False
        self._history_ready = False
        self._background_job_tokens: dict[str, int] = {}
        self._shutting_down = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with TabbedContent(initial="current", id="tabs"):
            with TabPane("Current", id="current"):
                with Vertical(id="current-view"):
                    yield Static("Loading current session...", id="current-summary")
                    with VerticalScroll(id="current-drivers-scroll"):
                        yield Static("Loading...", id="current-drivers")
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
                    with VerticalScroll(id="history-drivers-scroll"):
                        yield Static("Loading...", id="history-drivers")
            with TabPane("Settings", id="settings"):
                with Vertical(id="settings-view"):
                    yield Static("", id="settings-summary")
                    with Horizontal(id="settings-controls"):
                        yield Select(
                            self.TEAM_DISPLAY_OPTIONS,
                            prompt="Team Display",
                            allow_blank=False,
                            value=self.team_display_mode,
                            id="settings-team-display",
                        )
        yield Footer()

    def on_mount(self) -> None:
        self.title = "mof1"
        self._set_status(None)
        self.query_one("#settings-summary", Static).update(
            self._refresh_settings_text(team_display_mode=self.team_display_mode)
        )

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

        self._start_background_task(
            name="bootstrap",
            group="bootstrap",
            work=self._load_bootstrap,
        )

    def on_unmount(self) -> None:
        self._cancel_background_tasks()
        self._stop_current_live_feed(clear_status=True)

    async def action_quit(self) -> None:
        self._cancel_background_tasks()
        self._stop_current_live_feed(clear_status=True)
        self.exit()

    def action_refresh_current(self) -> None:
        self._start_current_refresh(manual=True)

    def action_refresh_history(self) -> None:
        selection = self._selected_history_session()
        if selection is None:
            return
        self._set_driver_loading("history", True)
        self._set_status(f"Refreshing history: {selection.event_name} {selection.session_name}")
        self._start_background_task(
            name="history-refresh",
            group="history-refresh",
            work=partial(self.service.load_session_snapshot, selection),
        )

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "settings-team-display":
            if isinstance(event.value, str):
                self._apply_team_display_mode(event.value, notify=True)
            return

        if self._history_syncing or self._bootstrapping:
            return

        if event.select.id == "history-year":
            year = event.value
            if not isinstance(year, int):
                return
            self._set_driver_loading("history", True)
            self._set_status(f"Loading season {year}")
            self._start_background_task(
                name="history-year",
                group="history-catalog",
                work=partial(self._load_history_catalog, year),
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
            self._start_background_task(
                name="history-event",
                group="history-event",
                work=partial(self._load_history_event, year, event_key),
            )
        elif event.select.id == "history-session":
            session_key = event.value
            if session_key == self.LOADING_VALUE or not isinstance(session_key, str):
                return
            selection = self.history_sessions.get(session_key)
            if selection is None:
                return
            self._set_driver_loading("history", True)
            self._set_status(f"Loading history: {selection.event_name} {selection.session_name}")
            self._start_background_task(
                name="history-refresh",
                group="history-refresh",
                work=partial(self.service.load_session_snapshot, selection),
            )

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
        if self.enable_live_current:
            self._restart_current_live_feed(manual=manual)
            return

        context = self.service.current_context()
        self.current_context = context
        note = (
            context.note
            if not manual
            else f"{context.note} Manual refresh at {datetime.now(UTC).strftime('%H:%M:%S')} UTC."
        )
        if not self._current_ready:
            self._set_driver_loading("current", True)
        self._set_status(
            f"Refreshing current: {context.target.event_name} {context.target.session_name}"
        )
        self._start_background_task(
            name="current-refresh",
            group="current-refresh",
            work=partial(
                self.service.load_session_snapshot,
                context.target,
                badge_override=context.badge,
                note_override=note,
            ),
        )

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

            self.history_sessions = {session.key: session for session in payload.history_sessions}
            session_select.set_options(
                [(session.label, session.key) for session in payload.history_sessions]
            )
            session_select.value = payload.history_session_key
        finally:
            self._history_syncing = False

        self._bootstrapping = False
        self._apply_history_snapshot(payload.history_snapshot)
        if self.enable_live_current:
            self._restart_current_live_feed(manual=False)

    def _apply_current_snapshot(self, snapshot: SessionSnapshot) -> None:
        self._current_ready = True
        self.current_snapshot = snapshot
        self._render_snapshot("current", snapshot)
        self._set_driver_loading("current", False)

    def _apply_history_catalog(self, payload: HistoryCatalogPayload) -> None:
        event_select = self.query_one("#history-event", Select)
        session_select = self.query_one("#history-session", Select)

        self.history_events = {event.key: event for event in payload.events}
        self.history_sessions = {session.key: session for session in payload.sessions}

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
        self.history_sessions = {session.key: session for session in payload.sessions}

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

    def _start_background_task(
        self,
        *,
        name: str,
        group: str,
        work: Callable[[], object],
    ) -> None:
        token = self._background_job_tokens.get(group, 0) + 1
        self._background_job_tokens[group] = token
        thread = Thread(
            target=self._run_background_task,
            args=(name, group, token, work),
            name=f"mof1-{name}",
            daemon=True,
        )
        thread.start()

    def _run_background_task(
        self,
        name: str,
        group: str,
        token: int,
        work: Callable[[], object],
    ) -> None:
        try:
            result = work()
        except Exception as error:
            self._call_from_live_thread(
                self._finish_background_task,
                name,
                group,
                token,
                None,
                error,
            )
            return

        self._call_from_live_thread(
            self._finish_background_task,
            name,
            group,
            token,
            result,
            None,
        )

    def _finish_background_task(
        self,
        name: str,
        group: str,
        token: int,
        result: object | None,
        error: Exception | None,
    ) -> None:
        if self._shutting_down:
            return
        if token != self._background_job_tokens.get(group):
            return
        if error is not None:
            self._handle_worker_error(name, error)
            return
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

    def _cancel_background_tasks(self) -> None:
        self._shutting_down = True
        for group, token in list(self._background_job_tokens.items()):
            self._background_job_tokens[group] = token + 1

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
        drivers = self.query_one(f"#{target}-drivers", Static)
        summary.update(message)
        drivers.update(message)

    def _set_status(self, message: str | None) -> None:
        self._transient_status_message = message
        self._refresh_sub_title()

    def _set_live_status(self, message: str | None) -> None:
        self._live_status_message = message
        self._refresh_sub_title()

    def _refresh_sub_title(self) -> None:
        message = self._transient_status_message or self._live_status_message
        self.sub_title = "mof1" if not message else f"mof1 | {message}"

    def _set_driver_loading(self, target: str, loading: bool) -> None:
        self.query_one(f"#{target}-drivers-scroll", VerticalScroll).loading = loading

    def _apply_team_display_mode(self, mode: str, *, notify: bool = False) -> None:
        if mode not in {"show", "hide"}:
            return

        self.team_display_mode = mode
        self.query_one("#settings-summary", Static).update(
            self._refresh_settings_text(
                team_display_mode=self.team_display_mode,
            )
        )
        if self.current_snapshot is not None:
            self._render_snapshot("current", self.current_snapshot)
        if self.history_snapshot is not None:
            self._render_snapshot("history", self.history_snapshot)
        if notify:
            self.notify(
                f"Team display: {self._team_display_label(mode)}",
                severity="information",
            )

    @staticmethod
    def _refresh_interval_label(seconds: int) -> str:
        if seconds <= 0:
            return "manual only"
        return f"every {seconds} seconds"

    @classmethod
    def _refresh_settings_text(
        cls,
        seconds: int | None = None,
        *,
        team_display_mode: str = DEFAULT_TEAM_DISPLAY_MODE,
    ) -> str:
        team_display = cls._team_display_label(team_display_mode)
        return (
            "Current tab uses the anonymous F1 live timing feed.\n"
            f"Driver column uses {team_display}.\n"
            "History stays manual and shows the loading indicator while it fetches.\n"
            "Use `r` to reconnect current and `Shift+R` for history."
        )

    @staticmethod
    def _team_display_label(mode: str) -> str:
        return "team names" if mode == "show" else "swatches only"

    def on_resize(self) -> None:
        if self.current_snapshot is not None:
            self._render_snapshot("current", self.current_snapshot)
        if self.history_snapshot is not None:
            self._render_snapshot("history", self.history_snapshot)

    def _render_snapshot(self, target: str, snapshot: SessionSnapshot) -> None:
        drivers_widget = self.query_one(f"#{target}-drivers", Static)
        self.query_one(f"#{target}-summary", Static).update(render_summary(snapshot))
        panel_width = drivers_widget.size.width or max(1, self.size.width - 8)
        compact = self._use_compact_driver_layout(panel_width)
        driver_panel = render_driver_panel(
            snapshot,
            panel_width=panel_width,
            compact=compact,
            team_display_mode=self.team_display_mode,
        )
        drivers_widget.update(driver_panel)

    @staticmethod
    def _use_compact_driver_layout(panel_width: int) -> bool:
        return panel_width != 0 and panel_width < 84

    def _restart_current_live_feed(self, *, manual: bool) -> None:
        self._stop_current_live_feed(clear_status=False)

        if not self._current_ready:
            self._set_driver_loading("current", True)

        self._set_live_status(
            "Reconnecting current live feed..." if manual else "Connecting current live feed..."
        )
        stop_event = Event()
        thread = Thread(
            target=self._run_current_live_feed,
            args=(stop_event,),
            name="mof1-current-live",
            daemon=True,
        )
        self._current_live_stop = stop_event
        self._current_live_thread = thread
        thread.start()

    def _stop_current_live_feed(self, *, clear_status: bool) -> None:
        stop_event = self._current_live_stop
        thread = self._current_live_thread
        self._current_live_stop = None
        self._current_live_thread = None

        if stop_event is not None:
            stop_event.set()
        if thread is not None and thread.is_alive() and thread is not current_thread():
            thread.join(timeout=2.0)

        if clear_status:
            self._set_live_status(None)

    def _run_current_live_feed(self, stop_event: Event) -> None:
        def on_snapshot(snapshot: SessionSnapshot) -> None:
            self._call_from_live_thread(self._apply_current_snapshot, snapshot)

        def on_status(message: str | None) -> None:
            self._call_from_live_thread(self._set_live_status, message)

        try:
            self.service.run_live_timing(
                stop_event,
                on_snapshot=on_snapshot,
                on_status=on_status,
            )
        except Exception as exc:
            if stop_event.is_set():
                return
            self._call_from_live_thread(
                self._handle_current_live_runtime_error,
                str(exc),
            )

    def _call_from_live_thread(self, callback: Any, *args: Any) -> None:
        try:
            self.call_from_thread(callback, *args)
        except RuntimeError:
            pass

    def _handle_current_live_runtime_error(self, message: str) -> None:
        self._set_live_status(f"Current live feed unavailable: {message}")
        if self.current_snapshot is None and not self._current_ready:
            self._render_message("current", message)
            self._set_driver_loading("current", False)


def main() -> None:
    with suppress(KeyboardInterrupt):
        F1TimingApp().run()


if __name__ == "__main__":
    main()
