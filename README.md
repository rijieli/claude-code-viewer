# claude-code-viewer

Minimalist local web app for browsing your Claude Code session history
(`~/.claude/projects`) and exporting any session as a self-contained HTML zip.

## Run

```sh
uv sync
uv run app.py
```

Open <http://127.0.0.1:8765>.

## Features

- Paginated session list (20 per page) with full-text search across metadata
  and transcript content.
- Two-pane session view: dialogue on the left, a swappable side panel on the
  right for tool calls, tool results, thinking blocks, system events, and
  session metadata.
- One-click **Download zip** — bundles `index.html` (same two-pane UX,
  self-contained), `session.jsonl`, `manifest.json`, and any related sidecar
  files.

## Layout

```
app.py            FastAPI routes
core.py           JSONL scanning + SessionInfo
web_render.py     two-pane HTML renderer (live view + exported index.html)
pyproject.toml
```
