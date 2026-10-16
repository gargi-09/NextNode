/* Graph visualizations for the static pages. They render the API's own payloads:
 *   careerGraph(svg, analysis.graph)        ← GET /api/v1/analyze/{user}/{job}  `graph`
 *   roadmapFlow(el, analysis.roadmap_flow)  ← the same response's `roadmap_flow`
 *   ontologyGraph(svg, ontologyGraph)       ← GET /api/v1/ontology/graph
 * so they work the same against the in-browser sandbox API and a live backend.
 * Needs d3 v7 (loaded from cdnjs by the page). */
(function () {
  "use strict";
  const css = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const STATUS = { met: "--green", partial: "--amber", missing: "--red", owned: "--teal", prerequisite: "--slate" };
  const TYPE = { user: "--violet", job: "--blue", domain: "--faint" };
  const LINK = { HAS_SKILL: "--teal", REQUIRES_SKILL: "--blue", PREREQUISITE_OF: "--amber", TARGETS: "--violet", IN_DOMAIN: "--faint", BELONGS_TO: "--line" };
  const CATEGORY = { language: "--violet", ml_framework: "--blue", platform: "--teal", concept: "--amber", tool: "--red" };
  const sims = new WeakMap();

  function noD3(svg) {
    svg.innerHTML = "";
    const t = document.createElementNS("http://www.w3.org/2000/svg", "text");
    t.setAttribute("x", "50%"); t.setAttribute("y", "50%"); t.setAttribute("text-anchor", "middle");
    t.textContent = "The graph needs d3 from cdnjs; check your internet connection.";
    svg.append(t);
  }

  function force(svg, nodes, links, { color, radius, linkColor, dashed, arrow, distance = 80, charge = -240, label = d => d.label, pin }) {
    if (!window.d3) return noD3(svg);
    sims.get(svg)?.stop();
    const g = d3.select(svg); g.selectAll("*").remove();
    const { width, height } = svg.getBoundingClientRect();
    g.attr("viewBox", `0 0 ${width} ${height}`);
    const id = `arr-${Math.random().toString(36).slice(2, 7)}`;
    g.append("defs").append("marker").attr("id", id).attr("viewBox", "0 0 10 10").attr("refX", 20).attr("refY", 5)
      .attr("markerWidth", 6).attr("markerHeight", 6).attr("orient", "auto")
      .append("path").attr("d", "M0 0 L10 5 L0 10 z").attr("fill", css("--amber"));
    const zoomLayer = g.append("g");
    g.call(d3.zoom().scaleExtent([0.3, 3]).on("zoom", e => zoomLayer.attr("transform", e.transform)));
    const link = zoomLayer.append("g").selectAll("line").data(links).join("line")
      .attr("stroke", linkColor).attr("stroke-opacity", .75).attr("stroke-width", d => (dashed(d) ? 1.6 : 1.1))
      .attr("stroke-dasharray", d => (dashed(d) ? "5 4" : null)).attr("marker-end", d => (arrow(d) ? `url(#${id})` : null));
    const node = zoomLayer.append("g").selectAll("g").data(nodes).join("g").style("cursor", "grab");
    node.append("circle").attr("r", radius).attr("fill", color).attr("stroke", css("--panel")).attr("stroke-width", 2);
    node.append("text").attr("dy", d => radius(d) + 12).attr("text-anchor", "middle").attr("fill", css("--text"))
      .style("font", "11px system-ui, sans-serif").style("pointer-events", "none").text(label);
    node.append("title").text(d => [d.label, d.type, d.status, d.category, d.proficiency != null ? `you ${Math.round(d.proficiency * 100)}%` : null,
      d.required_weight != null ? `required ${Math.round(d.required_weight * 100)}%` : null].filter(Boolean).join(" · "));
    const k = Math.max(0.35, Math.min(1, width / 760)); // tighter layout on small screens
    nodes.forEach((d, i) => { // start near the centre (d3 defaults to the origin)
      const a = i * 2.4, r = 30 + 6 * i;
      d.x ??= width / 2 + r * Math.cos(a); d.y ??= height / 2 + r * Math.sin(a);
    });
    const dist = typeof distance === "function" ? d => distance(d) * k : distance * k;
    const sim = d3.forceSimulation(nodes)
      .force("link", d3.forceLink(links).id(d => d.id).distance(dist))
      .force("charge", d3.forceManyBody().strength(charge * k))
      .force("center", d3.forceCenter(width / 2, height / 2))
      .force("collide", d3.forceCollide(d => radius(d) + 22 * k))
      .force("y", d3.forceY(height / 2).strength(0.04));
    if (pin) sim.force("x", d3.forceX(d => pin(d, width) ?? width / 2).strength(d => (pin(d, width) == null ? 0.02 : 0.35)));
    sim.on("tick", () => {
      for (const d of nodes) { // keep everything inside the frame
        d.x = Math.max(24, Math.min(width - 24, d.x));
        d.y = Math.max(20, Math.min(height - 28, d.y));
      }
      link.attr("x1", d => d.source.x).attr("y1", d => d.source.y).attr("x2", d => d.target.x).attr("y2", d => d.target.y);
      node.attr("transform", d => `translate(${d.x},${d.y})`);
    });
    node.call(d3.drag()
      .on("start", (e, d) => { if (!e.active) sim.alphaTarget(.3).restart(); d.fx = d.x; d.fy = d.y; })
      .on("drag", (e, d) => { d.fx = e.x; d.fy = e.y; })
      .on("end", (e, d) => { if (!e.active) sim.alphaTarget(0); d.fx = null; d.fy = null; }));
    sims.set(svg, sim);
    return { node };
  }

  function careerGraph(svg, graph) {
    const nodes = graph.nodes.map(n => ({ ...n })), links = graph.links.map(l => ({ ...l }));
    return force(svg, nodes, links, {
      color: d => css(d.type === "skill" ? STATUS[d.status] || "--slate" : TYPE[d.type]),
      radius: d => (d.type === "user" || d.type === "job" ? 13 : d.type === "domain" ? 9 : 4 + (d.val || 1.5) * 1.6),
      linkColor: d => css(LINK[d.type] || "--line"),
      dashed: d => d.type === "PREREQUISITE_OF", arrow: d => d.type === "PREREQUISITE_OF",
      distance: d => (d.type === "PREREQUISITE_OF" ? 75 : 140), charge: -460,
      pin: (d, w) => (d.type === "user" ? w * 0.1 : d.type === "job" ? w * 0.9 : null),
    });
  }

  function ontologyGraph(svg, graph, highlight = "") {
    const nodes = graph.nodes.map(n => ({ ...n })), links = graph.links.map(l => ({ ...l }));
    const h = highlight.trim().toLowerCase();
    return force(svg, nodes, links, {
      color: d => (h && d.label.toLowerCase().includes(h) ? "#facc15" : css(d.type === "domain" ? "--faint" : CATEGORY[d.category] || "--slate")),
      radius: d => (d.type === "domain" ? 11 : h && d.label.toLowerCase().includes(h) ? 9 : 6),
      linkColor: d => css(LINK[d.type] || "--line"),
      dashed: d => d.type === "PREREQUISITE_OF", arrow: d => d.type === "PREREQUISITE_OF",
      distance: d => (d.type === "BELONGS_TO" ? 120 : 50), charge: -120,
    });
  }

  /* roadmap_flow: absolutely positioned cards + SVG edges, scaled to fit the container */
  function roadmapFlow(el, flow) {
    el.innerHTML = "";
    if (!flow.nodes.length) { el.innerHTML = '<p class="empty">No gaps: nothing to learn for this job.</p>'; return; }
    const SIZE = { roadmapStep: [250, 104], ownedSkill: [180, 64] };
    const xs = flow.nodes.map(n => n.position.x), ys = flow.nodes.map(n => n.position.y);
    const minX = Math.min(...xs), minY = Math.min(...ys), pad = 16;
    const width = Math.max(...flow.nodes.map(n => n.position.x + SIZE[n.type][0])) - minX + pad * 2;
    const height = Math.max(...flow.nodes.map(n => n.position.y + SIZE[n.type][1])) - minY + pad * 2;
    const at = n => ({ x: n.position.x - minX + pad, y: n.position.y - minY + pad, w: SIZE[n.type][0], h: SIZE[n.type][1] });
    const box = {}; flow.nodes.forEach(n => { box[n.id] = at(n); });

    const stage = document.createElement("div");
    stage.className = "flow-stage"; stage.style.width = `${width}px`; stage.style.height = `${height}px`;
    const NS = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("width", width); svg.setAttribute("height", height); svg.classList.add("flow-edges");
    for (const e of flow.edges) {
      const s = box[e.source], t = box[e.target]; if (!s || !t) continue;
      const unlocks = e.data.kind === "unlocks";
      const [x1, y1] = unlocks ? [s.x + s.w, s.y + s.h / 2] : [s.x + s.w / 2, s.y + s.h];
      const [x2, y2] = unlocks ? [t.x, t.y + t.h / 2] : [t.x + t.w / 2, t.y];
      const p = document.createElementNS(NS, "path");
      p.setAttribute("d", unlocks ? `M${x1} ${y1} C${x1 + 50} ${y1}, ${x2 - 50} ${y2}, ${x2} ${y2}` : `M${x1} ${y1} L${x2} ${y2}`);
      p.setAttribute("fill", "none"); p.setAttribute("stroke", css(unlocks ? "--teal" : "--faint"));
      p.setAttribute("stroke-width", "1.5"); if (unlocks) p.setAttribute("stroke-dasharray", "5 4");
      svg.append(p);
    }
    stage.append(svg);
    for (const n of flow.nodes) {
      const b = box[n.id], d = document.createElement("div");
      d.className = n.type === "roadmapStep" ? `flow-node step ${n.data.priority}` : "flow-node owned";
      Object.assign(d.style, { left: `${b.x}px`, top: `${b.y}px`, width: `${b.w}px`, height: `${b.h}px` });
      if (n.type === "roadmapStep") {
        d.innerHTML = `<div class="t"><span class="rk"></span><b></b></div><div class="chips"><span class="chip"></span><span class="chip"></span></div><div class="why"></div>`;
        d.querySelector(".rk").textContent = n.data.rank; d.querySelector("b").textContent = n.data.label;
        const [c1, c2] = d.querySelectorAll(".chip"); c1.textContent = n.data.priority; c2.textContent = n.data.status;
        d.querySelector(".why").textContent = n.data.reason;
      } else {
        d.innerHTML = `<div class="why">you already have</div><b></b><div class="why"></div>`;
        d.querySelector("b").textContent = n.data.label;
        d.querySelectorAll(".why")[1].textContent = n.data.proficiency != null ? `${Math.round(n.data.proficiency * 100)}%` : "";
      }
      stage.append(d);
    }
    el.append(stage);
    const fit = () => { const s = Math.min(1, el.clientWidth / width); stage.style.transform = `scale(${s})`; el.style.height = `${height * s}px`; };
    fit(); new ResizeObserver(fit).observe(el);
  }

  window.NNViz = { careerGraph, ontologyGraph, roadmapFlow };
})();
