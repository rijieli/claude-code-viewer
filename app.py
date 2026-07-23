"""Minimalist web viewer for Claude Code session history.

Run:
    uv sync
    uv run app.py

Then open http://127.0.0.1:8765
"""

from __future__ import annotations

import io
import json
import shutil
import tempfile
import zipfile
from html import escape
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, Response
import uvicorn

import core
import web_render

CLAUDE_DIR = Path.home() / ".claude"

app = FastAPI()


def load_sessions() -> list[core.SessionInfo]:
    return core.scan_sessions(CLAUDE_DIR)


def find_session(session_id: str) -> core.SessionInfo:
    for item in load_sessions():
        if item.session_id == session_id:
            return item
    raise HTTPException(status_code=404, detail=f"Session {session_id} not found")


def session_matches(item: core.SessionInfo, needle: str) -> bool:
    haystack = " ".join([
        item.session_id, item.project_label, item.title,
        item.first_prompt, item.last_prompt, item.cwd, item.git_branch,
    ]).lower()
    if needle in haystack:
        return True
    try:
        with item.jsonl_path.open("r", encoding="utf-8", errors="replace") as fh:
            for chunk in iter(lambda: fh.read(65536), ""):
                if needle in chunk.lower():
                    return True
    except OSError:
        return False
    return False


PAGE_SIZE = 20


def project_key(item: core.SessionInfo) -> str:
    return item.project_label


def project_display(item: core.SessionInfo) -> str:
    if item.cwd:
        return Path(item.cwd).name or item.cwd
    return item.project_label


def build_project_tabs(sessions: list[core.SessionInfo]) -> list[dict]:
    groups: dict[str, dict] = {}
    for s in sessions:
        key = project_key(s)
        g = groups.setdefault(key, {"key": key, "label": project_display(s), "count": 0, "mtime": 0.0})
        g["count"] += 1
        if s.mtime > g["mtime"]:
            g["mtime"] = s.mtime
            g["label"] = project_display(s)
    return sorted(groups.values(), key=lambda g: g["mtime"], reverse=True)


@app.get("/", response_class=HTMLResponse)
def index(q: str = "", project: str = "", page: int = 1) -> str:
    all_sessions = load_sessions()
    tabs = build_project_tabs(all_sessions)
    sessions = all_sessions
    if project:
        sessions = [s for s in sessions if project_key(s) == project]
    needle = q.strip().lower()
    if needle:
        sessions = [s for s in sessions if session_matches(s, needle)]
    total = len(sessions)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(1, min(page, total_pages))
    start = (page - 1) * PAGE_SIZE
    visible = sessions[start:start + PAGE_SIZE]
    rows = []
    for item in visible:
        project = escape(item.cwd or item.project_label)
        title = escape(item.title)
        prompt = escape(item.first_prompt or "")
        when = escape(core.local_time(item.mtime))
        sid = escape(item.session_id)
        short = escape(item.session_id[:8])
        rows.append(f"""
        <tr>
          <td class="when">{when}</td>
          <td class="short"><code>{short}</code></td>
          <td class="lines">{item.line_count}</td>
          <td>
            <div class="title"><a href="/session/{sid}">{title}</a></div>
            <div class="project">{project}</div>
            <div class="prompt">{prompt}</div>
          </td>
          <td class="actions">
            <a class="btn" href="/session/{sid}/export.zip">Download zip</a>
          </td>
        </tr>
        """)
    q_val = escape(q)
    noun = "match" + ("es" if total != 1 else "") if needle else "session" + ("s" if total != 1 else "")
    range_note = f"{start + 1}–{start + len(visible)} of {total} {noun}" if total else f"0 {noun}"

    from urllib.parse import urlencode

    def page_link(p: int, label: str, disabled: bool = False, current: bool = False) -> str:
        params = {"page": p}
        if q:
            params["q"] = q
        if project:
            params["project"] = project
        qs = "?" + urlencode(params)
        cls = "page-btn" + (" current" if current else "") + (" disabled" if disabled else "")
        if disabled:
            return f'<span class="{cls}">{label}</span>'
        return f'<a class="{cls}" href="{qs}">{label}</a>'

    def tab_href(pkey: str) -> str:
        params = {}
        if pkey:
            params["project"] = pkey
        if q:
            params["q"] = q
        return "/" + ("?" + urlencode(params) if params else "")

    sidebar_parts = [
        f'<a class="proj{" active" if not project else ""}" href="{escape(tab_href(""))}">'
        f'<span class="proj-label">All sessions</span>'
        f'<span class="proj-count">{len(all_sessions)}</span></a>'
    ]
    for tab in tabs:
        active = " active" if tab["key"] == project else ""
        sidebar_parts.append(
            f'<a class="proj{active}" href="{escape(tab_href(tab["key"]))}" '
            f'title="{escape(tab["key"])}">'
            f'<span class="proj-label">{escape(tab["label"])}</span>'
            f'<span class="proj-count">{tab["count"]}</span></a>'
        )
    sidebar_html = (
        f'<aside class="sidebar">'
        f'<div class="sidebar-title">Projects</div>'
        f'<nav class="proj-list">{"".join(sidebar_parts)}</nav>'
        f'</aside>'
    )

    pager_parts = [page_link(page - 1, "‹ Prev", disabled=page <= 1)]
    window = range(max(1, page - 2), min(total_pages, page + 2) + 1)
    if window and window[0] > 1:
        pager_parts.append(page_link(1, "1"))
        if window[0] > 2:
            pager_parts.append('<span class="page-gap">…</span>')
    for p in window:
        pager_parts.append(page_link(p, str(p), current=(p == page)))
    if window and window[-1] < total_pages:
        if window[-1] < total_pages - 1:
            pager_parts.append('<span class="page-gap">…</span>')
        pager_parts.append(page_link(total_pages, str(total_pages)))
    pager_parts.append(page_link(page + 1, "Next ›", disabled=page >= total_pages))
    pager_html = f'<nav class="pager">{"".join(pager_parts)}</nav>' if total > PAGE_SIZE else ""

    active_label = next((t["label"] for t in tabs if t["key"] == project), "") if project else "All sessions"
    return PAGE.format(
        body=f"""
        <div class="layout">
          {sidebar_html}
          <section class="content">
            <h1>{escape(active_label)}</h1>
            <form method="get" action="/" class="search">
              {f'<input type="hidden" name="project" value="{escape(project)}">' if project else ''}
              <input type="search" name="q" value="{q_val}" placeholder="Search title, prompt, path, or full transcript text..." autofocus>
              <button type="submit">Search</button>
              {f'<a class="btn" href="{escape(tab_href(project))}">Clear</a>' if needle else ''}
            </form>
            <p class="muted">{range_note} in {escape(str(CLAUDE_DIR))}</p>
            <table>
              <thead><tr><th>Modified</th><th>ID</th><th>Lines</th><th>Title / project / first prompt</th><th></th></tr></thead>
              <tbody>{''.join(rows)}</tbody>
            </table>
            {pager_html}
          </section>
        </div>
        """
    )


@app.get("/session/{session_id}", response_class=HTMLResponse)
def view_session(session_id: str, raw: int = 0) -> str:
    info = find_session(session_id)
    if raw:
        return core.build_html(
            info,
            include_raw_json=True,
            max_appendix_bytes=2_000_000,
            verbose_events=True,
            embed_related=False,
            raw_json_name=None,
            manifest_name=None,
            related_dir_name=None,
        )
    return web_render.render_page(
        info,
        back_url="/",
        zip_url=f"/session/{session_id}/export.zip",
    )


@app.get("/session/{session_id}/export.zip")
def export_zip(session_id: str) -> Response:
    info = find_session(session_id)
    safe_title = "".join(c if c.isalnum() or c in "._-" else "-" for c in info.title.lower()).strip("-") or "session"
    folder_name = f"claude-{safe_title}-{info.session_id[:8]}"

    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / folder_name
        out.mkdir()

        raw_jsonl = out / "session.jsonl"
        manifest = out / "manifest.json"
        related_dir = out / "related"

        shutil.copy2(info.jsonl_path, raw_jsonl)
        copied: list[dict[str, str]] = []
        if core.collect_related_files(info):
            related_dir.mkdir()
            copied = core.copy_related_files(info, related_dir)

        extra_links = [
            ("session.jsonl (raw)", raw_jsonl.name),
            ("manifest.json", manifest.name),
        ]
        if copied:
            extra_links.append(("related/", related_dir.name + "/"))
        html_text = web_render.render_page(
            info,
            back_url=None,
            zip_url=None,
            extra_links=extra_links,
        )
        (out / "index.html").write_text(html_text, encoding="utf-8")

        manifest_data = core.manifest_for(
            info,
            html_path=out / "index.html",
            raw_jsonl_path=raw_jsonl,
            manifest_path=manifest,
            related_dir=related_dir if copied else None,
            copied_related=copied,
        )
        manifest.write_text(json.dumps(manifest_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(out.rglob("*")):
                if path.is_file():
                    zf.write(path, path.relative_to(out.parent))
        buf.seek(0)

    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{folder_name}.zip"'},
    )


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Claude Code sessions</title>
<style>
:root {{ color-scheme: light dark;
  --bg:#f7f7f4; --panel:#fff; --border:#d9d9d4; --muted:#676b73; --accent:#2459d6; }}
@media (prefers-color-scheme: dark) {{ :root {{
  --bg:#121314; --panel:#1b1d20; --border:#33363b; --muted:#a3a7ad; --accent:#8db2ff; }} }}
body {{ margin:0; background:var(--bg); color:inherit;
  font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
main {{ max-width:1400px; margin:0 auto; padding:24px 24px 60px; }}
h1 {{ margin:0 0 8px; }}
.muted {{ color:var(--muted); margin:0 0 18px; }}
.layout {{ display:grid; grid-template-columns:260px minmax(0,1fr); gap:24px;
  align-items:start; }}
.sidebar {{ position:sticky; top:20px; max-height:calc(100vh - 40px);
  overflow-y:auto; padding:14px; background:var(--panel);
  border:1px solid var(--border); border-radius:10px; scrollbar-width:thin; }}
.sidebar-title {{ font-size:11px; font-weight:600; letter-spacing:.06em;
  text-transform:uppercase; color:var(--muted); padding:2px 6px 10px; }}
.proj-list {{ display:flex; flex-direction:column; }}
.proj {{ display:flex; align-items:center; gap:8px; padding:6px 8px;
  border-radius:6px; color:inherit; text-decoration:none; font-size:13px;
  border-left:2px solid transparent; }}
.proj:hover {{ background:var(--bg); }}
.proj.active {{ background:var(--bg); border-left-color:var(--accent);
  color:var(--accent); font-weight:600; }}
.proj-label {{ flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis;
  white-space:nowrap; }}
.proj-count {{ color:var(--muted); font-size:12px; font-variant-numeric:tabular-nums; }}
.proj.active .proj-count {{ color:var(--accent); }}
.content {{ min-width:0; }}
.content h1 {{ margin-top:0; }}
.search {{ display:flex; gap:8px; margin:12px 0 16px; }}
@media (max-width: 820px) {{
  .layout {{ grid-template-columns:1fr; }}
  .sidebar {{ position:static; max-height:220px; }}
}}
.search input {{ flex:1; padding:8px 12px; border:1px solid var(--border);
  border-radius:6px; background:var(--panel); color:inherit; font:inherit; }}
.search button {{ padding:8px 14px; border:1px solid var(--border);
  border-radius:6px; background:var(--accent); color:#fff; cursor:pointer; font:inherit; }}
.pager {{ display:flex; gap:6px; justify-content:center; margin:20px 0 0;
  flex-wrap:wrap; }}
.page-btn {{ padding:6px 12px; border:1px solid var(--border); border-radius:6px;
  background:var(--panel); color:var(--accent); text-decoration:none;
  font-size:13px; min-width:36px; text-align:center; }}
.page-btn:hover {{ border-color:var(--accent); }}
.page-btn.current {{ background:var(--accent); color:#fff; border-color:var(--accent); }}
.page-btn.disabled {{ color:var(--muted); background:transparent; cursor:default; }}
.page-gap {{ padding:6px 4px; color:var(--muted); }}
table {{ width:100%; border-collapse:collapse; background:var(--panel);
  border:1px solid var(--border); border-radius:10px; overflow:hidden; }}
th, td {{ padding:10px 12px; border-bottom:1px solid var(--border); vertical-align:top; text-align:left; }}
tr:last-child td {{ border-bottom:none; }}
th {{ color:var(--muted); font-weight:600; font-size:12px; text-transform:uppercase; letter-spacing:.04em; }}
.when {{ white-space:nowrap; color:var(--muted); }}
.short code {{ font:12px ui-monospace,SFMono-Regular,Menlo,monospace; }}
.lines {{ text-align:right; color:var(--muted); }}
.title a {{ color:var(--accent); text-decoration:none; font-weight:600; }}
.title a:hover {{ text-decoration:underline; }}
.project {{ color:var(--muted); font-size:12px; overflow-wrap:anywhere; }}
.prompt {{ margin-top:4px; color:var(--muted); font-size:13px;
  display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; }}
.actions {{ white-space:nowrap; }}
.btn {{ display:inline-block; padding:6px 10px; border:1px solid var(--border);
  border-radius:6px; text-decoration:none; color:var(--accent); background:var(--bg); }}
.btn:hover {{ border-color:var(--accent); }}
</style>
</head>
<body>
<main>
{body}
</main>
</body>
</html>
"""


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8765)
