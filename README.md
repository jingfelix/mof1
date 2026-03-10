# mof1

A terminal UI built with `FastF1` and `Textual` to show:

- the current F1 session status (or the latest completed session when nothing is live)
- historical session status for a selected season, event, and session
- a two-column driver board with driver code, team, current lap, and best lap

## Stack

- `uv` for project and dependency management
- `FastF1` for schedule and session timing data
- `Textual` for the terminal UI

## Run

```bash
uv sync
uv run mof1
```

## Checks

```bash
uv run ruff check .
uv run ruff format .
uv run ty check
uv run pytest
```

## Git Hooks

```bash
uv run prek install
uv run prek run --all-files
```

The hooks use `.pre-commit-config.yaml` and run `uv lock`, export `requirements.txt`, then run
`ruff`, `ty`, and `pytest`.

## Notes

- The first load can take longer because `FastF1` downloads and caches official timing data.
- `Current` in the driver board means the latest recorded lap for that driver.
- `Current` uses the anonymous F1 live timing feed when available; history stays on cached FastF1 data.
