"""Interactive knowledge graph visualizer — generates a self-contained HTML file.

Uses D3.js v7 (CDN) for force-directed layout. Zero Python runtime deps beyond
what relic already requires. Output is a single portable HTML file.

Visual encoding
---------------
Node size     : PageRank centrality (important files appear larger)
Node color    : Louvain community ID
Edge opacity  : evidence weight (ast=1.0, treesitter=0.8, regex=0.5, convention=0.3)
Edge width    : same scale as opacity
Node label    : filename (stem), full path on hover / click

Interaction
-----------
- Click node  : blast-radius overlay (dependents → red), side panel with metadata
- Hover edge  : tooltip with edge type + evidence
- Search box  : highlights matching nodes
- Filter      : by language, community
- Drag nodes  : pin/unpin positions
"""

from __future__ import annotations

import json
from pathlib import Path

import networkx as nx

from relic.centrality import compute_centrality

_DEP_EDGE_TYPES = {"imports", "uses", "calls", "extends", "tested_by"}

# Nord palette — matches relic style
_COMMUNITY_PALETTE = [
    "#88C0D0",  # frost blue
    "#81A1C1",  # steel blue
    "#5E81AC",  # deep blue
    "#A3BE8C",  # aurora green
    "#EBCB8B",  # aurora gold
    "#D08770",  # aurora orange
    "#BF616A",  # aurora red
    "#B48EAD",  # aurora purple
    "#8FBCBB",  # frost teal
    "#E5E9F0",  # snow
]

_EVIDENCE_WEIGHT = {
    "ast": 1.0,
    "treesitter": 0.8,
    "regex": 0.5,
    "convention": 0.3,
    "": 0.4,
}

_LANG_ICON = {
    "python": "🐍",
    "typescript": "TS",
    "javascript": "JS",
    "go": "Go",
    "rust": "Rs",
    "java": "Jv",
    "csharp": "C#",
    "kotlin": "Kt",
    "scala": "Sc",
    "php": "PHP",
    "swift": "Sw",
    "markdown": "Md",
    "data": "{}",
    "pyproject": "📦",
    "packagejson": "📦",
}


def _build_graph_data(G: nx.DiGraph) -> dict:
    """Serialize graph to JSON-serialisable dict for D3."""
    centrality_rows = compute_centrality(G)
    pr_map = {r["path"]: r["pagerank"] for r in centrality_rows}
    bt_map = {r["path"]: r["betweenness"] for r in centrality_rows}

    # Normalise pagerank to [6, 28] for node radius
    pr_vals = list(pr_map.values()) or [0]
    pr_min, pr_max = min(pr_vals), max(pr_vals)
    pr_range = pr_max - pr_min or 1

    def _radius(path: str) -> float:
        pr = pr_map.get(path, 0)
        return round(6 + 22 * (pr - pr_min) / pr_range, 2)

    file_nodes = [(n, d) for n, d in G.nodes(data=True) if d.get("ntype") == "file"]
    node_index = {n: i for i, (n, _) in enumerate(file_nodes)}

    nodes = []
    for n, d in file_nodes:
        community = d.get("community", -1)
        lang = d.get("language", "other")
        stem = Path(n).name
        nodes.append(
            {
                "id": n,
                "label": stem,
                "language": lang,
                "icon": _LANG_ICON.get(lang, ""),
                "community": community,
                "color": _COMMUNITY_PALETTE[community % len(_COMMUNITY_PALETTE)] if community >= 0 else "#4C566A",
                "radius": _radius(n),
                "pagerank": pr_map.get(n, 0),
                "betweenness": bt_map.get(n, 0),
                "subproject": d.get("subproject", ""),
                "in_degree": G.in_degree(n),
                "out_degree": G.out_degree(n),
                "exports": [
                    {"name": G.nodes[s].get("name", ""), "stype": G.nodes[s].get("stype", "")}
                    for s in G.successors(n)
                    if G.nodes[s].get("ntype") == "symbol"
                ][:8],
            }
        )

    links = []
    file_set = set(node_index)
    for u, v, d in G.edges(data=True):
        if u not in file_set or v not in file_set:
            continue
        etype = d.get("etype", "")
        if etype not in _DEP_EDGE_TYPES:
            continue
        evidence = d.get("evidence", "")
        weight = _EVIDENCE_WEIGHT.get(evidence, 0.4)
        links.append(
            {
                "source": node_index[u],
                "target": node_index[v],
                "etype": etype,
                "evidence": evidence,
                "weight": weight,
            }
        )

    communities = sorted({n["community"] for n in nodes if n["community"] >= 0})
    languages = sorted({n["language"] for n in nodes})
    subprojects = sorted({n["subproject"] for n in nodes if n["subproject"]})

    return {
        "nodes": nodes,
        "links": links,
        "meta": {
            "communities": communities,
            "languages": languages,
            "subprojects": subprojects,
            "palette": _COMMUNITY_PALETTE,
        },
    }


def generate_html(G: nx.DiGraph, title: str = "relic — knowledge graph") -> str:
    """Return a self-contained HTML string visualising *G*."""
    data = _build_graph_data(G)
    data_json = json.dumps(data, separators=(",", ":"))

    node_count = len(data["nodes"])
    link_count = len(data["links"])

    # Build community legend entries
    community_ids = data["meta"]["communities"]
    palette = data["meta"]["palette"]
    community_legend = "".join(
        f'<span class="legend-dot" style="background:{palette[cid % len(palette)]}"></span><span>Community {cid}</span>'
        for cid in community_ids[:10]
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>{title}</title>
<script src="https://cdn.jsdelivr.net/npm/d3@7/dist/d3.min.js"></script>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ background: #2E3440; color: #D8DEE9; font-family: ui-monospace,"Cascadia Code","JetBrains Mono",monospace; overflow: hidden; }}
#app {{ display: flex; height: 100vh; width: 100vw; }}
#sidebar {{ width: 280px; min-width: 280px; background: #3B4252; border-right: 1px solid #4C566A; display: flex; flex-direction: column; z-index: 10; }}
#sidebar-header {{ padding: 14px 16px 10px; border-bottom: 1px solid #4C566A; }}
#sidebar-header h1 {{ font-size: 15px; color: #88C0D0; letter-spacing: 1px; }}
#sidebar-header .meta {{ font-size: 11px; color: #697283; margin-top: 4px; }}
#controls {{ padding: 12px 16px; border-bottom: 1px solid #4C566A; display: flex; flex-direction: column; gap: 8px; }}
#search {{ background: #2E3440; border: 1px solid #4C566A; border-radius: 4px; padding: 6px 10px; color: #D8DEE9; font-family: inherit; font-size: 12px; width: 100%; outline: none; }}
#search:focus {{ border-color: #88C0D0; }}
#search::placeholder {{ color: #697283; }}
select {{ background: #2E3440; border: 1px solid #4C566A; border-radius: 4px; padding: 5px 8px; color: #D8DEE9; font-family: inherit; font-size: 11px; width: 100%; outline: none; cursor: pointer; }}
select:focus {{ border-color: #88C0D0; }}
#legend {{ padding: 10px 16px; border-bottom: 1px solid #4C566A; font-size: 11px; color: #697283; display: flex; flex-direction: column; gap: 5px; }}
#legend-title {{ color: #81A1C1; font-size: 11px; margin-bottom: 2px; }}
.legend-row {{ display: flex; align-items: center; gap: 6px; flex-wrap: wrap; row-gap: 4px; }}
.legend-dot {{ width: 10px; height: 10px; border-radius: 50%; display: inline-block; flex-shrink: 0; }}
#panel {{ flex: 1; overflow-y: auto; padding: 12px 16px; font-size: 12px; }}
#panel .empty {{ color: #697283; font-style: italic; }}
#panel h3 {{ color: #88C0D0; font-size: 13px; margin-bottom: 8px; word-break: break-all; }}
.panel-kv {{ display: flex; gap: 8px; margin-bottom: 4px; color: #81A1C1; }}
.panel-kv span:first-child {{ color: #697283; min-width: 80px; flex-shrink: 0; }}
.panel-kv span:last-child {{ color: #D8DEE9; word-break: break-all; }}
.panel-section {{ margin-top: 10px; }}
.panel-section-title {{ color: #697283; font-size: 10px; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 5px; }}
.export-tag {{ display: inline-block; background: #2E3440; border: 1px solid #4C566A; border-radius: 3px; padding: 2px 6px; margin: 2px; font-size: 10px; color: #81A1C1; }}
.export-tag .stype {{ color: #697283; }}
#canvas {{ flex: 1; position: relative; overflow: hidden; }}
svg {{ width: 100%; height: 100%; }}
.link {{ fill: none; stroke-linecap: round; }}
.node circle {{ cursor: pointer; transition: stroke-width 0.15s; }}
.node circle:hover {{ stroke-width: 3px; }}
.node text {{ pointer-events: none; font-size: 9px; fill: #D8DEE9; text-anchor: middle; dominant-baseline: central; }}
.node.highlighted circle {{ stroke: #EBCB8B !important; stroke-width: 3px; }}
.node.impact circle {{ stroke: #BF616A !important; stroke-width: 3px; fill: #3d1f22 !important; }}
.node.dimmed circle {{ opacity: 0.15; }}
.node.dimmed text {{ opacity: 0.1; }}
.link.dimmed {{ opacity: 0.03 !important; }}
#tooltip {{ position: absolute; background: #3B4252; border: 1px solid #4C566A; border-radius: 4px; padding: 7px 10px; font-size: 11px; pointer-events: none; opacity: 0; transition: opacity 0.12s; max-width: 220px; z-index: 100; }}
#tooltip .tip-etype {{ color: #88C0D0; }}
#tooltip .tip-ev {{ color: #697283; }}
#zoom-controls {{ position: absolute; bottom: 16px; right: 16px; display: flex; flex-direction: column; gap: 4px; }}
.zoom-btn {{ background: #3B4252; border: 1px solid #4C566A; color: #D8DEE9; width: 30px; height: 30px; border-radius: 4px; cursor: pointer; font-size: 16px; display: flex; align-items: center; justify-content: center; }}
.zoom-btn:hover {{ border-color: #88C0D0; color: #88C0D0; }}
#stats-bar {{ position: absolute; top: 10px; right: 16px; font-size: 10px; color: #697283; background: #3B425299; padding: 4px 10px; border-radius: 4px; }}
</style>
</head>
<body>
<div id="app">
  <div id="sidebar">
    <div id="sidebar-header">
      <h1>⬢ relic</h1>
      <div class="meta">{node_count} files · {link_count} edges</div>
    </div>
    <div id="controls">
      <input id="search" type="text" placeholder="search files…" autocomplete="off"/>
      <select id="filter-lang"><option value="">all languages</option></select>
      <select id="filter-community"><option value="">all communities</option></select>
      <select id="filter-subproject"><option value="">all subprojects</option></select>
    </div>
    <div id="legend">
      <div id="legend-title">communities</div>
      <div class="legend-row">{community_legend}</div>
      <div style="margin-top:8px;color:#697283;">
        <div style="display:flex;align-items:center;gap:6px;margin-bottom:3px;">
          <svg width="30" height="6"><line x1="0" y1="3" x2="30" y2="3" stroke="#88C0D0" stroke-width="2.5"/></svg>ast
        </div>
        <div style="display:flex;align-items:center;gap:6px;margin-bottom:3px;">
          <svg width="30" height="6"><line x1="0" y1="3" x2="30" y2="3" stroke="#81A1C1" stroke-width="1.8"/></svg>treesitter
        </div>
        <div style="display:flex;align-items:center;gap:6px;margin-bottom:3px;">
          <svg width="30" height="6"><line x1="0" y1="3" x2="30" y2="3" stroke="#697283" stroke-width="1.2"/></svg>regex
        </div>
        <div style="display:flex;align-items:center;gap:6px;">
          <svg width="30" height="6"><line x1="0" y1="3" x2="30" y2="3" stroke="#697283" stroke-width="1" stroke-dasharray="3,2"/></svg>convention
        </div>
      </div>
    </div>
    <div id="panel"><div class="empty">click a node to inspect</div></div>
  </div>
  <div id="canvas">
    <div id="tooltip"></div>
    <div id="stats-bar">node size = PageRank · color = community</div>
    <div id="zoom-controls">
      <button class="zoom-btn" id="zoom-in">+</button>
      <button class="zoom-btn" id="zoom-reset">⊙</button>
      <button class="zoom-btn" id="zoom-out">−</button>
    </div>
  </div>
</div>
<script>
const GRAPH = {data_json};

// ── Populate filter dropdowns ──────────────────────────────────────────────
const langSel = document.getElementById('filter-lang');
const commSel = document.getElementById('filter-community');
const spSel   = document.getElementById('filter-subproject');
GRAPH.meta.languages.forEach(l => {{
  const o = document.createElement('option'); o.value = l; o.text = l; langSel.appendChild(o);
}});
GRAPH.meta.communities.forEach(c => {{
  const o = document.createElement('option'); o.value = c; o.text = `community ${{c}}`; commSel.appendChild(o);
}});
GRAPH.meta.subprojects.forEach(s => {{
  const o = document.createElement('option'); o.value = s; o.text = s; spSel.appendChild(o);
}});

// ── D3 setup ──────────────────────────────────────────────────────────────
const canvas = document.getElementById('canvas');
const W = canvas.clientWidth, H = canvas.clientHeight;

const svg = d3.select('#canvas').append('svg');
const g   = svg.append('g');

const zoom = d3.zoom().scaleExtent([0.05, 8]).on('zoom', e => g.attr('transform', e.transform));
svg.call(zoom);

// ── Build impact map (reverse adjacency for blast-radius) ─────────────────
const impactMap = new Map(); // nodeIndex → Set of dependent indices
GRAPH.nodes.forEach((_, i) => impactMap.set(i, new Set()));
GRAPH.links.forEach(l => {{
  const src = typeof l.source === 'object' ? l.source.index : l.source;
  const tgt = typeof l.target === 'object' ? l.target.index : l.target;
  if (!impactMap.has(tgt)) impactMap.set(tgt, new Set());
  impactMap.get(tgt).add(src);
}});

function getImpact(idx) {{
  const visited = new Set();
  const queue = [idx];
  while (queue.length) {{
    const cur = queue.shift();
    if (visited.has(cur)) continue;
    visited.add(cur);
    (impactMap.get(cur) || new Set()).forEach(n => queue.push(n));
  }}
  visited.delete(idx);
  return visited;
}}

// ── Force simulation ───────────────────────────────────────────────────────
const sim = d3.forceSimulation(GRAPH.nodes)
  .force('link', d3.forceLink(GRAPH.links).id((_, i) => i).distance(d => 60 + (1 - d.weight) * 40).strength(0.4))
  .force('charge', d3.forceManyBody().strength(d => -120 - d.radius * 6))
  .force('center', d3.forceCenter(W / 2, H / 2))
  .force('collision', d3.forceCollide().radius(d => d.radius + 4));

// ── Links ──────────────────────────────────────────────────────────────────
const link = g.append('g').attr('class', 'links')
  .selectAll('line')
  .data(GRAPH.links)
  .join('line')
  .attr('class', 'link')
  .attr('stroke', '#4C566A')
  .attr('stroke-width', d => 0.6 + d.weight * 2)
  .attr('stroke-opacity', d => 0.15 + d.weight * 0.45)
  .attr('stroke-dasharray', d => d.evidence === 'convention' ? '4,3' : null);

// ── Nodes ──────────────────────────────────────────────────────────────────
const node = g.append('g').attr('class', 'nodes')
  .selectAll('g')
  .data(GRAPH.nodes)
  .join('g')
  .attr('class', 'node')
  .call(d3.drag()
    .on('start', (e, d) => {{ if (!e.active) sim.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; }})
    .on('drag',  (e, d) => {{ d.fx = e.x; d.fy = e.y; }})
    .on('end',   (e, d) => {{ if (!e.active) sim.alphaTarget(0); }})
  );

node.append('circle')
  .attr('r', d => d.radius)
  .attr('fill', d => d.color + '33')
  .attr('stroke', d => d.color)
  .attr('stroke-width', 1.5);

node.append('text')
  .attr('y', d => d.radius + 10)
  .style('font-size', d => Math.max(7, Math.min(11, d.radius * 0.7)) + 'px')
  .style('fill', '#8B9BAD')
  .text(d => d.label.length > 16 ? d.label.slice(0, 14) + '…' : d.label);

// ── Tooltip ────────────────────────────────────────────────────────────────
const tooltip = document.getElementById('tooltip');
link
  .on('mouseenter', (e, d) => {{
    tooltip.innerHTML = `<span class="tip-etype">${{d.etype}}</span> <span class="tip-ev">[evidence: ${{d.evidence || '?'}}]</span>`;
    tooltip.style.opacity = '1';
  }})
  .on('mousemove', e => {{
    const r = canvas.getBoundingClientRect();
    tooltip.style.left = (e.clientX - r.left + 12) + 'px';
    tooltip.style.top  = (e.clientY - r.top  - 10) + 'px';
  }})
  .on('mouseleave', () => {{ tooltip.style.opacity = '0'; }});

// ── Click → panel + blast-radius ──────────────────────────────────────────
let selected = null;
node.on('click', (e, d) => {{
  e.stopPropagation();
  const idx = GRAPH.nodes.indexOf(d);
  if (selected === idx) {{ clearSelection(); return; }}
  selected = idx;
  const impact = getImpact(idx);

  node.classed('highlighted', (_, i) => i === idx)
      .classed('impact',      (_, i) => impact.has(i))
      .classed('dimmed',      (_, i) => i !== idx && !impact.has(i));

  link.classed('dimmed', l => {{
    const s = typeof l.source === 'object' ? GRAPH.nodes.indexOf(l.source) : l.source;
    const t = typeof l.target === 'object' ? GRAPH.nodes.indexOf(l.target) : l.target;
    return s !== idx && t !== idx && !impact.has(s) && !impact.has(t);
  }});

  renderPanel(d, impact.size);
}});
svg.on('click', clearSelection);

function clearSelection() {{
  selected = null;
  node.classed('highlighted dimmed impact', false);
  link.classed('dimmed', false);
  document.getElementById('panel').innerHTML = '<div class="empty">click a node to inspect</div>';
}}

function renderPanel(d, impactCount) {{
  const exports = (d.exports || []).map(e =>
    `<span class="export-tag"><span class="stype">${{e.stype}} </span>${{e.name}}</span>`
  ).join('');
  document.getElementById('panel').innerHTML = `
    <h3>${{d.id}}</h3>
    <div class="panel-kv"><span>language</span><span>${{d.language}}</span></div>
    <div class="panel-kv"><span>community</span><span>${{d.community >= 0 ? d.community : '—'}}</span></div>
    <div class="panel-kv"><span>pagerank</span><span>${{d.pagerank.toFixed(5)}}</span></div>
    <div class="panel-kv"><span>betweenness</span><span>${{(d.betweenness||0).toFixed(5)}}</span></div>
    <div class="panel-kv"><span>in-degree</span><span>${{d.in_degree}} dependents</span></div>
    <div class="panel-kv"><span>out-degree</span><span>${{d.out_degree}} dependencies</span></div>
    <div class="panel-kv"><span>blast radius</span><span style="color:#BF616A">${{impactCount}} files affected</span></div>
    ${{d.subproject ? `<div class="panel-kv"><span>subproject</span><span>${{d.subproject}}</span></div>` : ''}}
    ${{exports ? `<div class="panel-section"><div class="panel-section-title">exports (top 8)</div>${{exports}}</div>` : ''}}
  `;
}}

// ── Search ─────────────────────────────────────────────────────────────────
document.getElementById('search').addEventListener('input', e => {{
  const q = e.target.value.trim().toLowerCase();
  if (!q) {{ node.classed('highlighted dimmed', false); link.classed('dimmed', false); return; }}
  node.classed('highlighted', d => d.id.toLowerCase().includes(q) || d.label.toLowerCase().includes(q))
      .classed('dimmed',      d => !d.id.toLowerCase().includes(q) && !d.label.toLowerCase().includes(q));
  link.classed('dimmed', true);
}});

// ── Filters ────────────────────────────────────────────────────────────────
function applyFilters() {{
  const lang  = langSel.value;
  const comm  = commSel.value !== '' ? parseInt(commSel.value) : null;
  const sp    = spSel.value;
  node.classed('dimmed', d =>
    (lang && d.language !== lang) ||
    (comm !== null && d.community !== comm) ||
    (sp && d.subproject !== sp)
  );
  link.classed('dimmed', l => {{
    const s = typeof l.source === 'object' ? l.source : GRAPH.nodes[l.source];
    const t = typeof l.target === 'object' ? l.target : GRAPH.nodes[l.target];
    return (lang && (s.language !== lang || t.language !== lang)) ||
           (comm !== null && (s.community !== comm || t.community !== comm)) ||
           (sp && (s.subproject !== sp || t.subproject !== sp));
  }});
}}
[langSel, commSel, spSel].forEach(el => el.addEventListener('change', applyFilters));

// ── Zoom controls ──────────────────────────────────────────────────────────
document.getElementById('zoom-in') .addEventListener('click', () => svg.transition().call(zoom.scaleBy, 1.4));
document.getElementById('zoom-out').addEventListener('click', () => svg.transition().call(zoom.scaleBy, 0.7));
document.getElementById('zoom-reset').addEventListener('click', () => svg.transition().call(zoom.transform, d3.zoomIdentity.translate(W/2, H/2).scale(1).translate(-W/2, -H/2)));

// ── Tick ───────────────────────────────────────────────────────────────────
sim.on('tick', () => {{
  link.attr('x1', d => d.source.x).attr('y1', d => d.source.y)
      .attr('x2', d => d.target.x).attr('y2', d => d.target.y);
  node.attr('transform', d => `translate(${{d.x}},${{d.y}})`);
}});
</script>
</body>
</html>"""

    return html


def open_viz(G: nx.DiGraph, out_path: Path | None = None) -> Path:
    """Generate the HTML and open in browser (or save to *out_path*)."""
    import tempfile
    import webbrowser

    html = generate_html(G)
    if out_path:
        out_path.write_text(html, encoding="utf-8")
        target = out_path.resolve()
    else:
        tmp = tempfile.NamedTemporaryFile(suffix=".html", delete=False, prefix="relic_viz_")
        tmp.write(html.encode("utf-8"))
        tmp.close()
        target = Path(tmp.name)

    webbrowser.open(target.as_uri())
    return target
