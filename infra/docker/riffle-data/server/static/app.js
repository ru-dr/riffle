/* The pages' behaviour. Data is only ever put on the page with textContent. */
(function () {
  var C = window.RFC, h = C.h, nf = C.nf;
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var qs = new URLSearchParams(location.search);
  var LIVE_S = 25 * 60;

  function el(tag, cls, kids) {
    var e = h(tag, cls);
    [].concat(kids == null ? [] : kids).forEach(function (k) { if (k != null) e.append(k.nodeType ? k : document.createTextNode(String(k))); });
    return e;
  }
  function api(route, params) {
    var u = new URL("/api/" + route, location.origin);
    Object.keys(params || {}).forEach(function (k) { if (params[k] != null && params[k] !== "") u.searchParams.set(k, params[k]); });
    return fetch(u, { credentials: "same-origin" }).then(function (r) {
      if (r.status === 401) { location.href = "/login"; return new Promise(function () {}); }
      return r.json().then(function (j) { if (!r.ok) throw new Error(j.error || r.statusText); return j; });
    });
  }
  function bytes(n) { var u = ["B", "KB", "MB", "GB"], i = 0; while (n >= 1024 && i < 3) { n /= 1024; i++; } return (i ? n.toFixed(1) : n) + " " + u[i]; }
  function ago(ts) {
    if (!ts) return "never"; var s = Math.max(0, Math.floor(Date.now() / 1000 - ts));
    return s < 60 ? "just now" : s < 3600 ? Math.floor(s / 60) + " min ago" : s < 86400 ? Math.floor(s / 3600) + " h ago" : Math.floor(s / 86400) + " d ago";
  }
  function failure(node, e) { node.replaceChildren(el("div", "empty", "Could not load: " + e.message)); }
  function stat(label, value, hint, cls) {
    return el("div", "stat", [el("div", "cap", label), el("div", "v " + (cls || ""), value), hint ? el("div", "h", hint) : null]);
  }
  function runState(run) {
    var reason = run.status.reason, age = Date.now() / 1000 - run.updated;
    if (reason === "batch done") return ["done", "green"];
    if (reason === "stopped") return ["stopped", "amber"];
    return age < LIVE_S ? ["mining", "green"] : ["silent", "red"];
  }
  function tag(text, color) { return el("span", "tag " + (color || ""), text); }
  function cell(v, dash) {
    var td = h("td");
    if (v == null) { td.className = "nullc"; td.textContent = dash ? "–" : "null"; }
    else if (typeof v === "number") { td.className = "n"; td.textContent = Number.isInteger(v) ? v.toLocaleString() : String(+v.toFixed(4)); }
    else if (typeof v === "boolean") td.textContent = v ? "true" : "false";
    else {
      var t = String(v);
      if (t.length > 90 && /^[\[{]/.test(t)) { td.textContent = t.slice(0, 87) + "…"; td.title = t; }      // nested data: short, full text on hover
      else { td.textContent = t; if (t.length > 60) td.className = "wrapc"; }
    }
    return td;
  }
  /* A table. opts: sort {col, dir, on}; cell(v, i, row) for custom cells; dash shows null as "–"; hsm = columns hidden on phones;
     left = numeric-looking columns that should stay left; go(row) = a link the whole row opens; click(row, tr); tall = scrolls with a
     sticky header; sticky1 = the first column stays put when scrolling sideways. Columns of numbers get right-aligned headers. */
  function grid(cols, rows, opts) {
    opts = opts || {};
    var hide = opts.hsm || [], left = opts.left || [];
    var numeric = cols.map(function (_, i) {
      if (left.indexOf(i) >= 0) return false;
      var seen = false;
      for (var r = 0; r < rows.length; r++) { var v = rows[r][i]; if (v == null) continue; if (typeof v !== "number") return false; seen = true; }
      return seen;
    });
    var t = h("table", "t" + (opts.sticky1 ? " sticky1" : "")), hr = h("tr");
    cols.forEach(function (c, i) {
      var th = h("th", (opts.sort ? "sort" : "") + (numeric[i] ? " n" : "") + (hide.indexOf(i) >= 0 ? " hsm" : ""),
        c + (opts.sort && opts.sort.col === c ? (opts.sort.dir === "desc" ? " ↓" : " ↑") : ""));
      if (opts.sort) { th.tabIndex = 0; th.onclick = function () { opts.sort.on(c); }; th.onkeydown = function (e) { if (e.key === "Enter") opts.sort.on(c); }; }
      hr.append(th);
    });
    var head = h("thead"); head.append(hr); t.append(head);
    var body = h("tbody");
    rows.forEach(function (r) {
      var tr = h("tr"), href = opts.go ? opts.go(r) : null;
      if (href || opts.click) {
        tr.className = "go";
        tr.onclick = function (e) { if (e.target.closest("a,button")) return; if (opts.click) opts.click(r, tr); else location.href = href; };
      }
      cols.forEach(function (_c, i) {                                  // rows may carry extra hidden elements after the columns
        var v = r[i], td = opts.cell ? opts.cell(v, i, r) : cell(v, opts.dash);
        if (numeric[i] && !td.classList.contains("n") && !td.classList.contains("wrapc")) td.classList.add("n");
        if (hide.indexOf(i) >= 0) td.classList.add("hsm");
        tr.append(td);
      });
      body.append(tr);
    });
    t.append(body);
    var w = h("div", "tablewrap" + (opts.tall ? " tall" : "")); w.append(t);
    return rows.length ? w : el("div", "empty", opts.emptyText || "No rows.");
  }
  function link(text, href) { var a = h("a"); a.href = href; a.textContent = text; return a; }
  function viewHref(key) { return "/view?key=" + encodeURIComponent(key); }

  /* ---------------- overview ---------------- */
  var STATE_RANK = { mining: 0, silent: 1, stopped: 2, done: 3 };
  function overview() {
    function draw() {
      api("runs").then(function (runs) {
        var sum = function (k) { return runs.reduce(function (a, r) { return a + (r.status[k] || 0); }, 0); };
        var active = runs.filter(function (r) { return runState(r)[0] === "mining"; }).length;
        $("#kpis").replaceChildren(
          stat("PCs", runs.length, active + " mining now", active ? "good" : ""),
          stat("Repos done", sum("repos_done").toLocaleString(), "of " + sum("repos_total").toLocaleString() + " assigned"),
          stat("PRs mined", sum("prs_done").toLocaleString(), "rows written so far"),
          stat("PRs left", sum("prs_remaining").toLocaleString(), "in repos already started"),
          stat("Failed repos", sum("repos_failed").toLocaleString(), sum("repos_pending") + " not started", sum("repos_failed") ? "bad" : ""));
        runs.sort(function (a, b) { return (STATE_RANK[runState(a)[0]] - STATE_RANK[runState(b)[0]]) || a.pc.localeCompare(b.pc); });
        C.hbar($("#c-prs"), runs.map(function (r) { return { l: r.pc, v: r.status.prs_done || 0 }; }).sort(function (a, b) { return b.v - a.v; }),
          { aria: "PRs mined per PC", th: "PC", vh: "PRs mined", ml: 150 });
        C.stackh($("#c-repos"), runs.map(function (r) {
          var s = r.status, done = s.repos_done || 0, failed = s.repos_failed || 0, wait = (s.repos_pending || 0) + (s.repos_skipped || 0);
          return { l: r.pc, vals: [done, Math.max(0, (s.repos_total || 0) - done - failed - wait), wait, failed] };
        }), [{ n: "done", c: "var(--s1)" }, { n: "mining", c: "var(--s2)" }, { n: "waiting", c: "var(--dim)" }, { n: "failed", c: "var(--t-red)" }], { aria: "Repos by state per PC", ml: 150, th: "PC" });
        var cols = ["PC", "State", "Progress", "Now mining", "PRs mined", "PRs left", "Waiting", "Failed", "Last sync"];
        var rows = runs.map(function (r) {
          var s = r.status; return [r.pc, runState(r)[0], s.repos_done || 0, s.current || null, s.prs_done || 0, s.prs_remaining || 0, s.repos_pending || 0, s.repos_failed || 0, r.updated, r];
        });
        $("#pcs").replaceChildren(grid(cols, rows, {
          hsm: [2, 3, 5, 6, 7, 8], left: [2, 8],
          go: function (row) { var r = row[9]; return "/run?pc=" + encodeURIComponent(r.pc) + "&prefix=" + encodeURIComponent(r.prefix); },
          emptyText: "No PC has synced yet. Plan a split from the miner's TUI (Lead tools, F3), send each PC its join code, and it appears here as soon as it uploads.",
          cell: function (v, i, row) {
            var r = row[9], s = r.status, td = h("td");
            if (i === 0) td.append(el("div", "", link(r.pc, "/run?pc=" + encodeURIComponent(r.pc) + "&prefix=" + encodeURIComponent(r.prefix))), el("div", "cap", [(s.host || ""), el("span", "hsm", " · miner " + (s.miner_version || "?"))]));
            else if (i === 1) { var st = runState(r); td.append(tag(st[0], st[1])); }
            else if (i === 2) { var pct = s.repos_total ? Math.round(100 * (s.repos_done || 0) / s.repos_total) : 0, bar = el("span", "bar"), fill = h("i"); fill.style.width = pct + "%"; bar.append(fill); td.classList.add("prog"); td.append((s.repos_done || 0) + "/" + (s.repos_total || 0) + " repos", bar); }
            else if (i === 3) td.textContent = v || "–";
            else if (i === 8) td.textContent = ago(v);
            else { td.className = "n"; td.textContent = (v || 0).toLocaleString(); }
            return td;
          }
        }));
      }).catch(function (e) { failure($("#pcs"), e); });
    }
    draw(); setInterval(draw, 30000);
  }

  /* ---------------- one run ---------------- */
  var REPO_RANK = { mining: 0, failed: 1, pending: 2, skipped: 3, done: 4 };
  function run() {
    var pc = qs.get("pc"), prefix = qs.get("prefix"), filter = "all";
    function load() { api("run", { pc: pc, prefix: prefix }).then(function (d) {
      var r = d.run, s = r.status, st = runState(r);
      $("#r-cap").textContent = "Run · split " + (s.split_id || "none") + " · miner " + (s.miner_version || "?");
      $("#r-title").replaceChildren(document.createTextNode(r.pc + " "), tag(st[0], st[1]));
      $("#r-sub").textContent = "Last sync " + ago(r.updated) + " (" + s.reason + ") from " + (s.host || "unknown host") + ".";
      $("#kpis").replaceChildren(
        stat("Repos", (s.repos_done || 0) + "/" + (s.repos_total || 0), (s.repos_pending || 0) + " waiting"),
        stat("PRs mined", (s.prs_done || 0).toLocaleString(), "rows written"),
        stat("PRs left", (s.prs_remaining || 0).toLocaleString(), s.current ? "in " + s.current : "no repo in progress"),
        stat("Failed", String(s.repos_failed || 0), (s.repos_skipped || 0) + " skipped", s.repos_failed ? "bad" : ""),
        stat("Files", String(d.files.length), bytes(d.files.reduce(function (a, f) { return a + f.size; }, 0))));
      var repos = Object.keys(s.repos || {});
      C.hbar($("#c-repos"), repos.filter(function (k) { return (s.repos[k].rows || 0) > 0; }).map(function (k) {
        return { l: k, v: s.repos[k].rows || 0, n: s.repos[k].selected != null ? "of " + s.repos[k].selected + " selected" : "" };
      }).sort(function (a, b) { return b.v - a.v; }).slice(0, 20), { aria: "PRs mined per repo", ml: 170, th: "Repo", vh: "PRs mined" });
      var flagged = repos.map(function (k) { return { l: k, v: ((d.health[k] || {}).flags || []).length }; }).filter(function (x) { return x.v > 0; });
      if (flagged.length) C.hbar($("#c-health"), flagged, { aria: "Health flags per repo", ml: 170, c: "var(--s2)", th: "Repo", vh: "Flags" });
      else C.empty($("#c-health"), Object.keys(d.health).length ? "No repo has a health flag." : "No health files yet.");
      var all = repos.map(function (k) {
        var x = s.repos[k], hh = d.health[k], left = x.state === "mining" && x.selected != null ? Math.max(0, x.selected - (x.rows || 0)) : null;
        return [k, x.state, x.rows == null ? null : x.rows, x.selected == null ? null : x.selected, left, hh ? (hh.flags || []).join("; ") || "ok" : null];
      }).sort(function (a, b) { return (REPO_RANK[a[1]] - REPO_RANK[b[1]]) || a[0].localeCompare(b[0]); });
      var seg = el("div", "seg"), host = el("div"), box = $("#repos");
      function drawRepos() {
        var rows = all.filter(function (x) { return filter === "all" || x[1] === filter; });
        host.replaceChildren(grid(["Repo", "State", "PRs mined", "Selected", "Left", "Health"], rows, {
          dash: true, hsm: [3, 4], left: [5], emptyText: "No repo in this state.",
          cell: function (v, i) {
            var td = cell(v, true);
            if (i === 1) { td = h("td"); td.append(tag(v, v === "done" ? "green" : v === "failed" ? "red" : v === "mining" ? "amber" : "")); }
            if (i === 5) { td.className = "wrapc"; if (v && v !== "ok") td.style.color = "var(--t-amber)"; if (v == null) { td.className = "nullc"; td.textContent = "–"; } }
            return td;
          }
        }));
      }
      var counts = {}; all.forEach(function (x) { counts[x[1]] = (counts[x[1]] || 0) + 1; });
      ["all", "mining", "failed", "pending", "skipped", "done"].filter(function (k) { return k === "all" || counts[k]; }).forEach(function (k) {
        var b = el("button", "", [k === "all" ? "All" : k[0].toUpperCase() + k.slice(1), el("span", "count", String(k === "all" ? all.length : counts[k]))]);
        b.setAttribute("aria-pressed", String(k === filter)); b.onclick = function () { filter = k; [].forEach.call(seg.children, function (c) { c.setAttribute("aria-pressed", "false"); }); b.setAttribute("aria-pressed", "true"); drawRepos(); };
        seg.append(b);
      });
      var wrap = el("div", "panel"); wrap.style.paddingBottom = "0"; wrap.append(seg); box.replaceChildren(wrap, host); drawRepos();
      var files = d.files.map(function (f) { return [f.key.split("/").slice(3).join("/"), f.kind, f.size, f.updated, f]; });
      var hasUri = d.files.some(function (f) { return f.uri; });
      $("#files").replaceChildren(grid(hasUri ? ["File", "Type", "Size", "Updated", "Bucket"] : ["File", "Type", "Size", "Updated"], files, {
        hsm: [1, 3, 4], left: [3],
        go: function (row) { return viewHref(row[4].key); },
        cell: function (v, i, row) {
          var td = h("td");
          if (i === 0) td.append(link(v, viewHref(row[4].key)));
          else if (i === 2) { td.className = "n"; td.textContent = bytes(v); }
          else if (i === 3) td.textContent = ago(v);
          else if (i === 4) { td.className = "cap"; td.style.textTransform = "none"; td.textContent = row[4].uri || ""; }
          else td.textContent = v;
          return td;
        }
      }));
    }).catch(function (e) { $("#r-title").textContent = "Run not found"; $("#r-sub").textContent = e.message; }); }
    load(); setInterval(load, 30000);                                   // follow a PC that is mining
  }

  /* ---------------- parquet explorer ---------------- */
  function explorer(root, src, opts) {
    opts = opts || {};
    var tabs = el("div", "tabs"), body = el("div"), panes = {}, state = { sort: null, dir: "asc", offset: 0, limit: 50, fcol: "", fq: "" }, nCols = 0;
    var tabNames = [["charts", "Charts"], ["table", "Table"], ["schema", "Columns"]];
    function open(name) {
      tabNames.forEach(function (t) { panes[t[0]].hidden = t[0] !== name; });
      [].forEach.call(tabs.children, function (b) { b.setAttribute("aria-pressed", String(b.dataset.t === name)); });
      if (!panes[name].dataset.loaded) { panes[name].dataset.loaded = "1"; loaders[name](); }
    }
    tabNames.forEach(function (t) {
      var b = el("button", "", t[1]); b.dataset.t = t[0]; b.onclick = function () { open(t[0]); }; tabs.append(b);
      panes[t[0]] = el("div"); panes[t[0]].hidden = true; body.append(panes[t[0]]);
    });
    var head = el("div", "section"); head.append(tabs); var meta = el("div", "cap"); meta.style.marginTop = "12px"; head.append(meta);
    root.replaceChildren(head, body);
    var params = function (extra) { return Object.assign({}, src, extra || {}); };

    var loaders = {
      charts: function () {
        var p = panes.charts; p.replaceChildren(el("div", "empty", "Reading the file…"));
        api("parquet/charts", params()).then(function (c) {
          var box = el("section", "rule grid g2 stack"); p.replaceChildren(box);
          function panel(cap, title, note) { var d = el("div", "panel", [el("div", "cap", cap), el("h3", "", title)]); var ch = el("div", "chart"); d.append(ch); if (note) d.append(el("p", "note", note)); box.append(d); return ch; }
          if (c.repos && c.repos.length) C.hbar(panel("Rows", "PRs per repo (top 15)"), c.repos, { ml: 170, aria: "PRs per repo", th: "Repo", vh: "Rows" });
          if (c.months && c.months.items.length > 1) C.line(panel("Time", "Rows per month, by " + c.months.column), c.months.items.map(function (i) { return i.l; }), [{ n: "rows", c: "var(--s1)", v: c.months.items.map(function (i) { return i.v; }) }], { aria: "Rows per month", th: "Month" });
          if (c.labels && c.labels.length) C.stackh(panel("Labels", "Outcome labels", "Unobserved stays null. It is never counted as a pass."), c.labels, [{ n: "true", c: "var(--s2)" }, { n: "false", c: "var(--s1)" }, { n: "unobserved (null)", c: "var(--dim)" }], { ml: 150, aria: "Label balance", th: "Label" });
          if (c.nulls && c.nulls.length) C.hbar(panel("Quality", "Null share by column (top 12)"), c.nulls.map(function (i) { return { l: i.l, v: i.v }; }), { ml: 230, max: 100, fv: function (v) { return v.toFixed(1) + "%"; }, c: "var(--s3)", aria: "Null share per column", th: "Column", vh: "Null share" });
          if (c.files && c.files.length > 1) C.hbar(panel("Source", "Rows per file"), c.files, { ml: 170, aria: "Rows per file", th: "File", vh: "Rows" });
          if (!box.children.length) { p.replaceChildren(el("div", "empty", "No default charts for this file. Open Columns and chart any column.")); return; }
          if (box.children.length % 2) {                                         // an odd count would leave a hole: use it
            var f = el("div", "panel filler", [el("div", "cap", "Explore"), el("h3", "", "Look at the rows themselves"),
              el("p", "", "Sort and filter the " + (nCols || "") + " columns in Table, or open Columns to search them and chart any one."), el("div", "row")]);
            [["Open the table", "table"], ["Search the columns", "schema"]].forEach(function (x) { var b = el("button", "btn", x[0]); b.onclick = function () { open(x[1]); }; $(".row", f).append(b); });
            box.append(f);
          }
        }).catch(function (e) { failure(p, e); });
      },
      table: function () { drawTable(); },
      schema: function () {
        var p = panes.schema; p.replaceChildren(el("div", "empty", "Reading the file…"));
        api("parquet/schema", params()).then(function (sc) {
          var cols = sc.columns.filter(function (c) { return c.name !== "filename"; }), sel = null, q = "", onlyNulls = false;
          var left = el("div"), side = el("div", "side panel"), prof = el("div", "chart"), listHost = el("div");
          side.append(el("div", "cap", "Column chart"), el("h3", "", "Click a row to chart it"), prof);
          var tools = el("div", "tools"); tools.style.padding = "16px 24px 0";
          var inp = h("input"); inp.type = "search"; inp.placeholder = "Search " + cols.length + " columns…"; inp.setAttribute("aria-label", "Search columns"); inp.size = 30;
          var lab = el("label", "check"), cb = h("input"); cb.type = "checkbox"; lab.append(cb, "only columns with nulls");
          var cnt = el("span", "cap"); tools.append(inp, lab, cnt); left.append(tools, listHost);
          function draw() {
            var rows = cols.filter(function (c) { return (!q || c.name.toLowerCase().indexOf(q) >= 0) && (!onlyNulls || (c.null_pct || 0) > 0); })
              .map(function (c) { return [c.name, c.type, c.null_pct, c.distinct, c.min, c.max]; });
            cnt.textContent = rows.length + " of " + cols.length;
            listHost.replaceChildren(grid(["Column", "Type", "Null %", "Distinct≈", "Min", "Max"], rows, {
              tall: true, hsm: [3, 4, 5], emptyText: "No column matches.",
              click: function (row, tr) { pick(row[0]); [].forEach.call(listHost.querySelectorAll("tr.sel"), function (x) { x.classList.remove("sel"); }); tr.classList.add("sel"); },
              cell: function (v, i) {
                if (i === 2) { var td = h("td", "n", v == null ? "–" : (+v).toFixed(1) + "%"); if (v > 50) td.style.color = "var(--t-amber)"; return td; }
                if (i === 0) { var t0 = h("td"); t0.append(el("span", "mono", v)); return t0; }
                var c0 = cell(v, true); if (i >= 4 && v != null) { c0.classList.add("clip"); c0.title = String(v); } return c0;
              }
            }));
            if (sel) [].forEach.call(listHost.querySelectorAll("tbody tr"), function (tr) { if (tr.firstChild.textContent === sel) tr.classList.add("sel"); });
          }
          function pick(name) { sel = name; profile(name, prof); }
          inp.oninput = function () { q = inp.value.trim().toLowerCase(); draw(); }; cb.onchange = function () { onlyNulls = cb.checked; draw(); };
          var ex = el("div", "explore"); ex.append(left, side); p.replaceChildren(ex); draw();
          if (cols[0]) { pick(cols[0].name); }
        }).catch(function (e) { failure(p, e); });
      }
    };
    function profile(col, box) {
      if (!col) return; box.replaceChildren(el("div", "empty", "Reading " + col + "…"));
      api("parquet/profile", params({ col: col })).then(function (d) {
        var wrap = el("div"); wrap.append(el("div", "cap", d.column + " · " + d.type), el("div", "note", d.nulls.toLocaleString() + " null of " + d.rows.toLocaleString() + " rows"));
        wrap.lastChild.style.margin = "4px 0 8px"; wrap.lastChild.style.font = "13px var(--sans)"; wrap.lastChild.style.color = "var(--nickel)";
        var ch = el("div", "chart"); wrap.append(ch); box.replaceChildren(wrap);
        if (d.kind === "histogram") C.vbar(ch, d.items, { aria: "Histogram of " + col, xl: col, th: "From", vh: "Rows" });
        else if (d.kind === "months") C.line(ch, d.items.map(function (i) { return i.l; }), [{ n: "rows", c: "var(--s1)", v: d.items.map(function (i) { return i.v; }) }], { aria: "Rows per month", th: "Month" });
        else C.hbar(ch, d.items, { ml: 170, aria: "Most common values of " + col, th: "Value", vh: "Rows" });
      }).catch(function (e) { failure(box, e); });
    }
    function drawTable() {
      var p = panes.table;
      api("parquet/rows", params({ offset: state.offset, limit: state.limit, sort: state.sort, dir: state.dir, fcol: state.fcol, fq: state.fq })).then(function (d) {
        var tools = el("div", "toolbar"); tools.style.padding = "16px 24px 0";
        var g1 = el("div", "grp", [el("span", "cap", "Filter")]), row1 = el("div", "tools"); row1.style.margin = "0";
        var sel = h("select"); sel.setAttribute("aria-label", "Filter column");
        sel.append(new Option("any column…", "")); d.columns.forEach(function (c) { sel.append(new Option(c, c)); }); sel.value = state.fcol;
        var inp = h("input"); inp.type = "search"; inp.placeholder = "contains…"; inp.value = state.fq; inp.setAttribute("aria-label", "Filter text");
        var go = el("button", "btn solid", "Apply"), clear = el("button", "btn", "Clear");
        function apply() { if (inp.value && !sel.value) { sel.focus(); return; } state.fcol = sel.value; state.fq = inp.value; state.offset = 0; drawTable(); }
        go.onclick = apply; inp.onkeydown = function (e) { if (e.key === "Enter") apply(); };
        clear.onclick = function () { state.fcol = state.fq = ""; state.offset = 0; drawTable(); }; clear.hidden = !(state.fcol || state.fq);
        row1.append(sel, inp, go, clear); g1.append(row1);
        var g2 = el("div", "grp", [el("span", "cap", "Rows per page")]), size = h("select"); size.setAttribute("aria-label", "Rows per page");
        [25, 50, 100, 200].forEach(function (n) { size.append(new Option(String(n), n)); }); size.value = state.limit;
        size.onchange = function () { state.limit = +size.value; state.offset = 0; drawTable(); }; g2.append(size);
        tools.append(g1, g2);
        var from = d.total ? d.offset + 1 : 0, to = Math.min(d.total, d.offset + d.limit);
        var prev = el("button", "btn", "← Prev"), next = el("button", "btn", "Next →");
        prev.disabled = d.offset <= 0; next.disabled = to >= d.total;
        prev.onclick = function () { state.offset = Math.max(0, d.offset - d.limit); drawTable(); };
        next.onclick = function () { state.offset = d.offset + d.limit; drawTable(); };
        var pager = el("div", "pager", [prev, from.toLocaleString() + "–" + to.toLocaleString() + " of " + d.total.toLocaleString() + (state.fq ? " matching" : ""), next]); pager.style.padding = "0 24px";
        nCols = d.columns.length;
        var hint = el("div", "hint", d.columns.length + " columns: scroll sideways, click a header to sort. Empty cells are null, not zero.");
        var t = grid(d.columns, d.rows, { tall: true, sticky1: true, emptyText: "No rows match.",
          sort: { col: state.sort, dir: state.dir, on: function (c) { state.dir = state.sort === c && state.dir === "asc" ? "desc" : "asc"; state.sort = c; state.offset = 0; drawTable(); } } });
        p.replaceChildren(tools, pager, hint, t);
      }).catch(function (e) { failure(p, e); });
    }
    api("parquet/schema", params()).then(function (s) {
      nCols = s.columns.length; meta.textContent = s.rows.toLocaleString() + " rows · " + s.columns.length + " columns · " + s.files + " file" + (s.files === 1 ? "" : "s");
      if (opts.onSchema) opts.onSchema(s);
    }).catch(function (e) { meta.textContent = e.message; });
    open("charts");
  }

  /* ---------------- data page ---------------- */
  function data() {
    var pick = $("#pickers"), version = qs.get("version"), split = qs.get("split"), table = qs.get("table") || "mined_dataset", all = qs.get("all") === "1";
    function go() { var u = new URLSearchParams({ version: version, split: split, table: table }); if (all) u.set("all", "1"); location.search = u.toString(); }
    function group(label, node) { return el("div", "grp", [el("span", "cap", label), node]); }
    api("tables", { version: version, split: split }).then(function (c) {
      if (!c.tables.length) { $("#explorer").replaceChildren(el("div", "empty", "No parquet tables have been uploaded yet. A PC needs pandas and pyarrow to write them; when its first repo finishes, the tables appear here.")); return; }
      version = c.version; split = c.split;
      var vs = h("select"), ss = h("select"), ts = el("div", "tabs");
      vs.setAttribute("aria-label", "Miner version"); ss.setAttribute("aria-label", "Split");
      c.versions.forEach(function (v) { vs.append(new Option(v, v)); }); vs.value = version; vs.onchange = function () { version = vs.value; split = null; go(); };
      c.splits.forEach(function (v) { ss.append(new Option(v, v)); }); ss.value = split; ss.onchange = function () { split = ss.value; go(); };
      c.tables.forEach(function (t) {
        var n = all ? t.all : t.finished, b = el("button", "", [t.name, el("span", "count", n + (n === 1 ? " file" : " files"))]);
        b.setAttribute("aria-pressed", String(t.name === table)); b.disabled = !n && t.name !== table; if (!n) b.title = "No " + (all ? "" : "finished PC has this ") + "file yet";
        b.onclick = function () { table = t.name; go(); }; ts.append(b);
      });
      var lab = el("label", "check"), cb = h("input"); cb.type = "checkbox"; cb.checked = all; cb.onchange = function () { all = cb.checked; go(); };
      lab.append(cb, "Include PCs that are still mining");
      pick.className = "toolbar"; pick.replaceChildren(group("Miner version", vs), group("Split", ss), group("Table", ts), group("Which PCs", lab));
      var cur = c.tables.filter(function (t) { return t.name === table; })[0];
      if (!cur || !(all ? cur.all : cur.finished)) { $("#explorer").replaceChildren(el("div", "empty", "No " + (all ? "" : "finished PC has this ") + "file for this table yet. " + (all ? "" : "Tick “Include PCs that are still mining” to see partial data."))); return; }
      explorer($("#explorer"), { table: table, version: version, split: split, all: all ? "1" : "" });
    }).catch(function (e) { failure($("#explorer"), e); });
  }

  /* ---------------- file viewer ---------------- */
  function tree(v, key) {
    if (v !== null && typeof v === "object") {
      var arr = Array.isArray(v), keys = arr ? v.map(function (_, i) { return i; }) : Object.keys(v);
      var d = h("details"); if (key == null || keys.length <= 12) d.open = true;
      var s = h("summary"); if (key != null) { var k = h("span", "k", String(key)); s.append(k, document.createTextNode(": ")); }
      s.append(document.createTextNode(arr ? "[" + keys.length + "]" : "{" + keys.length + "}")); d.append(s);
      keys.forEach(function (kk) { d.append(tree(v[kk], kk)); });
      return d;
    }
    var line = h("span", "leaf");
    if (key != null) { line.append(h("span", "k", String(key)), document.createTextNode(": ")); }
    var val = h("span", v === null ? "nl" : typeof v === "string" ? "s" : "nm", v === null ? "null" : typeof v === "string" ? JSON.stringify(v) : String(v));
    line.append(val); return line;
  }
  function textViewer(root, key, kind) {
    var st = { offset: 0, limit: 200, q: "", tail: false, lvl: false };
    var box = el("div"); root.replaceChildren(box);
    function draw() {
      api("lines", { key: key, offset: st.offset, limit: st.limit, q: st.q, tail: st.tail ? "1" : "", lvl: st.lvl ? "1" : "" }).then(function (d) {
        var tools = el("div", "toolbar"); tools.style.padding = "16px 24px 0";
        var g1 = el("div", "grp", [el("span", "cap", "Find in this file")]), r1 = el("div", "tools"); r1.style.margin = "0";
        var inp = h("input"); inp.type = "search"; inp.placeholder = "text…"; inp.value = st.q; inp.setAttribute("aria-label", "Filter lines");
        function apply() { st.q = inp.value; st.offset = 0; draw(); }
        inp.onkeydown = function (e) { if (e.key === "Enter") apply(); };
        var go = el("button", "btn solid", "Find"); go.onclick = apply; r1.append(inp, go); g1.append(r1);
        var g2 = el("div", "grp", [el("span", "cap", "Show")]), seg = el("div", "seg"); seg.style.margin = "0";
        [["All lines", false], ["Warnings and errors", true]].forEach(function (x) {
          var b = el("button", "", x[0]); b.setAttribute("aria-pressed", String(st.lvl === x[1])); b.onclick = function () { st.lvl = x[1]; st.offset = 0; draw(); }; seg.append(b);
        });
        var tl = el("button", "btn", st.tail ? "From the start" : "Jump to the end"); tl.onclick = function () { st.tail = !st.tail; st.offset = 0; draw(); };
        seg.append(tl); g2.append(seg); tools.append(g1, g2);
        var pager = el("div", "pager"); pager.style.padding = "0 24px";
        if (!st.tail) {
          var prev = el("button", "btn", "← Prev"), next = el("button", "btn", "Next →"), to = Math.min(d.total, d.offset + d.limit);
          prev.disabled = d.offset <= 0; next.disabled = to >= d.total;
          prev.onclick = function () { st.offset = Math.max(0, d.offset - d.limit); draw(); }; next.onclick = function () { st.offset = d.offset + d.limit; draw(); };
          pager.append(prev, (d.total ? d.offset + 1 : 0).toLocaleString() + "–" + to.toLocaleString() + " of " + d.total.toLocaleString() + " lines", next);
        } else pager.append("last " + d.lines.length + " of " + d.total.toLocaleString() + " lines");
        var pre = h("pre", "log"); var needle = st.q.toLowerCase();
        d.lines.forEach(function (l) {
          var row = h("span"), txt = l[1];
          if (/\b(ERROR|Traceback)\b|\[error/i.test(txt)) row.className = "e"; else if (/\b(WARN|WARNING)\b|\[warn/i.test(txt)) row.className = "w";
          row.append(h("span", "ln", String(l[0])));
          var i = needle ? txt.toLowerCase().indexOf(needle) : -1;
          if (i >= 0) { row.append(document.createTextNode(txt.slice(0, i)), el("mark", "", txt.slice(i, i + needle.length)), document.createTextNode(txt.slice(i + needle.length))); }
          else row.append(document.createTextNode(txt));
          pre.append(row, document.createTextNode("\n"));
        });
        box.replaceChildren(tools, pager, d.lines.length ? pre : el("div", "empty", st.lvl ? "No warnings or errors in this file." : "No lines."));
      }).catch(function (e) { failure(box, e); });
    }
    draw();
  }
  function jsonlViewer(root, key) {
    var st = { offset: 0, limit: 50, q: "" }, box = el("div"); root.replaceChildren(box);
    function draw() {
      api("jsonl", { key: key, offset: st.offset, limit: st.limit, q: st.q }).then(function (d) {
        var tools = el("div", "toolbar"); tools.style.padding = "16px 24px 0";
        var g = el("div", "grp", [el("span", "cap", "Find in these records")]), r1 = el("div", "tools"); r1.style.margin = "0";
        var inp = h("input"); inp.type = "search"; inp.placeholder = "text…"; inp.value = st.q; inp.setAttribute("aria-label", "Filter records");
        function apply() { st.q = inp.value; st.offset = 0; draw(); }
        inp.onkeydown = function (e) { if (e.key === "Enter") apply(); };
        var go = el("button", "btn solid", "Find"); go.onclick = apply; r1.append(inp, go); g.append(r1); tools.append(g);
        var prev = el("button", "btn", "← Prev"), next = el("button", "btn", "Next →"), to = Math.min(d.total, d.offset + d.limit);
        prev.disabled = d.offset <= 0; next.disabled = to >= d.total;
        prev.onclick = function () { st.offset = Math.max(0, d.offset - d.limit); draw(); }; next.onclick = function () { st.offset = d.offset + d.limit; draw(); };
        var pager = el("div", "pager", [prev, (d.total ? d.offset + 1 : 0) + "–" + to + " of " + d.total.toLocaleString() + " records", next]); pager.style.padding = "0 24px";
        var keep = d.columns.map(function (_, i) { return d.rows.some(function (r) { return r[i] != null; }); });      // a column nobody filled is noise
        var cols = d.columns.filter(function (_, i) { return keep[i]; }), rows = d.rows.map(function (r) { return r.filter(function (_, i) { return keep[i]; }); });
        box.replaceChildren(tools, pager, grid(cols, rows, { dash: true, tall: true, emptyText: "No records match." }));
      }).catch(function (e) { failure(box, e); });
    }
    draw();
  }
  function traceViewer(root, key) {
    root.replaceChildren(el("div", "empty", "Reading the trace…"));
    api("trace", { key: key }).then(function (t) {
      var top = el("section", "rule grid g5");
      top.append(stat("Events", t.events.toLocaleString(), Object.keys(t.levels).map(function (k) { return k + " " + t.levels[k]; }).join(" · ")),
        stat("PRs", t.prs.toLocaleString(), "finished in this trace"), stat("Warnings", String(t.levels.WARN || 0), "", t.levels.WARN ? "warn" : ""),
        stat("Errors", String(t.levels.ERROR || 0), "", t.levels.ERROR ? "bad" : ""),
        stat("Slowest PR", t.slowest.length ? t.slowest[0].dur_s + " s" : "–", t.slowest.length ? t.slowest[0].repo + " #" + t.slowest[0].pr : ""));
      var charts = el("section", "rule grid g2 stack");
      function panel(cap, title, note) { var d = el("div", "panel", [el("div", "cap", cap), el("h3", "", title)]); var c = el("div", "chart"); d.append(c); if (note) d.append(el("p", "note", note)); charts.append(d); return c; }
      C.hbar(panel("Where the time went", "Seconds per PR stage", "Stages nest, so they add up to more than the wall time."), t.pr_stage_seconds, { ml: 150, fv: function (v) { return nf(v) + " s"; }, aria: "Seconds per stage", th: "Stage", vh: "Seconds" });
      if (t.pr_durations.length > 1) C.line(panel("Pace", "Seconds per PR, in order"), t.pr_durations.map(function (_, i) { return String(i + 1); }), [{ n: "seconds", c: "var(--s2)", v: t.pr_durations }], { aria: "Seconds per PR", th: "PR #", fv: function (v) { return v + " s"; } });
      else C.empty(panel("Pace", "Seconds per PR, in order"), "Fewer than two PRs in this trace.");
      if (t.run_stage_seconds.length) C.hbar(panel("Repo stages", "Seconds per repo stage"), t.run_stage_seconds, { ml: 150, fv: function (v) { return nf(v) + " s"; }, c: "var(--s3)", aria: "Seconds per repo stage", th: "Stage", vh: "Seconds" });
      var wrap = el("div"); var probs = el("section", "section");
      probs.append(el("div", "shead", [el("div", "cap", "Needs a look"), el("h2", "", t.problems.length ? t.problems.length + " warnings and errors" : "No warnings or errors")]));
      var pt = t.problems.length ? grid(["Level", "Stage", "Repo", "PR", "Message"], t.problems.map(function (p) { return [p.lvl, p.stage, p.repo, p.pr, p.msg]; }), { dash: true, hsm: [1, 3],
        cell: function (v, i) { if (i === 0) { var td = h("td"); td.append(tag(v, v === "ERROR" ? "red" : "amber")); return td; } return cell(v, true); } }) : null;
      var ev = el("div"); var evh = el("section", "section"); evh.append(el("div", "shead", [el("div", "cap", "Events"), el("h2", "", "Every event, filterable")]));
      wrap.append(top, charts, probs); if (pt) wrap.append(pt); wrap.append(evh, ev);
      root.replaceChildren(wrap); jsonlViewer(ev, key);
    }).catch(function (e) { failure(root, e); });
  }
  function jsonViewer(root, key) {
    api("json", { key: key }).then(function (doc) {
      var box = el("div"), body = el("div"); root.replaceChildren(box);
      var tabs = el("div", "tabs"), tb = el("button", "", "Tree"), rb = el("button", "", "Raw"); tabs.append(tb, rb);
      var head = el("div", "section"); head.append(tabs);
      function showTree() { tb.setAttribute("aria-pressed", "true"); rb.setAttribute("aria-pressed", "false"); var j = el("div", "json"); j.append(tree(doc)); body.replaceChildren(smart(doc), j); }
      function showRaw() { rb.setAttribute("aria-pressed", "true"); tb.setAttribute("aria-pressed", "false"); var pre = h("pre", "log"); pre.textContent = JSON.stringify(doc, null, 2); body.replaceChildren(pre); }
      tb.onclick = showTree; rb.onclick = showRaw; box.append(head, body); showTree();
    }).catch(function (e) { failure(root, e); });
  }
  /* run.json and health files get a summary above the tree */
  function smart(doc) {
    var out = el("div"), s = doc && doc.stats;
    if (s && doc.repo) {
      var g = el("section", "rule grid g5");
      [["Rows", s.rows], ["Merged share", s.merged_share], ["Outside share", s.outside_share], ["Bot share", s.bot_share], ["CI unobserved", s.ci_unobserved_share]].forEach(function (kv) {
        g.append(stat(kv[0], kv[1] == null ? "null" : (typeof kv[1] === "number" && kv[1] <= 1 && kv[0] !== "Rows" ? (kv[1] * 100).toFixed(1) + "%" : String(kv[1])), ""));
      });
      out.append(g);
      var fl = (doc.flags || []); out.append(el("div", "section", [el("div", "cap", "Health flags"), fl.length ? el("p", "", fl.join(" · ")) : el("p", "", "None. This repo looks healthy.")]));
    } else if (doc && doc.repos && doc.reason) {
      var g2 = el("section", "rule grid g5");
      g2.append(stat("Repos", (doc.repos_done || 0) + "/" + (doc.repos_total || 0), ""), stat("PRs mined", (doc.prs_done || 0).toLocaleString(), ""),
        stat("PRs left", (doc.prs_remaining || 0).toLocaleString(), doc.current || ""), stat("Reason", doc.reason, ""), stat("Host", doc.host || "?", ""));
      out.append(g2);
    }
    return out;
  }
  function view() {
    var key = qs.get("key"), root = $("#viewer");
    api("meta", { key: key }).then(function (m) {
      var parts = key.split("/");
      $("#v-cap").textContent = m.kind + " · " + bytes(m.size) + " · updated " + ago(m.updated);
      $("#v-title").textContent = parts.slice(3).join("/") || key;
      $("#v-sub").textContent = parts[0] + " · miner " + parts[1] + " · split " + parts[2] + (m.uri ? " · " + m.uri : "");
      if (m.kind === "parquet") explorer(root, { key: key });
      else if (m.kind === "trace") traceViewer(root, key);
      else if (m.kind === "jsonl") jsonlViewer(root, key);
      else if (m.kind === "json") jsonViewer(root, key);
      else if (m.kind === "text") textViewer(root, key);
      else root.replaceChildren(el("div", "empty", "This file type has no viewer. It is in the store at " + (m.uri || key) + "."));
    }).catch(function (e) { $("#v-title").textContent = "Not available"; $("#v-sub").textContent = e.message; });
  }

  /* ---------------- search ---------------- */
  function search() {
    var f = $("#sform"); f.q.value = qs.get("q") || ""; f.regex.checked = qs.get("regex") === "1";
    api("search", { q: qs.get("q"), pc: qs.get("pc"), regex: qs.get("regex") === "1" ? "1" : "" }).then(function (d) {
      var sel = f.pc; sel.append(new Option("All PCs", "")); d.pcs.forEach(function (p) { sel.append(new Option(p, p)); }); sel.value = qs.get("pc") || "";
      $("#note").textContent = d.note || "";
      if (!qs.get("q")) { $("#hits").replaceChildren(el("div", "empty", "Type a word, such as warn, rate limited or a repo name, and press Search.")); return; }
      var rows = d.hits.map(function (x) { return [x.key.split("/")[0], x.key.split("/").slice(3).join("/"), x.line, x.text, x.key]; });
      var needle = qs.get("q"), isRe = qs.get("regex") === "1", re = null;
      try { re = new RegExp(isRe ? needle : needle.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "i"); } catch (e) { re = null; }
      $("#note").textContent = (d.note ? d.note + " " : "") + d.hits.length + " match" + (d.hits.length === 1 ? "" : "es");
      $("#hits").replaceChildren(grid(["PC", "File", "Line", "Text"], rows, {
        hsm: [1], left: [], emptyText: "No matches. Try fewer words, or tick regex only if you meant one.",
        go: function (row) { return viewHref(row[4]); },
        cell: function (v, i, row) {
          var td = h("td");
          if (i === 0) td.append(link(v, viewHref(row[4])));
          else if (i === 1) { td.className = "mut"; td.style.fontFamily = "var(--mono)"; td.style.fontSize = "12px"; td.textContent = v; }
          else if (i === 2) { td.className = "n"; td.textContent = v; }
          else {
            td.className = "wrapc"; td.style.fontFamily = "var(--mono)"; td.style.fontSize = "12.5px";
            var m = re ? re.exec(v) : null;
            if (m && m[0]) { td.append(document.createTextNode(v.slice(0, m.index)), el("mark", "", m[0]), document.createTextNode(v.slice(m.index + m[0].length))); } else td.textContent = v;
          }
          return td;
        }
      }));
    }).catch(function (e) { failure($("#hits"), e); });
  }

  /* ---------------- admin ---------------- */
  /* the lead's flags as tags: `--max-prs 8000` is one tag, a bare switch is a tag with no value */
  function flagList(flags) {
    var groups = [];
    flags.forEach(function (f) {
      f = String(f);
      if (f.slice(0, 2) === "--" || !groups.length) groups.push({ name: f, value: [] }); else groups[groups.length - 1].value.push(f);
    });
    var d = h("details", "flags"), n = groups.length;
    d.append(h("summary", "", n + (n === 1 ? " setting" : " settings")));
    var box = h("div", "flagbox");
    groups.forEach(function (g) {
      var chip = h("span", "flag"); chip.append(h("b", "", g.name));
      if (g.value.length) chip.append(h("i", "", g.value.join(" ")));
      box.append(chip);
    });
    if (!n) box.append(h("span", "cap", "no flags: the miner's defaults"));
    d.append(box); return d;
  }
  function admin() {
    var pending = null;
    function call(method, path, body) {
      return fetch("/v1/admin/" + path, { method: method, credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-Requested-With": "riffle" }, body: body ? JSON.stringify(body) : undefined
      }).then(function (r) {
        if (r.status === 401) { location.href = "/admin/login"; return new Promise(function () {}); }
        return r.json().then(function (j) { if (!r.ok) throw new Error(j.error || r.statusText); return j; });
      });
    }
    function say(node, text) { node.textContent = text; }
    /* a button that needs a second press: the first press only asks */
    function confirmBtn(label, what, act, danger) {
      var b = el("button", "btn" + (danger ? " bad" : ""), label);
      b.onclick = function () {
        if (pending && pending.what === what) { pending = null; b.disabled = true; act(); return; }
        if (pending) pending.btn.textContent = pending.label;
        pending = { what: what, btn: b, label: label }; b.textContent = "Press again to confirm";
        setTimeout(function () { if (pending && pending.btn === b) { b.textContent = label; pending = null; } }, 6000);
      };
      return b;
    }
    function copyBtn(text, label) {
      var b = el("button", "btn", label || "Copy");
      b.onclick = function () {
        (navigator.clipboard ? navigator.clipboard.writeText(text) : Promise.reject()).then(
          function () { b.textContent = "Copied"; setTimeout(function () { b.textContent = label || "Copy"; }, 1500); },
          function () { b.textContent = "Select and copy by hand"; });
      };
      return b;
    }
    function stateTag(st) {
      return tag(st, { mining: "green", done: "green", stopped: "amber", silent: "red", expired: "red", never: "" }[st] || "");
    }
    function showCodes(d) {
      $("#newcodes").hidden = false; $("#nc-table").hidden = false;
      $("#nc-title").textContent = "New join codes for split " + d.split_id;
      say($("#nc-note"), "The old tokens stopped working. Send each code only to that PC's owner. This list is not stored: leave the page and it is gone.");
      var tools = $("#nc-tools"); tools.replaceChildren(copyBtn(d.pcs.map(function (p) { return "PC " + p.k + "/" + p.n + "\t" + p.code; }).join("\n"), "Copy all"));
      var rows = d.pcs.map(function (p) { return [p.pc, p.code]; });
      $("#nc-rows").replaceChildren(grid(["PC", "Join code", ""], rows.map(function (r) { return r.concat([r[1]]); }), {
        cell: function (v, i, row) {
          var td = h("td");
          if (i === 0) td.textContent = v;
          else if (i === 1) { td.className = "wrapc"; td.style.fontFamily = "var(--mono)"; td.textContent = v.slice(0, 26) + "…" + v.slice(-10); }
          else td.append(copyBtn(row[2]));
          return td;
        }
      }));
      $("#newcodes").scrollIntoView({ block: "start" });
    }
    function draw() {
      call("GET", "overview").then(function (d) {
        var soon = Date.now() / 1000 + 3 * 86400;
        $("#kpis").replaceChildren(
          stat("PC tokens", String(d.pcs.length), d.splits.length + " split" + (d.splits.length === 1 ? "" : "s")),
          stat("Uploading", String(d.pcs.filter(function (p) { return p.state === "mining"; }).length), "synced in the last 25 min", "good"),
          stat("Never uploaded", String(d.pcs.filter(function (p) { return p.state === "never"; }).length), "token issued, nothing sent", d.pcs.some(function (p) { return p.state === "never"; }) ? "warn" : ""),
          stat("Expiring in 3 days", String(d.pcs.filter(function (p) { return p.expires && p.expires < soon && p.expires > Date.now() / 1000; }).length), "rotate to extend"),
          stat("Readers", String(d.readers.length), d.admins.length + " admin" + (d.admins.length === 1 ? "" : "s")));
        // splits
        var sp = d.splits.map(function (x) { return [x.split_id, x, x, x, x, x, x]; });          // one element per column
        $("#splits").replaceChildren(sp.length ? grid(["Split", "PCs", "Uploaded", "Miner", "Created", "Flags", ""], sp, {
          hsm: [3, 4, 5], left: [1, 2],
          cell: function (v, i, row) {
            var x = row[1], td = h("td");
            if (i === 0) td.textContent = x.split_id;
            else if (i === 1) td.textContent = x.n + " (" + x.tokens + " active)";
            else if (i === 2) td.textContent = x.uploaded + "/" + x.tokens + " uploaded";
            else if (i === 3) td.textContent = x.version || "?";
            else if (i === 4) td.textContent = (x.created || "").slice(0, 10);
            else if (i === 5) { td.append(flagList(x.flags || [])); }
            else {
              var acts = el("div", "actions");
              acts.append(x.can_rotate ? confirmBtn("Rotate", "rot:" + x.split_id, function () {
                call("POST", "rotate", { split_id: x.split_id }).then(function (r) { showCodes(r); draw(); }).catch(function (e) { window.riffleToast(e.message); draw(); });
              }) : el("span", "cap", "plan it again to rotate"),
                confirmBtn("Revoke all", "revs:" + x.split_id, function () { call("POST", "revoke", { split_id: x.split_id }).then(draw).catch(function (e) { window.riffleToast(e.message); draw(); }); }, true));
              td.append(acts);
            }
            return td;
          }
        }) : el("div", "empty", "No split has been planned yet. Plan one from the miner's TUI: Lead tools (F3)."));
        // PCs
        $("#pcs").replaceChildren(grid(["PC", "Split", "State", "Progress", "PRs mined", "Expires", "Last upload", ""],
          d.pcs.map(function (p) { return [p.pc, p.split, p.state, p, p.prs_done, p.expires, p.last_upload, p]; }), {
          emptyText: "No PC tokens issued yet. Plan a split from the miner's TUI (Lead tools, F3).", hsm: [1, 3, 4, 5], left: [5, 6],
          cell: function (v, i, row) {
            var p = row[3], td = h("td");
            if (i === 0) { td.append(el("div", "", p.pc), el("div", "cap", p.host || "")); }
            else if (i === 1) td.textContent = v || "-";
            else if (i === 2) td.append(stateTag(v));
            else if (i === 3) td.textContent = p.repos_total ? p.repos_done + "/" + p.repos_total + " repos" : "–";
            else if (i === 4) { td.className = "n"; td.textContent = v == null ? "–" : v.toLocaleString(); }
            else if (i === 5) td.textContent = v ? new Date(v * 1000).toISOString().slice(0, 10) : "never";
            else if (i === 6) td.textContent = ago(v);
            else td.append(confirmBtn("Revoke", "rev:" + p.pc, function () { call("POST", "revoke", { name: p.pc, kind: "pcs" }).then(draw).catch(function (e) { window.riffleToast(e.message); draw(); }); }, true));
            return td;
          }
        }));
        // readers and admins
        $("#readers").replaceChildren(grid(["Reader", "Created", ""], d.readers.map(function (r) { return [r.name, (r.created || "").slice(0, 10), r.name]; }), {
          emptyText: "No readers yet. Add one above.", left: [1],
          cell: function (v, i, row) {
            var td = h("td");
            if (i < 2) td.textContent = v;
            else td.append(confirmBtn("Revoke", "revr:" + v, function () { call("POST", "revoke", { name: v, kind: "readers" }).then(draw).catch(function (e) { window.riffleToast(e.message); draw(); }); }, true));
            return td;
          }
        }));
        $("#admins").replaceChildren(grid(["Admin", "Created"], d.admins.map(function (r) { return [r.name, (r.created || "").slice(0, 10)]; }), { emptyText: "No admins." }));
      }).catch(function (e) { failure($("#pcs"), e); });
    }
    $("#rform").onsubmit = function (e) {
      e.preventDefault();
      var name = e.target.name.value.trim(), box = $("#newreader");
      if (!/^[a-z0-9][a-z0-9_-]{0,39}$/.test(name)) {
        window.riffleFieldError(e.target.name, "Use lowercase letters, digits, - or _ (40 characters at most), e.g. maria.");
        return;
      }
      window.riffleFieldError(e.target.name, "");
      call("POST", "readers", { name: name }).then(function (r) {
        box.hidden = false; box.replaceChildren(el("b", "", "Read token for " + r.name + " (shown once): "), el("code", "", r.token), document.createTextNode(" "), copyBtn(r.token));
        e.target.reset(); draw();
      }).catch(function (err) { box.hidden = false; box.textContent = err.message; });
    };
    draw(); setInterval(draw, 60000);
  }

  var page = document.body.dataset.page;
  var pages = { overview: overview, run: run, data: data, view: view, search: search, admin: admin };
  if (pages[page]) pages[page]();
})();
