# AGENTS.md

## Cursor Cloud specific instructions

This is a Python/Poetry project (**Media Organiser**) — an offline CLI tool and Flask web app that sorts media files into a Plex/Kodi directory structure.

### Services

| Service | Command | Port |
|---|---|---|
| **CLI** | `poetry run media-organiser SOURCE [DEST] [flags]` | N/A |
| **Flask web upload UI** | `IMPORT_DIR=/path/to/import poetry run flask --app media_organiser.web:app run --host 0.0.0.0 --port 6767` | 6767 |

### Key commands

- **Install deps:** `poetry install`
- **Run tests:** `poetry run pytest -q --maxfail=1 --disable-warnings`
- **Run tests with coverage:** `poetry run pytest --cov=media_organiser --cov-report=term --cov-report=xml`
- **CLI help:** `poetry run media-organiser --help`

### Notes

- No linter is configured in the project (no ruff, flake8, mypy, etc.).
- No database or external services are required — the tool is entirely filesystem-based.
- The test suite uses `tmp_path` fixtures; no cleanup is needed.
- Poetry must be on `PATH`; if not, add `$HOME/.local/bin` to `PATH`.
