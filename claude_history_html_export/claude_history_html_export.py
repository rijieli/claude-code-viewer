#!/usr/bin/env python3
"""
Export Claude Code session history to a readable HTML folder.

The script scans ~/.claude/projects, lists direct per-project session JSONL files,
lets you choose one, and writes an output folder with index.html for reading,
session.jsonl for raw source, manifest.json, and a related-files folder for
tool-results/file-history backups.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


SESSION_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


@dataclass
class SessionInfo:
    session_id: str
    project_label: str
    project_path: Path
    jsonl_path: Path
    sidecar_path: Path
    file_history_path: Path
    title: str
    first_prompt: str
    last_prompt: str
    cwd: str
    git_branch: str
    first_timestamp: str
    last_timestamp: str
    mtime: float
    line_count: int
    type_counts: dict[str, int]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="List and export Claude Code session history as an HTML folder."
    )
    parser.add_argument(
        "--claude-dir",
        default=str(Path.home() / ".claude"),
        help="Claude config/history directory. Default: ~/.claude",
    )
    parser.add_argument(
        "--project",
        help="Filter sessions by project label/path substring, e.g. MDBlank.",
    )
    parser.add_argument(
        "--search",
        help="Filter sessions by title, prompt, cwd, or session id substring.",
    )
    parser.add_argument(
        "--session",
        help="Export this session id directly instead of prompting.",
    )
    parser.add_argument(
        "--output",
        "-o",
        help=(
            "Output folder. If the path ends in .html/.htm, that suffix is removed. "
            "Default: ./claude-<title>-<id>/"
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Limit displayed sessions in interactive mode. 0 means all.",
    )
    parser.add_argument(
        "--no-raw-json",
        action="store_true",
        help="Compatibility no-op. Raw JSONL is written as a sidecar by default.",
    )
    parser.add_argument(
        "--embed-raw-json",
        action="store_true",
        help="Also embed the raw source JSONL inside the HTML.",
    )
    parser.add_argument(
        "--embed-related",
        action="store_true",
        help="Also embed related tool-result/file-history file contents inside the HTML.",
    )
    parser.add_argument(
        "--no-sidecars",
        action="store_true",
        help="Only write index.html. By default, raw JSONL, manifest, and related files are written into the export folder.",
    )
    parser.add_argument(
        "--verbose-events",
        action="store_true",
        help=(
            "Show low-value metadata events in the transcript, including ai-title, "
            "last-prompt, mode, permission-mode, and queue-operation records."
        ),
    )
    parser.add_argument(
        "--max-appendix-bytes",
        type=int,
        default=2_000_000,
        help="Per-file text embed limit for related sidecar files. Default: 2000000.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Only list sessions; do not export.",
    )
    return parser.parse_args()


def read_jsonl(path: Path) -> Iterable[tuple[int, dict[str, Any]]]:
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line_no, line in enumerate(handle, 1):
            line = line.rstrip("\n")
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                yield line_no, {
                    "type": "json-decode-error",
                    "error": str(exc),
                    "raw": line,
                }
                continue
            if isinstance(value, dict):
                yield line_no, value


def one_line(text: str, limit: int = 110) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "..."


def content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                block_type = item.get("type", "")
                if block_type == "text":
                    parts.append(str(item.get("text", "")))
                elif block_type == "tool_result":
                    parts.append(str(item.get("content", "")))
                elif block_type == "tool_use":
                    name = item.get("name", "tool")
                    parts.append(f"[tool_use: {name}]")
                else:
                    parts.append(str(item.get("text") or item.get("content") or block_type))
        return "\n".join(part for part in parts if part)
    if content is None:
        return ""
    return json.dumps(content, ensure_ascii=False, indent=2)


def is_real_user_prompt(event: dict[str, Any]) -> bool:
    if event.get("type") != "user":
        return False
    if event.get("isMeta"):
        return False
    content = event.get("message", {}).get("content")
    text = content_text(content).strip()
    if not text:
        return False
    ignored_prefixes = (
        "<local-command-caveat>",
        "[Request interrupted by user]",
        "Base directory for this skill:",
    )
    return not text.startswith(ignored_prefixes)


def scan_session(
    jsonl_path: Path, claude_dir: Path, project_path: Path
) -> SessionInfo:
    session_id = jsonl_path.stem
    project_label = project_path.name
    sidecar_path = project_path / session_id
    file_history_path = claude_dir / "file-history" / session_id
    type_counts: dict[str, int] = {}
    title = ""
    first_prompt = ""
    last_prompt = ""
    cwd = ""
    git_branch = ""
    first_timestamp = ""
    last_timestamp = ""
    line_count = 0

    for line_no, event in read_jsonl(jsonl_path):
        line_count = line_no
        event_type = str(event.get("type", ""))
        type_counts[event_type] = type_counts.get(event_type, 0) + 1

        if event.get("timestamp"):
            if not first_timestamp:
                first_timestamp = str(event["timestamp"])
            last_timestamp = str(event["timestamp"])
        if event.get("cwd") and not cwd:
            cwd = str(event["cwd"])
        if event.get("gitBranch") and not git_branch:
            git_branch = str(event["gitBranch"])
        if event_type == "ai-title" and event.get("aiTitle"):
            title = str(event["aiTitle"])
        if event_type == "last-prompt" and event.get("lastPrompt"):
            last_prompt = str(event["lastPrompt"])
        if not first_prompt and is_real_user_prompt(event):
            first_prompt = content_text(event.get("message", {}).get("content"))

    return SessionInfo(
        session_id=session_id,
        project_label=project_label,
        project_path=project_path,
        jsonl_path=jsonl_path,
        sidecar_path=sidecar_path,
        file_history_path=file_history_path,
        title=title or "(no title)",
        first_prompt=one_line(first_prompt, 160),
        last_prompt=one_line(last_prompt, 160),
        cwd=cwd,
        git_branch=git_branch,
        first_timestamp=first_timestamp,
        last_timestamp=last_timestamp,
        mtime=jsonl_path.stat().st_mtime,
        line_count=line_count,
        type_counts=type_counts,
    )


def scan_sessions(claude_dir: Path) -> list[SessionInfo]:
    projects_dir = claude_dir / "projects"
    if not projects_dir.is_dir():
        raise SystemExit(f"No Claude projects directory found: {projects_dir}")

    sessions: list[SessionInfo] = []
    for project_path in sorted(path for path in projects_dir.iterdir() if path.is_dir()):
        for jsonl_path in sorted(project_path.glob("*.jsonl")):
            if SESSION_ID_RE.match(jsonl_path.stem):
                sessions.append(scan_session(jsonl_path, claude_dir, project_path))
    sessions.sort(key=lambda item: item.mtime, reverse=True)
    return sessions


def filter_sessions(
    sessions: list[SessionInfo], project: str | None, search: str | None
) -> list[SessionInfo]:
    result = sessions
    if project:
        needle = project.lower()
        result = [
            item
            for item in result
            if needle in item.project_label.lower() or needle in item.cwd.lower()
        ]
    if search:
        needle = search.lower()
        result = [
            item
            for item in result
            if needle in " ".join(
                [
                    item.session_id,
                    item.project_label,
                    item.title,
                    item.first_prompt,
                    item.last_prompt,
                    item.cwd,
                ]
            ).lower()
        ]
    return result


def local_time(epoch_seconds: float) -> str:
    return dt.datetime.fromtimestamp(epoch_seconds).strftime("%Y-%m-%d %H:%M")


def print_sessions(sessions: list[SessionInfo], limit: int = 0) -> None:
    shown = sessions if limit <= 0 else sessions[:limit]
    for index, item in enumerate(shown, 1):
        project = item.cwd or item.project_label
        print(
            f"{index:4d}  {local_time(item.mtime)}  "
            f"{item.session_id[:8]}  {item.line_count:5d}  "
            f"{project}  {item.title}"
        )
        if item.first_prompt:
            print(f"      {item.first_prompt}")
    if limit > 0 and len(sessions) > limit:
        print(f"\nShowing {limit} of {len(sessions)} sessions. Use --limit 0 for all.")


def choose_session(sessions: list[SessionInfo], limit: int = 0) -> SessionInfo:
    if not sessions:
        raise SystemExit("No sessions matched.")
    print_sessions(sessions, limit=limit)
    visible = sessions if limit <= 0 else sessions[:limit]
    while True:
        raw = input("\nChoose session number, UUID prefix, or q: ").strip()
        if raw.lower() in {"q", "quit", "exit"}:
            raise SystemExit(0)
        if raw.isdigit():
            index = int(raw)
            if 1 <= index <= len(visible):
                return visible[index - 1]
        matches = [item for item in sessions if item.session_id.startswith(raw)]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            print("Multiple sessions match that prefix; type more characters.")
        else:
            print("No match. Try a visible number or a longer UUID prefix.")


def html_escape(text: Any) -> str:
    return html.escape("" if text is None else str(text), quote=False)


def render_json(value: Any) -> str:
    return html_escape(json.dumps(value, ensure_ascii=False, indent=2))


def render_block_text(text: str) -> str:
    return f"<pre>{html_escape(text)}</pre>"


def render_tool_use(block: dict[str, Any]) -> str:
    name = html_escape(block.get("name", "tool"))
    tool_id = html_escape(block.get("id", ""))
    input_value = block.get("input", {})
    command = input_value.get("command") if isinstance(input_value, dict) else None
    description = input_value.get("description") if isinstance(input_value, dict) else None
    body = []
    if description:
        body.append(f"<p>{html_escape(description)}</p>")
    if command:
        body.append(render_block_text(str(command)))
    else:
        body.append(f"<pre>{render_json(input_value)}</pre>")
    return (
        '<div class="tool-block">'
        f'<div class="tool-title">Tool use: {name} <span>{tool_id}</span></div>'
        + "".join(body)
        + "</div>"
    )


def render_tool_result(block: dict[str, Any], tool_result: Any = None) -> str:
    tool_id = html_escape(block.get("tool_use_id", ""))
    parts: list[str] = []
    content = block.get("content")
    if content:
        parts.append(render_block_text(content_text(content)))
    if isinstance(tool_result, dict):
        stdout = tool_result.get("stdout")
        stderr = tool_result.get("stderr")
        file_value = tool_result.get("file")
        if stdout:
            parts.append("<h4>stdout</h4>" + render_block_text(str(stdout)))
        if stderr:
            parts.append("<h4>stderr</h4>" + render_block_text(str(stderr)))
        if file_value:
            parts.append("<h4>file result</h4>" + f"<pre>{render_json(file_value)}</pre>")
    if not parts:
        parts.append("<p class=\"muted\">No visible result content.</p>")
    return (
        '<div class="tool-block tool-result">'
        f'<div class="tool-title">Tool result <span>{tool_id}</span></div>'
        + "".join(parts)
        + "</div>"
    )


def render_message_content(event: dict[str, Any]) -> str:
    message = event.get("message", {})
    content = message.get("content")
    tool_result = event.get("toolUseResult")

    if isinstance(content, list):
        fragments: list[str] = []
        for block in content:
            if isinstance(block, str):
                fragments.append(render_block_text(block))
            elif isinstance(block, dict):
                block_type = block.get("type")
                if block_type == "text":
                    fragments.append(f"<div class=\"text\">{render_markdownish(block.get('text', ''))}</div>")
                elif block_type == "tool_use":
                    fragments.append(render_tool_use(block))
                elif block_type == "tool_result":
                    fragments.append(render_tool_result(block, tool_result=tool_result))
                elif block_type == "thinking":
                    thinking_text = content_text(
                        block.get("thinking") or block.get("text") or ""
                    ).strip()
                    if thinking_text:
                        fragments.append(
                            '<details class="thinking"><summary>thinking</summary>'
                            + render_block_text(thinking_text)
                            + "</details>"
                        )
                else:
                    fragments.append(f"<pre>{render_json(block)}</pre>")
        return "\n".join(fragments)

    if isinstance(content, str):
        return f"<div class=\"text\">{render_markdownish(content)}</div>"

    if content is None and tool_result is not None:
        return render_tool_result({}, tool_result=tool_result)

    return f"<pre>{render_json(content)}</pre>"


def has_visible_message_content(event: dict[str, Any]) -> bool:
    message = event.get("message", {})
    content = message.get("content")
    if isinstance(content, str):
        return bool(content.strip())
    if isinstance(content, list):
        for block in content:
            if isinstance(block, str) and block.strip():
                return True
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type == "thinking":
                thinking_text = content_text(
                    block.get("thinking") or block.get("text") or ""
                ).strip()
                if thinking_text:
                    return True
            elif block_type in {"text", "tool_result"}:
                if content_text(block.get("text") or block.get("content") or "").strip():
                    return True
            elif block_type == "tool_use":
                return True
            elif content_text(block).strip():
                return True
        return event.get("toolUseResult") is not None
    return content is not None or event.get("toolUseResult") is not None


def should_render_event(event: dict[str, Any], verbose_events: bool) -> bool:
    event_type = str(event.get("type", ""))
    if event_type in {"user", "assistant", "system"}:
        return has_visible_message_content(event)
    if event_type in {"ai-title", "last-prompt", "mode", "permission-mode", "queue-operation"}:
        return verbose_events
    return True


def render_markdownish(text: str) -> str:
    """Lightweight text renderer that preserves code fences without dependencies."""
    text = str(text)
    parts: list[str] = []
    pattern = re.compile(r"```([A-Za-z0-9_+.-]*)\n(.*?)```", re.DOTALL)
    last = 0
    for match in pattern.finditer(text):
        before = text[last : match.start()]
        if before:
            parts.append(paragraphs(before))
        lang = html_escape(match.group(1))
        code = html_escape(match.group(2).rstrip("\n"))
        label = f'<div class="code-lang">{lang}</div>' if lang else ""
        parts.append(f'<div class="code-wrap">{label}<pre><code>{code}</code></pre></div>')
        last = match.end()
    rest = text[last:]
    if rest:
        parts.append(paragraphs(rest))
    return "".join(parts)


def paragraphs(text: str) -> str:
    chunks = re.split(r"\n{2,}", text.strip("\n"))
    rendered: list[str] = []
    for chunk in chunks:
        if not chunk:
            continue
        escaped = html_escape(chunk)
        escaped = escaped.replace("\n", "<br>")
        rendered.append(f"<p>{escaped}</p>")
    return "".join(rendered)


def render_event(event: dict[str, Any], line_no: int) -> str:
    event_type = str(event.get("type", ""))
    timestamp = html_escape(event.get("timestamp", ""))
    uuid = html_escape(event.get("uuid", ""))

    if event_type in {"user", "assistant", "system"}:
        role = event_type
        if event_type == "user":
            origin = event.get("origin", {}).get("kind") if isinstance(event.get("origin"), dict) else ""
            if origin:
                role = f"user ({origin})"
        body = render_message_content(event)
        return (
            f'<article class="message {html_escape(event_type)}" id="line-{line_no}">'
            f'<header><strong>{html_escape(role)}</strong>'
            f'<span>{timestamp}</span><a href="#line-{line_no}">#{line_no}</a></header>'
            f"{body}"
            f'<footer>{uuid}</footer>'
            "</article>"
        )

    if event_type in {"ai-title", "last-prompt", "mode", "permission-mode", "queue-operation"}:
        return (
            f'<details class="event meta" id="line-{line_no}">'
            f"<summary>{html_escape(event_type)} {timestamp} #{line_no}</summary>"
            f"<pre>{render_json(event)}</pre>"
            "</details>"
        )

    if event_type in {"attachment", "file-history-snapshot"}:
        summary = event_type
        if event_type == "attachment":
            attachment = event.get("attachment")
            if isinstance(attachment, dict) and attachment.get("type"):
                summary += f": {attachment['type']}"
        return (
            f'<details class="event attachment" id="line-{line_no}">'
            f"<summary>{html_escape(summary)} {timestamp} #{line_no}</summary>"
            f"<pre>{render_json(event)}</pre>"
            "</details>"
        )

    return (
        f'<details class="event other" id="line-{line_no}">'
        f"<summary>{html_escape(event_type or 'event')} {timestamp} #{line_no}</summary>"
        f"<pre>{render_json(event)}</pre>"
        "</details>"
    )


def read_text_limited(path: Path, max_bytes: int) -> tuple[str, bool]:
    data = path.read_bytes()
    truncated = len(data) > max_bytes
    if truncated:
        data = data[:max_bytes]
    return data.decode("utf-8", errors="replace"), truncated


def collect_related_files(info: SessionInfo) -> list[Path]:
    paths: list[Path] = []
    if info.sidecar_path.is_dir():
        paths.extend(path for path in sorted(info.sidecar_path.rglob("*")) if path.is_file())
    if info.file_history_path.is_dir():
        paths.extend(path for path in sorted(info.file_history_path.rglob("*")) if path.is_file())
    return paths


def related_export_name(info: SessionInfo, path: Path) -> Path:
    if info.sidecar_path.is_dir():
        try:
            return Path("related") / "session-sidecar" / path.relative_to(info.sidecar_path)
        except ValueError:
            pass
    if info.file_history_path.is_dir():
        try:
            return Path("related") / "file-history" / path.relative_to(info.file_history_path)
        except ValueError:
            pass
    return Path("related") / path.name


def render_related_files(
    info: SessionInfo,
    max_bytes: int,
    embed_related: bool,
    related_dir_name: str | None,
) -> str:
    related = collect_related_files(info)
    if not related:
        return "<p class=\"muted\">No related sidecar or file-history files found.</p>"
    parts: list[str] = []
    for path in related:
        try:
            label = str(path.relative_to(info.project_path.parent.parent))
        except ValueError:
            label = str(path)

        copied_rel = ""
        if related_dir_name:
            exported_rel = related_export_name(info, path)
            if exported_rel.parts and exported_rel.parts[0] == "related":
                exported_rel = Path(*exported_rel.parts[1:])
            copied_rel = f"{related_dir_name}/{exported_rel.as_posix()}"

        if not embed_related:
            if copied_rel:
                parts.append(
                    f'<li><a href="{html_escape(copied_rel)}">{html_escape(label)}</a></li>'
                )
            else:
                parts.append(f"<li>{html_escape(label)}</li>")
            continue

        try:
            text, truncated = read_text_limited(path, max_bytes)
        except OSError as exc:
            text = f"Could not read file: {exc}"
            truncated = False
        trunc_note = " (truncated)" if truncated else ""
        link = (
            f' <a href="{html_escape(copied_rel)}">sidecar</a>'
            if copied_rel
            else ""
        )
        parts.append(
            "<details class=\"related-file\">"
            f"<summary>{html_escape(label)}{trunc_note}{link}</summary>"
            f"{render_block_text(text)}"
            "</details>"
        )
    if not embed_related:
        return "<ul>\n" + "\n".join(parts) + "\n</ul>"
    return "\n".join(parts)


def render_raw_jsonl(info: SessionInfo) -> str:
    try:
        text = info.jsonl_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        text = f"Could not read raw JSONL: {exc}"
    return (
        '<details class="raw-jsonl">'
        "<summary>Raw session JSONL</summary>"
        f"{render_block_text(text)}"
        "</details>"
    )


def build_html(
    info: SessionInfo,
    include_raw_json: bool,
    max_appendix_bytes: int,
    verbose_events: bool,
    embed_related: bool,
    raw_json_name: str | None,
    manifest_name: str | None,
    related_dir_name: str | None,
) -> str:
    events = list(read_jsonl(info.jsonl_path))
    rendered_events = "\n".join(
        render_event(event, line_no)
        for line_no, event in events
        if should_render_event(event, verbose_events)
    )
    type_rows = "\n".join(
        f"<tr><td>{html_escape(key)}</td><td>{count}</td></tr>"
        for key, count in sorted(info.type_counts.items())
    )
    related_files = collect_related_files(info)
    related_list = "\n".join(f"<li>{html_escape(str(path))}</li>" for path in related_files)
    raw_jsonl = render_raw_jsonl(info) if include_raw_json else ""
    generated = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    output_links = []
    if raw_json_name:
        output_links.append(f'<li><a href="{html_escape(raw_json_name)}">Raw JSONL</a></li>')
    if manifest_name:
        output_links.append(f'<li><a href="{html_escape(manifest_name)}">Manifest JSON</a></li>')
    if related_dir_name and related_files:
        output_links.append(f'<li><a href="{html_escape(related_dir_name)}/">Related files folder</a></li>')
    output_links_html = "\n".join(output_links) or "<li>No sidecar files were written.</li>"

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html_escape(info.title)} - Claude Session Export</title>
<style>
:root {{
  color-scheme: light dark;
  --bg: #f7f7f4;
  --panel: #ffffff;
  --text: #1d1d1f;
  --muted: #676b73;
  --border: #d9d9d4;
  --user: #e8f3ff;
  --assistant: #ffffff;
  --system: #fff8df;
  --tool: #f2f4f7;
  --accent: #2459d6;
}}
@media (prefers-color-scheme: dark) {{
  :root {{
    --bg: #121314;
    --panel: #1b1d20;
    --text: #eeeeea;
    --muted: #a3a7ad;
    --border: #33363b;
    --user: #11283d;
    --assistant: #1b1d20;
    --system: #332b15;
    --tool: #24272c;
    --accent: #8db2ff;
  }}
}}
body {{
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}}
main {{
  max-width: 1120px;
  margin: 0 auto;
  padding: 28px 20px 60px;
}}
h1 {{
  font-size: 28px;
  line-height: 1.2;
  margin: 0 0 8px;
}}
h2 {{
  font-size: 20px;
  margin: 32px 0 12px;
}}
a {{ color: var(--accent); }}
.meta-grid {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 10px;
  margin: 18px 0;
}}
.meta-card, .message, details.event, details.related-file, details.raw-jsonl {{
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 8px;
}}
.meta-card {{
  padding: 12px 14px;
}}
.meta-card b {{
  display: block;
  color: var(--muted);
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: .04em;
}}
.message {{
  margin: 14px 0;
  padding: 0;
  overflow: hidden;
}}
.message.user {{ background: var(--user); }}
.message.assistant {{ background: var(--assistant); }}
.message.system {{ background: var(--system); }}
.message header {{
  display: flex;
  gap: 12px;
  align-items: center;
  padding: 9px 12px;
  border-bottom: 1px solid var(--border);
  color: var(--muted);
  font-size: 13px;
}}
.message header strong {{
  color: var(--text);
}}
.message header a {{
  margin-left: auto;
  text-decoration: none;
}}
.message .text {{
  padding: 12px 14px;
}}
.message footer {{
  padding: 0 14px 10px;
  color: var(--muted);
  font-size: 12px;
  overflow-wrap: anywhere;
}}
p {{
  margin: 0 0 10px;
}}
pre {{
  margin: 0;
  padding: 12px 14px;
  overflow: auto;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font: 12px/1.45 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}}
.code-wrap {{
  margin: 10px 0;
  border: 1px solid var(--border);
  border-radius: 6px;
  overflow: hidden;
  background: var(--tool);
}}
.code-lang {{
  padding: 6px 10px;
  border-bottom: 1px solid var(--border);
  color: var(--muted);
  font-size: 12px;
}}
.tool-block {{
  margin: 12px 14px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--tool);
  overflow: hidden;
}}
.tool-title {{
  padding: 8px 10px;
  border-bottom: 1px solid var(--border);
  font-weight: 600;
}}
.tool-title span {{
  color: var(--muted);
  font-weight: 400;
  margin-left: 8px;
}}
details {{
  margin: 10px 0;
}}
summary {{
  cursor: pointer;
  padding: 10px 12px;
  color: var(--muted);
}}
details[open] summary {{
  border-bottom: 1px solid var(--border);
}}
table {{
  border-collapse: collapse;
  width: 100%;
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 8px;
  overflow: hidden;
}}
td, th {{
  border-bottom: 1px solid var(--border);
  padding: 8px 10px;
  text-align: left;
}}
.muted {{
  color: var(--muted);
}}
ul {{
  padding-left: 22px;
}}
</style>
</head>
<body>
<main>
<h1>{html_escape(info.title)}</h1>
<p class="muted">Claude Code session export generated {html_escape(generated)}</p>

<section class="meta-grid">
  <div class="meta-card"><b>Session ID</b>{html_escape(info.session_id)}</div>
  <div class="meta-card"><b>Project</b>{html_escape(info.cwd or info.project_label)}</div>
  <div class="meta-card"><b>Git Branch</b>{html_escape(info.git_branch or "(unknown)")}</div>
  <div class="meta-card"><b>First Timestamp</b>{html_escape(info.first_timestamp)}</div>
  <div class="meta-card"><b>Last Timestamp</b>{html_escape(info.last_timestamp)}</div>
  <div class="meta-card"><b>JSONL Records</b>{info.line_count}</div>
  <div class="meta-card"><b>Last Prompt</b>{html_escape(info.last_prompt or "(unknown)")}</div>
</section>

<details>
  <summary>Source paths</summary>
  <p><b>Main JSONL:</b> {html_escape(str(info.jsonl_path))}</p>
  <p><b>Sidecar path:</b> {html_escape(str(info.sidecar_path))}</p>
  <p><b>File history path:</b> {html_escape(str(info.file_history_path))}</p>
</details>

<details open>
  <summary>Exported files</summary>
  <ul>{output_links_html}</ul>
</details>

<details>
  <summary>Original related file paths</summary>
  <ul>{related_list or '<li>No related files found.</li>'}</ul>
</details>

<h2>Record Types</h2>
<table>
  <thead><tr><th>Type</th><th>Count</th></tr></thead>
  <tbody>{type_rows}</tbody>
</table>

<h2>Transcript</h2>
<p class="muted">Low-value metadata records are hidden by default. Re-run with <code>--verbose-events</code> to show ai-title, mode, permission, and queue-operation records inline.</p>
{rendered_events}

<h2>Related Files</h2>
{render_related_files(info, max_appendix_bytes, embed_related, related_dir_name)}

<h2>Raw Source</h2>
{raw_jsonl or '<p class="muted">Raw JSONL is written to <code>session.jsonl</code> in this export folder by default. Use <code>--embed-raw-json</code> to duplicate it inside this HTML.</p>'}
</main>
</body>
</html>
"""


def manifest_for(
    info: SessionInfo,
    html_path: Path,
    raw_jsonl_path: Path | None,
    manifest_path: Path | None,
    related_dir: Path | None,
    copied_related: list[dict[str, str]],
) -> dict[str, Any]:
    return {
        "title": info.title,
        "sessionId": info.session_id,
        "project": info.cwd or info.project_label,
        "projectLabel": info.project_label,
        "gitBranch": info.git_branch,
        "firstTimestamp": info.first_timestamp,
        "lastTimestamp": info.last_timestamp,
        "lastPrompt": info.last_prompt,
        "lineCount": info.line_count,
        "typeCounts": info.type_counts,
        "source": {
            "jsonl": str(info.jsonl_path),
            "sidecarPath": str(info.sidecar_path),
            "fileHistoryPath": str(info.file_history_path),
        },
        "export": {
            "html": str(html_path),
            "jsonl": str(raw_jsonl_path) if raw_jsonl_path else None,
            "manifest": str(manifest_path) if manifest_path else None,
            "relatedDir": str(related_dir) if related_dir else None,
            "relatedFiles": copied_related,
        },
    }


def copy_related_files(info: SessionInfo, related_dir: Path) -> list[dict[str, str]]:
    copied: list[dict[str, str]] = []
    for source in collect_related_files(info):
        rel = related_export_name(info, source)
        if rel.parts and rel.parts[0] == "related":
            rel = Path(*rel.parts[1:])
        destination = related_dir / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        copied.append(
            {
                "source": str(source),
                "exported": str(destination),
                "size": str(destination.stat().st_size),
            }
        )
    return copied


def find_session_by_id(sessions: list[SessionInfo], session_id: str) -> SessionInfo:
    matches = [item for item in sessions if item.session_id.startswith(session_id)]
    if not matches:
        raise SystemExit(f"No session matched: {session_id}")
    if len(matches) > 1:
        raise SystemExit(f"Multiple sessions matched prefix {session_id}; use a longer id.")
    return matches[0]


def default_output_dir(info: SessionInfo) -> Path:
    safe_title = re.sub(r"[^A-Za-z0-9._-]+", "-", info.title.lower()).strip("-")
    if not safe_title:
        safe_title = "session"
    return Path.cwd() / f"claude-{safe_title}-{info.session_id[:8]}"


def resolve_output_dir(output_arg: str | None, info: SessionInfo) -> Path:
    if not output_arg:
        return default_output_dir(info)
    output_dir = Path(output_arg).expanduser()
    if output_dir.suffix.lower() in {".html", ".htm"}:
        output_dir = output_dir.with_suffix("")
    return output_dir


def main() -> int:
    args = parse_args()
    claude_dir = Path(args.claude_dir).expanduser()
    sessions = filter_sessions(scan_sessions(claude_dir), args.project, args.search)

    if args.list:
        print_sessions(sessions, limit=args.limit)
        return 0

    if args.session:
        info = find_session_by_id(sessions, args.session)
    else:
        info = choose_session(sessions, limit=args.limit)

    output_dir = resolve_output_dir(args.output, info)
    if output_dir.exists() and not output_dir.is_dir():
        raise SystemExit(f"Output path exists and is not a folder: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    html_path = output_dir / "index.html"

    raw_jsonl_path: Path | None = None
    manifest_path: Path | None = None
    related_dir: Path | None = None
    copied_related: list[dict[str, str]] = []

    if not args.no_sidecars:
        raw_jsonl_path = output_dir / "session.jsonl"
        manifest_path = output_dir / "manifest.json"
        related_dir = output_dir / "related"

        shutil.copy2(info.jsonl_path, raw_jsonl_path)
        if collect_related_files(info):
            related_dir.mkdir(parents=True, exist_ok=True)
            copied_related = copy_related_files(info, related_dir)

    html_text = build_html(
        info,
        include_raw_json=args.embed_raw_json and not args.no_raw_json,
        max_appendix_bytes=args.max_appendix_bytes,
        verbose_events=args.verbose_events,
        embed_related=args.embed_related,
        raw_json_name=raw_jsonl_path.name if raw_jsonl_path else None,
        manifest_name=manifest_path.name if manifest_path else None,
        related_dir_name=related_dir.name if related_dir else None,
    )
    html_path.write_text(html_text, encoding="utf-8")

    if manifest_path:
        manifest = manifest_for(
            info,
            html_path=html_path,
            raw_jsonl_path=raw_jsonl_path,
            manifest_path=manifest_path,
            related_dir=related_dir,
            copied_related=copied_related,
        )
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    size = html_path.stat().st_size
    print(f"Wrote {output_dir}/")
    print(f"Wrote {html_path} ({size:,} bytes)")
    if raw_jsonl_path:
        print(f"Wrote {raw_jsonl_path} ({raw_jsonl_path.stat().st_size:,} bytes)")
    if manifest_path:
        print(f"Wrote {manifest_path} ({manifest_path.stat().st_size:,} bytes)")
    if related_dir and copied_related:
        print(f"Wrote {related_dir} ({len(copied_related)} related files)")

    if shutil.which("open") and os.environ.get("CLAUDE_EXPORT_OPEN") == "1":
        os.system(f"open {json.dumps(str(html_path))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
