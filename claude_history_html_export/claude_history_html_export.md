# Claude Code History HTML Exporter

`claude_history_html_export.py` scans Claude Code's local history in `~/.claude/projects`, lists sessions, lets you choose one, and writes a readable HTML export folder.

## What It Exports

For a selected session, the script reads:

- The main transcript JSONL: `~/.claude/projects/<project>/<session-id>.jsonl`
- Sidecar files next to the session: `~/.claude/projects/<project>/<session-id>/...`
- Tool result files such as `tool-results/*.txt`
- File backup snapshots: `~/.claude/file-history/<session-id>/...`

The HTML includes:

- Session metadata: title, id, project, git branch, timestamps, record counts
- A readable chronological transcript
- Tool calls and tool results
- Collapsed useful metadata events: attachments and file-history snapshots
- Links to related sidecar and file-history files
- Links to the raw JSONL and manifest files in the export folder

By default, noisy low-value records are hidden from the readable transcript:

- Empty `thinking` blocks
- Repeated `ai-title` records
- `last-prompt`, `mode`, `permission-mode`, and `queue-operation` records

Those records are still preserved in the raw `.jsonl` sidecar file.

## Output Files

If you export to:

```text
mdblank-reload-request-quota/
```

the script writes:

```text
mdblank-reload-request-quota/
  index.html
  session.jsonl
  manifest.json
  related/
```

The HTML is for reading. The JSONL is the exact raw Claude source transcript. The manifest records source paths, session metadata, record counts, and copied related files. The `related/` folder contains tool results and file-history backups.

For compatibility, if `--output` ends in `.html` or `.htm`, that suffix is removed and the remaining path is used as the export folder.

## Basic Usage

Run it with no arguments to list sessions and choose interactively:

```sh
python3 /Users/roger/Workspace/ai/documents/claude_history_html_export/claude_history_html_export.py
```

Filter to one project:

```sh
python3 /Users/roger/Workspace/ai/documents/claude_history_html_export/claude_history_html_export.py --project MDBlank
```

Search by title, prompt text, cwd, or session id:

```sh
python3 /Users/roger/Workspace/ai/documents/claude_history_html_export/claude_history_html_export.py --search "reload request quota"
```

Export one known session directly:

```sh
python3 /Users/roger/Workspace/ai/documents/claude_history_html_export/claude_history_html_export.py \
  --session 7bec6043 \
  --output /Users/roger/Desktop/mdblank-reload-request-quota
```

List only, without exporting:

```sh
python3 /Users/roger/Workspace/ai/documents/claude_history_html_export/claude_history_html_export.py --project MDBlank --list
```

Show every metadata event inline when you need a forensic export:

```sh
python3 /Users/roger/Workspace/ai/documents/claude_history_html_export/claude_history_html_export.py \
  --session 7bec6043 \
  --verbose-events
```

## Recommended Output Format

Use HTML as the primary reading format and keep JSONL as a separate file in the export folder. This avoids duplicating the full transcript inside the HTML while preserving the raw source exactly.

For a one-file export that duplicates the raw JSONL inside the HTML:

```sh
python3 /Users/roger/Workspace/ai/documents/claude_history_html_export/claude_history_html_export.py \
  --session 7bec6043 \
  --embed-raw-json
```

To also embed related tool-result/file-history contents inside the HTML:

```sh
python3 /Users/roger/Workspace/ai/documents/claude_history_html_export/claude_history_html_export.py \
  --session 7bec6043 \
  --embed-related
```

To write only HTML, without sidecar files:

```sh
python3 /Users/roger/Workspace/ai/documents/claude_history_html_export/claude_history_html_export.py \
  --session 7bec6043 \
  --no-sidecars
```

## Notes

- The script uses only Python's standard library.
- It does not modify Claude's history.
- It scans only direct `*.jsonl` session files under each project directory, not nested subagent logs as separate sessions.
- Related sidecar files are copied into the `related/` folder by default. They are embedded as text only when `--embed-related` is used, up to `--max-appendix-bytes` per file. The default is `2000000` bytes.
- Claude project directory names are encoded, so the script prefers the session's stored `cwd` metadata when displaying project names.
