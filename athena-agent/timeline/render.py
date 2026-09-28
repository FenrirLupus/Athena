"""TIMELINE RENDER — the two-file bundle: index.json + timeline.html.

index.json    — the condensed database: the graph + per-module
                breakdown + the agent's TOC. Parsed natively by agents
                (reasoning); followed by filename to other timelines.
timeline.html — the SVG visual: the top-down spine, branches splitting
                downward, DEAD END labels (✗), cross-module refs as
                clickable links, search + zoom. Read by vision OR
                reasoning (the DOM is structured).
"""

from __future__ import annotations

import html as _html
import json
from pathlib import Path
from typing import Dict, List

from . import ALIVE, SICK, DEAD, CONNECTION


def write_index(graph: dict, dest: Path, *, title: str = "") -> None:
    """Write the condensed index.json (the agent's TOC)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "title": title or graph.get("graph", {}).get("name", ""),
        "kind": graph.get("graph", {}).get("kind", ""),
        "summary": graph.get("summary", {}),
        "entry_points": graph.get("entry_points", []),
        "nodes": graph.get("nodes", []),
        "links": graph.get("links", []),
    }
    dest.write_text(json.dumps(payload, indent=2), encoding="utf-8")




def _wire_color(target_state: str, src_state: str = "") -> str:
    """The wire color: the path-health gradient (the Operator's 08-14
    spec). A wire flowing INTO alive code is green; into a sick/caution
    is yellow; into dead/error is red. When the SOURCE is already sick
    and the target is dead, the wire is the blend (orange) — the
    divergence where caution becomes error."""
    if target_state == DEAD:
        if src_state == SICK:
            return "#fb923c"  # the sick→dead blend (orange)
        return "#f87171"
    if target_state == SICK:
        return "#fbbf24"
    return "#34d399"


def _esc(s) -> str:
    return _html.escape(str(s), quote=True)




def _wrap_label(label: str, max_chars: int = 18) -> list[str]:
    """Wrap a label into lines (the Operator's 08-14 spec: a node's box
    wraps based on its label text). Splits on spaces/dots first, then
    hard-cuts long tokens."""
    label = str(label)
    if len(label) <= max_chars:
        return [label]
    lines = []
    cur = ""
    for token in label.replace(".", " . ").split():
        if len(cur) + len(token) + 1 > max_chars and cur:
            lines.append(cur.strip())
            cur = token
        else:
            cur = f"{cur} {token}".strip()
    if cur:
        lines.append(cur.strip())
    # Hard-cut any single token still too long.
    out = []
    for ln in lines[:3]:
        while len(ln) > max_chars:
            out.append(ln[:max_chars])
            ln = ln[max_chars:]
        if ln:
            out.append(ln)
    return out[:4] or [label[:max_chars]]


def _box_size(label: str) -> tuple:
    """The node box size from its label: width scales with the text
    (min 96px readable — the Operator's spec: element size minimum 1x,
    never sub-native), height = lines."""
    lines = _wrap_label(label)
    width = max(96, min(220, max(len(l) for l in lines) * 7.2 + 20))
    height = 18 + len(lines) * 14
    return width, height


def write_html(graph: dict, dest: Path, *, title: str = "",
               graph_list: list | None = None) -> None:
    """Write the self-contained timeline.html (SVG, top-down)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    nodes = graph.get("nodes", [])
    links = graph.get("links", [])
    title = title or graph.get("graph", {}).get("name", "")
    gl = json.dumps(graph_list or []).replace("</", "<\\/")

    # Group nodes by module for the legend + the module pairs.
    modules: Dict[str, int] = {}
    for n in nodes:
        m = n.get("module") or "root"
        modules[m] = modules.get(m, 0) + 1

    # THE CANVAS DATA (the 08-14 perf fix): instead of thousands of SVG
    # DOM elements (laggy), the graph is embedded as JSON + drawn to a
    # <canvas> once per frame with level-of-detail (only visible nodes
    # and edges, sized for the current zoom).
    def _xy(n):
        return (60 + n.get("pos_x", 0) * 240, 40 + n.get("pos_y", 0) * 110)

    node_payload = []
    for n in nodes:
        x, y = _xy(n)
        lines = _wrap_label(str(n.get("label", n.get("id", ""))))
        w, h = _box_size(str(n.get("label", n.get("id", ""))))
        node_payload.append({
            "id": n.get("id", ""),
            "label": n.get("label", n.get("id", "")),
            "lines": lines,
            "state": n.get("state", ALIVE),
            "x": x, "y": y, "w": w, "h": h,
            "kind": n.get("kind", ""),
            "enters": n.get("enters"),
            "file": n.get("file", ""),
        })

    target_state = {n.get("id"): n.get("state", ALIVE) for n in nodes}
    src_state = {n.get("id"): n.get("state", ALIVE) for n in nodes}
    boxes = {n["id"]: (n["x"] + n["w"] / 2, n["y"] + n["h"] / 2,
                        n["w"], n["h"]) for n in node_payload}
    link_payload = []
    seen_links = set()
    for lk in links:
        s, t = lk.get("source"), lk.get("target")
        if s not in boxes or t not in boxes:
            continue
        key = (s, t)
        if key in seen_links:
            continue
        seen_links.add(key)
        sx, sy, sw, sh = boxes[s]
        tx, ty, tw, th = boxes[t]
        link_payload.append({
            "sx": sx, "sy": sy + sh / 2,
            "mx": tx, "my": sy + sh / 2,
            "tx": tx, "ty": ty - th / 2,
            "color": _wire_color(target_state.get(t, ALIVE),
                                 src_state.get(s, "")),
            "rel": lk.get("relation", ""),
        })

    graph_data = json.dumps({
        "nodes": node_payload,
        "links": link_payload,
        "summary": graph.get("summary", {}),
    }, separators=(",", ":")).replace("</", "<\\/")
    graph_w = max(1200, (max((n.get("pos_x", 0) for n in nodes), default=0) + 1) * 240 + 400)
    graph_h = max(500, (max((n.get("pos_y", 0) for n in nodes), default=0) + 1) * 110 + 200)

    # Module legend (the cross-graph pairs).
    legend = "".join(
        f'<span class="mod">{_esc(m)} ({c})</span>' for m, c in
        sorted(modules.items(), key=lambda kv: -kv[1])[:20])

    summary = graph.get("summary", {})
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Timeline — {_esc(title)}</title>
<style>
  :root {{ --bg:#0f172a; --surface:#1e293b; --text:#e2e8f0; --muted:#94a3b8;
           --alive:#34d399; --sick:#fbbf24; --dead:#f87171; }}
  * {{ box-sizing:border-box; margin:0; padding:0; }}
  body {{ background:var(--bg); color:var(--text); font-family:'Segoe UI',system-ui,sans-serif; }}
  header {{ padding:18px 28px; border-bottom:1px solid #334155; position:sticky; top:0; background:var(--bg); z-index:10; }}
  h1 {{ font-size:1.4rem; }}
  .sub {{ color:var(--muted); font-size:.85rem; margin-top:4px; }}
  .health-legend {{ display:flex; gap:14px; align-items:center; margin-top:6px; font-size:.78rem; color:var(--muted); }}
  .health-legend .swatch {{ display:inline-block; width:12px; height:12px; border-radius:3px; margin-right:4px; vertical-align:middle; }}
  .health-legend .g {{ background:#34d399; }} .health-legend .y {{ background:#fbbf24; }}
  .health-legend .o {{ background:#fb923c; }} .health-legend .c {{ background:#38bdf8; }} .health-legend .r {{ background:#f87171; }}
  .toolbar {{ margin-top:10px; display:flex; gap:10px; align-items:center; flex-wrap:wrap; }}
  input {{ background:#1e293b; border:1px solid #334155; color:var(--text); padding:6px 10px; border-radius:6px; font-size:.85rem; }}
  .stat {{ padding:2px 8px; border-radius:4px; font-size:.75rem; font-weight:600; }}
  .stat.alive {{ background:#34d39922; color:var(--alive); }}
  .stat.sick {{ background:#fbbf2422; color:var(--sick); }}
  .stat.warn {{ background:#fb923c22; color:#fb923c; }}
  .stat.dead {{ background:#f8717122; color:var(--dead); }}
  .stat.conn {{ background:#38bdf822; color:#38bdf8; }}
  .legend {{ padding:14px 28px; display:flex; gap:8px; flex-wrap:wrap; border-bottom:1px solid #334155; }}
  .mod {{ background:#1e293b; border:1px solid #334155; padding:3px 8px; border-radius:4px; font-size:.72rem; color:var(--muted); }}
  main {{ overflow:hidden; padding:0; position:relative; }}
  #viewport {{ width:100%; height:calc(100vh - 150px); overflow:hidden; cursor:grab; background:repeating-linear-gradient(45deg, #0b1220 0 12px, #0d1526 12px 24px); }}
  #viewport.dragging {{ cursor:grabbing; }}
  #graph {{ position:absolute; top:0; left:0; width:100%; height:100%; display:block; }}
  .zoom-controls {{ position:absolute; right:16px; bottom:16px; display:flex; flex-direction:column; gap:6px; z-index:20; }}
  .zoom-controls button {{ width:36px; height:36px; border-radius:8px; border:1px solid #334155; background:#1e293b; color:var(--text); font-size:1.1rem; cursor:pointer; }}
  .zoom-controls button:hover {{ border-color:var(--muted); }}
  #zoom-level {{ text-align:center; color:var(--muted); font-size:.75rem; padding:4px 0; }}
  #search-empty {{ color:var(--dead); padding:10px 28px; font-size:.9rem; display:none; }}
</style>
</head>
<body>
<header>
  <h1>⧉ {_esc(title)}</h1>
  <div class="sub">Timeline System — {_esc(graph.get("graph",{}).get("kind",""))} graph ·
    {summary.get("nodes",0)} nodes · {summary.get("links",0)} links ·
    <span class="stat alive">{summary.get("alive",0)} healthy</span>
    <span class="stat sick">{summary.get("sick",0)} caution</span>
    <span class="stat warn">{summary.get("warnings",0)} warning</span>
    <span class="stat conn">{summary.get("connections",0)} connection</span>
    <span class="stat dead">{summary.get("dead",0)} unused</span>
  </div>
  <div class="health-legend">
    <span><span class="swatch g"></span>Healthy/Used — traces green</span>
    <span><span class="swatch y"></span>Caution — diverges yellow</span>
    <span><span class="swatch o"></span>Warning — the blend</span>
    <span><span class="swatch c"></span>Connection — transitions to another graph/file</span>
    <span><span class="swatch r"></span>Errors/Unused — terminates red</span>
  </div>
  <div class="toolbar">
    <input id="search" type="text" placeholder="Search nodes…" autocomplete="off">
    <button onclick="resetView()">Reset</button>
    <label><input type="checkbox" id="hide-dead" onchange="toggleDead()"> hide dead</label>
    <select id="graph-switch" onchange="goGraph(this.value)" title="swap graphs">
      <option value="">— graphs —</option>
    </select>
    <span style="color:var(--muted);font-size:.78rem;margin-left:8px;">drag to pan · scroll to zoom</span>
  </div>
</header>
<div class="legend">{legend}</div>
<main>
<div id="viewport">
<canvas id="graph"></canvas>
<div class="zoom-controls">
  <button onclick="zoomBy(1.3)" title="zoom in">+</button>
  <div id="zoom-level">100%</div>
  <button onclick="zoomBy(1/1.3)" title="zoom out">−</button>
</div>
</div>
</main>
<div id="search-empty">No nodes match.</div>
<script id="graph-list-data" type="application/json">{gl}</script>
<script id="graph-data" type="application/json">{graph_data}</script>
<script>
// THE CANVAS RENDERER (the 08-14 perf fix): the graph is drawn to a
// <canvas> once per frame with level-of-detail — only the nodes + edges
// visible in the viewport, sized for the current zoom. No SVG DOM per
// node (thousands of elements were the lag); the canvas repaints fast.
const GRAPH = JSON.parse(document.getElementById('graph-data').textContent);
const NODES = GRAPH.nodes || [];
const LINKS = GRAPH.links || [];
const STATE_COLOR = {{alive:'#34d399', sick:'#fbbf24', dead:'#f87171', connection:'#38bdf8'}};
const canvas = document.getElementById('graph');
const viewport = document.getElementById('viewport');
const ctx = canvas.getContext('2d');
const MIN_SCALE = 1, MAX_SCALE = 1024;
let scale = 1, tx = 0, ty = 0;
let hideDead = false, query = '';
let gW = 1200, gH = 500;
for (const n of NODES) {{ if (n.x + n.w > gW) gW = n.x + n.w + 80; if (n.y + n.h > gH) gH = n.y + n.h + 80; }}

function resize() {{
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, viewport.clientWidth * dpr);
  canvas.height = Math.max(1, viewport.clientHeight * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}}

// THE DRAW (once per frame, LOD): skip off-screen nodes/edges entirely;
// skip labels when the box is smaller than ~24px on screen.
function draw() {{
  const vw = viewport.clientWidth, vh = viewport.clientHeight;
  ctx.clearRect(0, 0, vw, vh);
  const s = scale, dx = tx, dy = ty;
  const visible = (x, y, w, h) =>
    x * s + dx < vw + 200 && x * s + dx + w * s > -200 &&
    y * s + dy < vh + 200 && y * s + dy + h * s > -200;
  // Edges first (behind the nodes).
  ctx.lineWidth = 1.1;
  for (const lk of LINKS) {{
    if (!visible(lk.sx, lk.sy, 1, 1) && !visible(lk.tx, lk.ty, 1, 1)) continue;
    ctx.strokeStyle = lk.color;
    ctx.globalAlpha = 0.55;
    ctx.beginPath();
    ctx.moveTo(lk.sx * s + dx, lk.sy * s + dy);
    ctx.lineTo(lk.mx * s + dx, lk.my * s + dy);
    ctx.lineTo(lk.tx * s + dx, lk.ty * s + dy);
    ctx.stroke();
    ctx.globalAlpha = 1;
  }}
  // Nodes.
  const labelMin = 26 / s;  // label only when the box is ~26px+ on screen
  for (const n of NODES) {{
    if (!visible(n.x, n.y, n.w, n.h)) continue;
    if (hideDead && n.state === 'dead') continue;
    if (query && !(n.label.toLowerCase().includes(query) ||
                   (n.file || '').toLowerCase().includes(query))) continue;
    const x = n.x * s + dx, y = n.y * s + dy, w = n.w * s, h = n.h * s;
    const c = STATE_COLOR[n.state] || '#94a3b8';
    ctx.fillStyle = c + '22';
    ctx.strokeStyle = c;
    ctx.lineWidth = Math.max(1, 1.6 * s);
    ctx.beginPath();
    ctx.roundRect(x, y, w, h, 6);
    ctx.fill();
    ctx.stroke();
    if (w >= labelMin && s >= 0.7) {{
      ctx.fillStyle = c;
      ctx.font = (11 * s) + 'px monospace';
      for (let i = 0; i < n.lines.length; i++) {{
        ctx.fillText(n.lines[i], x + 8 * s, y + (15 + i * 14) * s);
      }}
      if (n.state === 'dead') {{
        ctx.fillStyle = '#f87171';
        ctx.font = (9 * s) + 'px monospace';
        ctx.fillText('DEAD END ✗', x + 8 * s, y + (n.h - 6) * s);
      }} else if (n.state === 'sick') {{
        ctx.fillStyle = '#fbbf24';
        ctx.font = (9 * s) + 'px monospace';
        ctx.fillText('SICK', x + 8 * s, y + (n.h - 6) * s);
      }} else if (n.state === 'connection') {{
        ctx.fillStyle = '#38bdf8';
        ctx.font = (9 * s) + 'px monospace';
        ctx.fillText('→ transition', x + 8 * s, y + (n.h - 6) * s);
      }}
    }}
  }}
  document.getElementById('zoom-level').textContent = Math.round(scale * 100) + '%';
}}

// PAN + ZOOM (RAF-throttled: one repaint per frame).
let rafPending = false;
function scheduleDraw() {{ if (!rafPending) {{ rafPending = true; requestAnimationFrame(() => {{ rafPending = false; draw(); }}); }} }}
function applyTransform() {{ scheduleDraw(); }}
function fitView() {{
  const vw = viewport.clientWidth, vh = viewport.clientHeight;
  // The fit scale (could be < 1 for a huge graph) — but the elements
  // must never be sub-native, so floor at MIN_SCALE and PAN to the
  // TOP-LEFT (the entry spine) instead of centering (centering a huge
  // graph pushes the origin off-screen).
  scale = Math.max(MIN_SCALE, Math.min(3, vw / gW, vh / gH));
  tx = 0; ty = 0;
  draw();
}}
let dragging = false, startX = 0, startY = 0, origTx = 0, origTy = 0;
viewport.addEventListener('mousedown', (e) => {{
  if (e.target.closest('a, button, input, select')) return;
  dragging = true; startX = e.clientX; startY = e.clientY;
  origTx = tx; origTy = ty;
  viewport.classList.add('dragging');
  e.preventDefault();
}});
window.addEventListener('mousemove', (e) => {{
  if (!dragging) return;
  tx = origTx + (e.clientX - startX);
  ty = origTy + (e.clientY - startY);
  scheduleDraw();
}});
window.addEventListener('mouseup', () => {{ dragging = false; viewport.classList.remove('dragging'); draw(); }});
viewport.addEventListener('wheel', (e) => {{
  e.preventDefault();
  const rect = viewport.getBoundingClientRect();
  const cx = e.clientX - rect.left, cy = e.clientY - rect.top;
  const factor = e.deltaY < 0 ? 1.2 : 1 / 1.2;
  const ns = Math.min(MAX_SCALE, Math.max(MIN_SCALE, scale * factor));
  tx = cx - (cx - tx) * (ns / scale);
  ty = cy - (cy - ty) * (ns / scale);
  scale = ns; draw();
}}, {{ passive: false }});
function zoomBy(factor) {{
  const rect = viewport.getBoundingClientRect();
  const cx = rect.width / 2, cy = rect.height / 2;
  const ns = Math.min(MAX_SCALE, Math.max(MIN_SCALE, scale * factor));
  tx = cx - (cx - tx) * (ns / scale);
  ty = cy - (cy - ty) * (ns / scale);
  scale = ns; draw();
}}
function resetView() {{ fitView(); }}
function toggleDead() {{ hideDead = document.getElementById('hide-dead').checked; draw(); }}
const search = document.getElementById('search');
search.addEventListener('input', () => {{ query = search.value.toLowerCase().trim(); draw(); }});
window.addEventListener('resize', () => {{ resize(); fitView(); }});
// THE GRAPH SWITCHER (absolute-rooted hrefs + page-depth resolution).
function pageDepth() {{
  const p = location.pathname;
  const i = p.indexOf('/graphs/');
  if (i < 0) return 0;
  const rest = p.slice(i + 8).split('/').filter(Boolean);
  return Math.max(0, rest.length - 1);
}}
function goGraph(href) {{ if (!href) return; location.href = '../'.repeat(pageDepth()) + href; }}
try {{
  const list = JSON.parse(document.getElementById('graph-list-data').textContent || '[]');
  const sel = document.getElementById('graph-switch');
  for (const g of list) {{
    const o = document.createElement('option');
    o.value = g.href; o.textContent = g.label;
    sel.appendChild(o);
  }}
  sel.style.display = list.length ? '' : 'none';
}} catch (e) {{ /* no graph list */ }}
resize();
fitView();
</script></script>
</body>
</html>
"""
    dest.write_text(html, encoding="utf-8")


def write_bundle(graph: dict, out_dir: Path, *, title: str = "",
                 graph_list: list | None = None) -> Path:
    """Write the two-file bundle. Returns the index.json path."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    index = out_dir / "index.json"
    htmlf = out_dir / "timeline.html"
    write_index(graph, index, title=title)
    write_html(graph, htmlf, title=title, graph_list=graph_list)
    return index
