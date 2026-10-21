/* Charts for the team store: hand-drawn SVG, no library. The forms (horizontal and
   vertical bars, stacked bars, line with a crosshair, tooltips, a data-table
   fallback) follow the project's chart catalogue; the colours are the site's
   tokens (riffle.css). Each builder returns nothing and draws into `el`. */
(function () {
  var NS = "http://www.w3.org/2000/svg";
  function h(t, c, x) { var e = document.createElement(t); if (c) e.className = c; if (x != null) e.textContent = x; return e; }
  function S(tag, a, p) {
    var e = document.createElementNS(NS, tag);
    for (var k in a) { if (k === "text") e.textContent = a[k]; else e.setAttribute(k, a[k]); }
    if (p) p.appendChild(e);
    return e;
  }
  var lin = function (d0, d1, r0, r1) { return function (v) { return d1 === d0 ? r0 : r0 + (v - d0) / (d1 - d0) * (r1 - r0); }; };
  var nf = function (v) { return Math.abs(v) >= 1e6 ? (v / 1e6).toFixed(1) + "M" : Math.abs(v) >= 1e4 ? (v / 1e3).toFixed(0) + "k" : Number.isInteger(v) ? v.toLocaleString() : String(+v.toFixed(2)); };

  /* one tooltip for the page */
  var tip = null, last = null;
  function tipEl() { if (!tip) { tip = h("div"); tip.id = "tip"; tip.hidden = true; tip.setAttribute("role", "tooltip"); document.body.appendChild(tip); } return tip; }
  function show(t, x, y) {
    var el = tipEl(); el.replaceChildren(); el.append(h("div", "tt", t.t));
    t.rows.forEach(function (r) {
      var d = h("div", "tr");
      if (r.c) { var k = h("i"); k.style.background = r.c; d.append(k); }
      d.append(h("b", null, r.v), h("span", null, r.n || "")); el.append(d);
    });
    el.hidden = false;
    var bw = el.offsetWidth, bh = el.offsetHeight, L = x + 14, T = y + 14;
    if (L + bw > innerWidth - 8) L = x - bw - 14;
    if (T + bh > innerHeight - 8) T = y - bh - 14;
    el.style.left = Math.max(8, L) + "px"; el.style.top = Math.max(8, T) + "px";
  }
  document.addEventListener("pointermove", function (e) {
    var t = e.target && e.target.__tip ? e.target : null;
    if (t !== last) { if (last && last.__out) last.__out(); last = t; if (t && t.__over) t.__over(); }
    if (t) show(t.__tip, e.clientX, e.clientY); else if (tip) tip.hidden = true;
  });
  document.addEventListener("focusin", function (e) { var t = e.target; if (t && t.__tip) { var r = t.getBoundingClientRect(); show(t.__tip, r.left + r.width / 2, r.top + r.height / 2); } });
  document.addEventListener("focusout", function (e) { if (e.target && e.target.__tip && tip) tip.hidden = true; });
  function mark(e, t, rows) {
    e.__tip = { t: t, rows: rows }; e.setAttribute("tabindex", "0");
    e.setAttribute("aria-label", t + ": " + rows.map(function (r) { return (r.n ? r.n + " " : "") + r.v; }).join(", "));
    e.__over = function () { e.style.opacity = ".8"; }; e.__out = function () { e.style.opacity = ""; };
    return e;
  }
  function frame(W, H, m, label) {
    var svg = S("svg", { viewBox: "0 0 " + W + " " + H, role: "img", "aria-label": label });
    var g = S("g", { transform: "translate(" + m.l + "," + m.t + ")" }, svg);
    return { svg: svg, g: g, w: W - m.l - m.r, h: H - m.t - m.b };
  }
  function legend(items) {
    var d = h("div", "lg");
    items.forEach(function (it) { var s = h("span"), k = h("i", it.k || "rc"); k.style.background = it.c; s.append(k, document.createTextNode(it.n)); d.append(s); });
    return d;
  }
  function clip(s, n) { s = String(s); return s.length > n ? s.slice(0, n - 1) + "…" : s; }
  function done(el, c, lg, table) {
    el.replaceChildren(c.svg); if (lg) el.append(lg);
    if (table) {
      var t = h("table", "t"), th = h("thead"), tb = h("tbody"), tr = h("tr");
      table.head.forEach(function (x, i) { var c2 = h("th", i ? "n" : "", x); tr.append(c2); }); th.append(tr);
      table.rows.forEach(function (r) { var q = h("tr"); r.forEach(function (x, i) { q.append(h("td", i ? "n" : "", x)); }); tb.append(q); });
      t.append(th, tb);
      var w = h("div", "tablewrap"); w.style.marginTop = "12px"; w.style.maxHeight = "220px"; w.append(t); w.hidden = true;
      var b = h("button", "btn", "Show data table"); b.style.marginTop = "8px"; b.style.padding = "0";
      b.onclick = function () { w.hidden = !w.hidden; b.textContent = w.hidden ? "Show data table" : "Hide data table"; };
      el.append(b, w);
    }
  }
  function empty(el, msg) { el.replaceChildren(h("div", "empty", msg || "Nothing to chart yet.")); }

  /* horizontal bars: items [{l, v, c?, n?}] */
  function hbar(el, items, o) {
    o = o || {}; if (!items.length) return empty(el);
    var rowH = 26, W = 560, m = { l: o.ml || 150, r: 56, t: 6, b: 6 }, H = items.length * rowH + m.t + m.b;
    var c = frame(W, H, m, o.aria || "Bar chart"), g = c.g, fv = o.fv || nf;
    var max = o.max || Math.max.apply(null, items.map(function (i) { return i.v; })) * 1.08 || 1, xs = lin(0, max, 0, c.w);
    items.forEach(function (it, i) {
      var y = i * rowH + (rowH - 14) / 2, bw = Math.max(2, xs(it.v)), col = it.c || o.c || "var(--s1)";
      S("text", { class: "tl", x: -8, y: y + 11, "text-anchor": "end", text: clip(it.l, Math.floor(m.l / 6.4)) }, g);
      mark(S("rect", { x: 0, y: y, width: bw, height: 14, rx: 3, style: "fill:" + col }, g), it.l, [{ c: col, v: fv(it.v), n: it.n || o.vn || "" }]);
      S("text", { class: "tl", x: bw + 6, y: y + 11, text: fv(it.v) }, g);
    });
    S("line", { class: "ax", x1: 0, x2: 0, y1: 0, y2: c.h }, g);
    done(el, c, o.legend ? legend(o.legend) : null, { head: [o.th || "Item", o.vh || "Value"], rows: items.map(function (i) { return [i.l, fv(i.v)]; }) });
  }

  /* stacked horizontal bars: rows [{l, vals:[..]}], series [{n, c}] */
  function stackh(el, rows, series, o) {
    o = o || {}; if (!rows.length) return empty(el);
    var rowH = 28, W = 480, m = { l: o.ml || 90, r: 14, t: 6, b: 24 }, H = rows.length * rowH + m.t + m.b;
    var c = frame(W, H, m, o.aria || "Stacked bar chart"), g = c.g;
    var max = Math.max.apply(null, rows.map(function (r) { return r.vals.reduce(function (a, b) { return a + b; }, 0); })) || 1, xs = lin(0, max, 0, c.w);
    [0, .25, .5, .75, 1].forEach(function (f) { var x = xs(max * f); S("line", { class: "gl", x1: x, x2: x, y1: 0, y2: rows.length * rowH }, g); S("text", { class: "tk", x: x, y: rows.length * rowH + 16, "text-anchor": "middle", text: nf(Math.round(max * f)) }, g); });
    rows.forEach(function (r, i) {
      var y = i * rowH + 5, cum = 0;
      S("text", { class: "tl", x: -8, y: y + 13, "text-anchor": "end", text: clip(r.l, Math.floor(m.l / 6.4)) }, g);
      r.vals.forEach(function (v, j) {
        if (!v) return;
        mark(S("rect", { x: xs(cum) + (cum ? 1 : 0), y: y, width: Math.max(2, xs(v) - 1), height: 18, rx: 2, style: "fill:" + series[j].c }, g), r.l,
          r.vals.map(function (vv, k) { return { c: series[k].c, v: nf(vv), n: series[k].n }; }));
        cum += v;
      });
    });
    done(el, c, legend(series), { head: [o.th || "Item"].concat(series.map(function (s) { return s.n; })), rows: rows.map(function (r) { return [r.l].concat(r.vals.map(nf)); }) });
  }

  /* vertical bars: items [{l, v}] (histograms, small series) */
  function vbar(el, items, o) {
    o = o || {}; if (!items.length) return empty(el);
    var W = 480, H = o.h || 220, m = { l: 48, r: 12, t: 12, b: 34 }, c = frame(W, H, m, o.aria || "Column chart"), g = c.g, fv = o.fv || nf;
    var max = Math.max.apply(null, items.map(function (i) { return i.v; })) || 1, ys = lin(0, max * 1.08, c.h, 0);
    [0, .5, 1].forEach(function (f) { var y = ys(max * f); S("line", { class: "gl", x1: 0, x2: c.w, y1: y, y2: y }, g); S("text", { class: "tk", x: -8, y: y + 3.5, "text-anchor": "end", text: nf(max * f) }, g); });
    var band = c.w / items.length, bw = Math.max(2, Math.min(28, band - 2));
    var every = Math.ceil(items.length / 8);
    items.forEach(function (it, i) {
      var x = band * i + (band - bw) / 2, bh = Math.max(1, c.h - ys(it.v));
      mark(S("rect", { x: x, y: c.h - bh, width: bw, height: bh, rx: 2, style: "fill:" + (it.c || o.c || "var(--s1)") }, g), it.l, [{ c: it.c || o.c || "var(--s1)", v: fv(it.v), n: o.vn || "" }]);
      if (i % every === 0) S("text", { class: "tk", x: band * i + band / 2, y: c.h + 15, "text-anchor": "middle", text: clip(it.l, 9) }, g);
    });
    S("line", { class: "ax", x1: 0, x2: c.w, y1: c.h, y2: c.h }, g);
    if (o.xl) S("text", { class: "tl", x: c.w / 2, y: c.h + 30, "text-anchor": "middle", text: o.xl }, g);
    done(el, c, null, { head: [o.th || "Bin", o.vh || "Count"], rows: items.map(function (i) { return [i.l, fv(i.v)]; }) });
  }

  /* line over ordered x labels: x [labels], series [{n, c, v:[..]}] */
  function line(el, x, series, o) {
    o = o || {}; if (x.length < 2) return empty(el, "Not enough points to draw a line.");
    var W = 480, H = o.h || 220, m = { l: 52, r: 14, t: 12, b: 32 }, c = frame(W, H, m, o.aria || "Line chart"), g = c.g, fv = o.fv || nf;
    var max = Math.max.apply(null, [].concat.apply([], series.map(function (s) { return s.v; }))) || 1;
    var xs = lin(0, x.length - 1, 0, c.w), ys = lin(0, max * 1.08, c.h, 0);
    [0, .5, 1].forEach(function (f) { var y = ys(max * f); S("line", { class: "gl", x1: 0, x2: c.w, y1: y, y2: y }, g); S("text", { class: "tk", x: -8, y: y + 3.5, "text-anchor": "end", text: nf(max * f) }, g); });
    S("line", { class: "ax", x1: 0, x2: c.w, y1: c.h, y2: c.h }, g);
    var every = Math.ceil(x.length / 6);
    x.forEach(function (l, i) { if (i % every === 0) S("text", { class: "tk", x: xs(i), y: c.h + 16, "text-anchor": "middle", text: l }, g); });
    series.forEach(function (s) {
      var d = s.v.map(function (v, i) { return (i ? "L" : "M") + xs(i) + "," + ys(v); }).join("");
      if (series.length === 1) S("path", { d: d + "L" + xs(x.length - 1) + "," + c.h + "L0," + c.h + "Z", style: "fill:" + s.c + ";opacity:.1" }, g);
      S("path", { d: d, style: "fill:none;stroke:" + s.c + ";stroke-width:2;stroke-linejoin:round;stroke-linecap:round" }, g);
    });
    var cg = S("g", { style: "display:none;pointer-events:none" }, g), cl = S("line", { y1: 0, y2: c.h, style: "stroke:var(--grey);stroke-width:1" }, cg);
    var cd = series.map(function (s) { return S("circle", { r: 4, style: "fill:" + s.c + ";stroke:var(--bg);stroke-width:2" }, cg); });
    x.forEach(function (l, i) {
      var xi = xs(i), a = i ? (xs(i - 1) + xi) / 2 : 0, b = i < x.length - 1 ? (xi + xs(i + 1)) / 2 : c.w;
      var hit = S("rect", { x: a, y: 0, width: Math.max(1, b - a), height: c.h, style: "fill:transparent" }, g);
      mark(hit, l, series.map(function (s) { return { c: s.c, v: fv(s.v[i]), n: s.n }; }));
      hit.__over = function () { cg.style.display = ""; cl.setAttribute("x1", xi); cl.setAttribute("x2", xi); cd.forEach(function (d, j) { d.setAttribute("cx", xi); d.setAttribute("cy", ys(series[j].v[i])); }); };
      hit.__out = function () { cg.style.display = "none"; };
    });
    done(el, c, series.length > 1 ? legend(series.map(function (s) { return { n: s.n, c: s.c, k: "ln" }; })) : null,
      { head: [o.th || "x"].concat(series.map(function (s) { return s.n; })), rows: x.map(function (l, i) { return [l].concat(series.map(function (s) { return fv(s.v[i]); })); }) });
  }

  window.RFC = { hbar: hbar, vbar: vbar, line: line, stackh: stackh, nf: nf, h: h, empty: empty };
})();
