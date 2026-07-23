"""Two-pane dialogue renderer.

Left: the conversation, tightly focused on user/assistant text.
Right: a swappable side panel. Starts on 'Session details'; clicking any
button inside a turn (tool call, tool result, thinking, system event)
swaps the panel to that item's full details.

The output HTML is self-contained (inline CSS + JS), so the same renderer
powers the live web viewer and the exported index.html in the zip.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from html import escape
from typing import Any

import claude_history_html_export as core


HIDDEN_META_TYPES = {
    "ai-title", "last-prompt", "mode", "permission-mode",
    "queue-operation", "attachment", "file-history-snapshot",
    "summary", "json-decode-error",
}


@dataclass
class PayloadRegistry:
    items: list[tuple[str, str, str, str]] = field(default_factory=list)
    _n: int = 0

    def add(self, kind: str, title: str, body_html: str) -> str:
        self._n += 1
        pid = f"d{self._n}"
        self.items.append((pid, kind, title, body_html))
        return pid


def _fmt_time(raw: str) -> str:
    if not raw:
        return ""
    try:
        return dt.datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return raw


def _one_line(text: str, limit: int = 90) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _tool_label(block: dict[str, Any]) -> tuple[str, str]:
    name = str(block.get("name") or "tool")
    inp = block.get("input") if isinstance(block.get("input"), dict) else {}
    hint = ""
    for key in ("command", "file_path", "path", "description", "query", "prompt", "pattern", "url"):
        val = inp.get(key)
        if isinstance(val, str) and val.strip():
            hint = _one_line(val, 80)
            break
    return name, hint


def _btn(kind: str, target: str, label: str, hint: str = "") -> str:
    hint_html = f'<span class="btn-hint">{escape(hint)}</span>' if hint else ""
    return (f'<button type="button" class="side-btn {kind}" data-target="{target}">'
            f'<span class="btn-label">{escape(label)}</span>{hint_html}</button>')


def _render_tool_use(block: dict[str, Any], reg: PayloadRegistry) -> str:
    name, hint = _tool_label(block)
    inp = block.get("input", {})
    title = f"Tool call · {name}"
    body_parts = [f'<div class="kv"><b>id</b><code>{escape(str(block.get("id", "")))}</code></div>']
    if isinstance(inp, dict):
        cmd = inp.get("command")
        if isinstance(cmd, str):
            body_parts.append('<h4>command</h4>' + f'<pre>{escape(cmd)}</pre>')
            remainder = {k: v for k, v in inp.items() if k != "command"}
            if remainder:
                body_parts.append('<h4>other input</h4>'
                                  f'<pre>{escape(json.dumps(remainder, ensure_ascii=False, indent=2))}</pre>')
        else:
            body_parts.append('<h4>input</h4>'
                              f'<pre>{escape(json.dumps(inp, ensure_ascii=False, indent=2))}</pre>')
    else:
        body_parts.append(f'<pre>{escape(json.dumps(inp, ensure_ascii=False, indent=2))}</pre>')
    pid = reg.add("tool", title, "".join(body_parts))
    return _btn("tool", pid, f"⚙ {name}", hint)


def _render_tool_result(block: dict[str, Any], tool_result: Any, reg: PayloadRegistry) -> str:
    content = block.get("content")
    text = core.content_text(content).strip()
    hint = _one_line(text.splitlines()[0], 80) if text else "(empty)"
    title = "Tool result"
    body_parts: list[str] = []
    if text:
        body_parts.append(f'<h4>content</h4><pre>{escape(text)}</pre>')
    if isinstance(tool_result, dict):
        for key in ("stdout", "stderr"):
            val = tool_result.get(key)
            if val:
                body_parts.append(f'<h4>{key}</h4><pre>{escape(str(val))}</pre>')
        file_val = tool_result.get("file")
        if file_val:
            body_parts.append('<h4>file</h4>'
                              f'<pre>{escape(json.dumps(file_val, ensure_ascii=False, indent=2))}</pre>')
    if not body_parts:
        body_parts.append('<p class="muted">(no result content)</p>')
    pid = reg.add("result", title, "".join(body_parts))
    return _btn("result", pid, "↳ result", hint)


def _render_thinking(block: dict[str, Any], reg: PayloadRegistry) -> str:
    text = core.content_text(block.get("thinking") or block.get("text") or "").strip()
    if not text:
        return ""
    pid = reg.add("thinking", "Thinking", f'<pre>{escape(text)}</pre>')
    return _btn("thinking", pid, "✻ thinking", _one_line(text, 80))


def _render_system(event: dict[str, Any], reg: PayloadRegistry) -> str:
    text = core.content_text(event.get("message", {}).get("content")).strip()
    if not text:
        return ""
    pid = reg.add("system", "System message", f'<pre>{escape(text)}</pre>')
    return _btn("system", pid, "ⓘ system", _one_line(text, 80))


def _render_message_body(event: dict[str, Any], reg: PayloadRegistry) -> str:
    message = event.get("message", {})
    content = message.get("content")
    tool_result = event.get("toolUseResult")

    if isinstance(content, str):
        return f'<div class="text">{core.render_markdownish(content)}</div>'

    if not isinstance(content, list):
        if content is None and tool_result is not None:
            return _render_tool_result({}, tool_result, reg)
        return f'<pre>{escape(json.dumps(content, ensure_ascii=False, indent=2))}</pre>'

    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            if block.strip():
                parts.append(f'<div class="text">{core.render_markdownish(block)}</div>')
            continue
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            text = str(block.get("text", ""))
            if text.strip():
                parts.append(f'<div class="text">{core.render_markdownish(text)}</div>')
        elif btype == "thinking":
            parts.append(_render_thinking(block, reg))
        elif btype == "tool_use":
            parts.append(_render_tool_use(block, reg))
        elif btype == "tool_result":
            parts.append(_render_tool_result(block, tool_result, reg))
        else:
            pid = reg.add("other", str(btype or "block"),
                          f'<pre>{escape(json.dumps(block, ensure_ascii=False, indent=2))}</pre>')
            parts.append(_btn("other", pid, f"· {btype or 'block'}"))
    return "\n".join(p for p in parts if p)


def _render_turn(event: dict[str, Any], line_no: int, reg: PayloadRegistry) -> str:
    etype = event.get("type", "")
    ts = _fmt_time(str(event.get("timestamp", "")))
    if etype == "user":
        body = _render_message_body(event, reg)
        origin = ""
        if isinstance(event.get("origin"), dict) and event["origin"].get("kind"):
            origin = f" · {event['origin']['kind']}"
        return (f'<article class="turn user" id="line-{line_no}">'
                f'<div class="role">You{escape(origin)}<span class="stamp">{escape(ts)}</span></div>'
                f'<div class="body">{body}</div></article>')
    if etype == "assistant":
        body = _render_message_body(event, reg)
        return (f'<article class="turn assistant" id="line-{line_no}">'
                f'<div class="role">Claude<span class="stamp">{escape(ts)}</span></div>'
                f'<div class="body">{body}</div></article>')
    if etype == "system":
        btn = _render_system(event, reg)
        if not btn:
            return ""
        return (f'<article class="turn meta" id="line-{line_no}">'
                f'<div class="role">System<span class="stamp">{escape(ts)}</span></div>'
                f'<div class="body">{btn}</div></article>')
    return ""


def _home_panel_html(info: core.SessionInfo, extra_links: list[tuple[str, str]]) -> str:
    rows = "".join(
        f'<div class="kv"><b>{escape(label)}</b><span>{escape(str(value) or "—")}</span></div>'
        for label, value in [
            ("Title", info.title),
            ("Project", info.cwd or info.project_label),
            ("Git branch", info.git_branch),
            ("Session ID", info.session_id),
            ("Started", _fmt_time(info.first_timestamp)),
            ("Last activity", _fmt_time(info.last_timestamp)),
            ("Records", info.line_count),
        ]
    )
    type_rows = "".join(
        f"<tr><td>{escape(k)}</td><td>{v}</td></tr>"
        for k, v in sorted(info.type_counts.items())
    )
    related = core.collect_related_files(info)
    related_list = "".join(f"<li><code>{escape(str(p))}</code></li>" for p in related) or '<li class="muted">None</li>'
    links_html = ""
    if extra_links:
        links_html = ('<h4>Files</h4><ul class="linklist">'
                      + "".join(f'<li><a href="{escape(href)}">{escape(label)}</a></li>'
                                for label, href in extra_links)
                      + "</ul>")
    return (f'<div class="kv-grid">{rows}</div>'
            f'{links_html}'
            f'<h4>Source path</h4><p class="mono">{escape(str(info.jsonl_path))}</p>'
            f'<h4>Record types</h4><table class="counts"><tbody>{type_rows}</tbody></table>'
            f'<h4>Related files ({len(related)})</h4><ul class="mono small">{related_list}</ul>')


def render_page(
    info: core.SessionInfo,
    back_url: str | None = "/",
    zip_url: str | None = None,
    extra_links: list[tuple[str, str]] | None = None,
) -> str:
    reg = PayloadRegistry()
    turns: list[str] = []
    hidden_meta = 0
    for line_no, event in core.read_jsonl(info.jsonl_path):
        etype = str(event.get("type", ""))
        if etype in HIDDEN_META_TYPES:
            hidden_meta += 1
            continue
        if etype in {"user", "assistant"} and not core.has_visible_message_content(event):
            continue
        rendered = _render_turn(event, line_no, reg)
        if rendered:
            turns.append(rendered)

    hidden_note = (f'<p class="muted small hidden-note">{hidden_meta} metadata events hidden.</p>'
                   if hidden_meta else "")
    home_html = _home_panel_html(info, extra_links or [])
    payload_nodes = "".join(
        f'<template data-id="{pid}" data-kind="{escape(kind)}" data-title="{escape(title)}">{body}</template>'
        for pid, kind, title, body in reg.items
    )

    back_btn = f'<a class="topbtn" href="{escape(back_url)}">← Sessions</a>' if back_url else ""
    zip_btn = f'<a class="topbtn primary" href="{escape(zip_url)}">Download zip</a>' if zip_url else ""

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(info.title)} — Claude session</title>
<style>
:root {{ color-scheme: light dark;
  --bg:#f7f7f4; --panel:#ffffff; --panel2:#f0efe9; --text:#1d1d1f;
  --muted:#676b73; --border:#d9d9d4; --accent:#2459d6;
  --user-bg:#e8f3ff; --user-border:#c9dcf5; --btn-hover:#eef1f5;
  --tool:#4a6cf7; --result:#2f9c66; --thinking:#8b5cf6; --system:#c47a1a; }}
@media (prefers-color-scheme: dark) {{ :root {{
  --bg:#0f1012; --panel:#191b1e; --panel2:#141518; --text:#eeeeea;
  --muted:#9ea3ab; --border:#2c2f34; --accent:#8db2ff;
  --user-bg:#132638; --user-border:#1e3a58; --btn-hover:#222629;
  --tool:#7f9dff; --result:#5cc78f; --thinking:#b18cff; --system:#e6a558; }} }}
* {{ box-sizing:border-box; }}
html, body {{ height:100%; }}
body {{ margin:0; background:var(--bg); color:var(--text);
  font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
.topbar {{ display:flex; align-items:center; gap:14px;
  padding:10px 20px; background:var(--panel); border-bottom:1px solid var(--border);
  position:sticky; top:0; z-index:30; }}
.topbar h1 {{ font-size:14px; font-weight:600; margin:0; flex:1; min-width:0;
  overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
.topbtn {{ padding:6px 12px; border:1px solid var(--border); border-radius:6px;
  background:var(--bg); color:var(--accent); text-decoration:none; font-size:13px; }}
.topbtn.primary {{ background:var(--accent); color:#fff; border-color:var(--accent); }}
#toggle-panel {{ display:none; }}
.layout {{ display:grid; grid-template-columns: minmax(0, 1fr) 420px;
  gap:0; height:calc(100vh - 49px); }}
.left {{ overflow-y:auto; padding:24px 28px 60px; }}
.right {{ border-left:1px solid var(--border); background:var(--panel);
  overflow-y:auto; }}
.right-header {{ position:sticky; top:0; background:var(--panel);
  border-bottom:1px solid var(--border); padding:12px 18px;
  display:flex; align-items:center; gap:10px; z-index:5; }}
.right-header h2 {{ font-size:14px; font-weight:600; margin:0; flex:1;
  overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
.right-header button {{ border:1px solid var(--border); background:var(--bg);
  color:var(--muted); border-radius:6px; padding:4px 10px; cursor:pointer;
  font:inherit; font-size:12px; }}
.right-header button:hover {{ border-color:var(--accent); color:var(--accent); }}
#right-body {{ padding:16px 18px 40px; }}

.turn {{ margin:0 0 18px; padding:14px 16px; border-radius:10px;
  border:1px solid var(--border); background:var(--panel); max-width:820px; }}
.turn.user {{ background:var(--user-bg); border-color:var(--user-border); }}
.turn.meta {{ background:transparent; border-style:dashed; }}
.turn .role {{ font-size:11px; font-weight:600; letter-spacing:.05em;
  text-transform:uppercase; color:var(--muted); margin-bottom:6px;
  display:flex; gap:8px; align-items:baseline; }}
.turn.user .role {{ color:var(--accent); }}
.turn .role .stamp {{ margin-left:auto; font-weight:400; letter-spacing:0;
  text-transform:none; font-variant-numeric:tabular-nums; }}
.turn .body {{ display:flex; flex-direction:column; gap:8px; }}
.turn .text p {{ margin:0 0 10px; }}
.turn .text p:last-child {{ margin:0; }}
.turn pre {{ margin:0; padding:10px 12px; border-radius:6px;
  background:var(--panel2); overflow:auto; white-space:pre-wrap;
  overflow-wrap:anywhere;
  font:12px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; }}
.turn .code-wrap {{ margin:10px 0; border:1px solid var(--border);
  border-radius:6px; overflow:hidden; background:var(--panel2); }}
.turn .code-lang {{ padding:5px 10px; border-bottom:1px solid var(--border);
  font-size:11px; color:var(--muted); text-transform:lowercase; }}

.side-btn {{ display:inline-flex; gap:8px; align-items:baseline;
  max-width:100%; text-align:left; padding:6px 10px; border-radius:6px;
  border:1px solid var(--border); background:var(--panel2);
  color:var(--text); font:inherit; font-size:13px; cursor:pointer;
  overflow:hidden; align-self:flex-start; }}
.side-btn:hover {{ background:var(--btn-hover); border-color:var(--accent); }}
.side-btn.active {{ border-color:var(--accent); background:var(--user-bg); }}
.side-btn .btn-label {{ white-space:nowrap; font-weight:500; }}
.side-btn .btn-hint {{ color:var(--muted); font-size:12px;
  overflow:hidden; text-overflow:ellipsis; white-space:nowrap; min-width:0; }}
.side-btn.tool .btn-label {{ color:var(--tool); }}
.side-btn.result .btn-label {{ color:var(--result); }}
.side-btn.thinking .btn-label {{ color:var(--thinking); }}
.side-btn.system .btn-label {{ color:var(--system); }}

#right-body h4 {{ font-size:11px; text-transform:uppercase; letter-spacing:.05em;
  color:var(--muted); margin:16px 0 6px; font-weight:600; }}
#right-body h4:first-child {{ margin-top:0; }}
#right-body pre {{ margin:0; padding:10px 12px; border-radius:6px;
  background:var(--panel2); border:1px solid var(--border); overflow:auto;
  white-space:pre-wrap; overflow-wrap:anywhere;
  font:12px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; }}
#right-body p {{ margin:0 0 10px; }}
#right-body p.mono, #right-body ul.mono {{
  font:12px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  overflow-wrap:anywhere; }}
#right-body ul {{ padding-left:18px; margin:0; }}
#right-body ul.small {{ font-size:12px; }}
#right-body .kv-grid {{ display:grid; grid-template-columns:1fr; gap:8px; }}
#right-body .kv {{ display:flex; flex-direction:column; gap:1px; }}
#right-body .kv b {{ font-size:11px; text-transform:uppercase; letter-spacing:.05em;
  color:var(--muted); font-weight:600; }}
#right-body .kv span, #right-body .kv code {{ overflow-wrap:anywhere; }}
#right-body table.counts {{ width:100%; border-collapse:collapse; font-size:13px; }}
#right-body table.counts td {{ padding:3px 6px; border-bottom:1px solid var(--border); }}
#right-body ul.linklist {{ list-style:none; padding:0; }}
#right-body ul.linklist a {{ color:var(--accent); }}

.muted {{ color:var(--muted); }}
.small {{ font-size:12px; }}
.hidden-note {{ margin-top:24px; }}

@media (max-width: 900px) {{
  #toggle-panel {{ display:inline-block; }}
  .layout {{ grid-template-columns: 1fr; height:auto; }}
  .left {{ height:auto; padding:16px; }}
  .right {{ position:fixed; inset:49px 0 0 0; border-left:none;
    transform:translateX(100%); transition:transform .2s; z-index:25; }}
  .right.open {{ transform:translateX(0); }}
}}
</style>
</head>
<body>
<div class="topbar">
  {back_btn}
  <h1>{escape(info.title)}</h1>
  <button id="toggle-panel" class="topbtn" type="button">Info</button>
  {zip_btn}
</div>
<div class="layout">
  <main class="left">
    {"".join(turns)}
    {hidden_note}
  </main>
  <aside class="right" id="right">
    <div class="right-header">
      <h2 id="right-title">Session details</h2>
      <button id="right-home" type="button">Reset</button>
    </div>
    <div id="right-body"></div>
  </aside>
</div>
<template id="home-payload" data-title="Session details">{home_html}</template>
{payload_nodes}
<script>
(function() {{
  const body = document.getElementById('right-body');
  const title = document.getElementById('right-title');
  const panel = document.getElementById('right');
  const homeTpl = document.getElementById('home-payload');
  function loadTemplate(tpl) {{
    title.textContent = tpl.dataset.title || '';
    body.innerHTML = '';
    body.appendChild(tpl.content.cloneNode(true));
    body.scrollTop = 0;
    panel.scrollTop = 0;
  }}
  function showHome() {{
    loadTemplate(homeTpl);
    document.querySelectorAll('.side-btn.active').forEach(b => b.classList.remove('active'));
  }}
  showHome();
  document.querySelectorAll('.side-btn').forEach(btn => {{
    btn.addEventListener('click', () => {{
      const tpl = document.querySelector('template[data-id="' + btn.dataset.target + '"]');
      if (!tpl) return;
      loadTemplate(tpl);
      document.querySelectorAll('.side-btn.active').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      panel.classList.add('open');
    }});
  }});
  document.getElementById('right-home').addEventListener('click', () => {{
    showHome();
    panel.classList.remove('open');
  }});
  const toggle = document.getElementById('toggle-panel');
  if (toggle) toggle.addEventListener('click', () => panel.classList.toggle('open'));
}})();
</script>
</body>
</html>
"""
