/* Controls that look the same in every browser: a styled select (the browser's own list is not), toasts in place of
   alert(), and inline form errors in place of the browser's validation bubble. Plain selects stay in the page, hidden,
   so every script that reads or sets `select.value` and listens for `change` keeps working. */
(function () {
  "use strict";
  function node(tag, cls, text) { var n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; }

  // -- toast ---------------------------------------------------------------------
  var host;
  window.riffleToast = function (message, kind) {
    if (!host) { host = node("div", "toasts"); host.setAttribute("role", "status"); host.setAttribute("aria-live", "polite"); document.body.append(host); }
    var t = node("div", "toast" + (kind === "ok" ? " ok" : ""), String(message)), x = node("button", "x", "×");
    x.type = "button"; x.setAttribute("aria-label", "Dismiss"); x.onclick = function () { t.remove(); };
    t.append(x); host.append(t);
    setTimeout(function () { t.remove(); }, 8000);
  };

  // -- select --------------------------------------------------------------------
  var openMenu = null;
  function closeMenu(focusBack) {
    if (!openMenu) return;
    var m = openMenu; openMenu = null;
    m.list.hidden = true; m.btn.setAttribute("aria-expanded", "false");
    if (focusBack) m.btn.focus();
  }
  document.addEventListener("mousedown", function (e) { if (openMenu && !openMenu.root.contains(e.target)) closeMenu(false); });
  window.addEventListener("resize", function () { closeMenu(false); });

  function enhance(sel) {
    if (sel.dataset.custom || sel.multiple) return;
    sel.dataset.custom = "1";
    var root = node("div", "sel"), btn = node("button", "selbtn"), list = node("ul", "sellist"), label = node("span", "sellabel");
    btn.type = "button"; btn.setAttribute("aria-haspopup", "listbox"); btn.setAttribute("aria-expanded", "false");
    if (sel.getAttribute("aria-label")) btn.setAttribute("aria-label", sel.getAttribute("aria-label"));
    if (sel.id) { var lab = document.querySelector('label[for="' + sel.id + '"]'); if (lab) lab.onclick = function (e) { e.preventDefault(); btn.focus(); }; }
    list.setAttribute("role", "listbox"); list.hidden = true; list.tabIndex = -1;
    btn.append(label, node("span", "caret"));
    sel.after(root); root.append(btn, list); sel.hidden = true;
    var m = { root: root, btn: btn, list: list }, active = -1;

    function sync() {
      var o = sel.options[sel.selectedIndex]; label.textContent = o ? o.text : "";
      btn.disabled = sel.disabled; root.classList.toggle("ph", !o || o.value === "");
    }
    function build() {
      list.replaceChildren();
      Array.prototype.forEach.call(sel.options, function (o, i) {
        var li = node("li", "opt", o.text); li.setAttribute("role", "option"); li.dataset.i = i;
        li.setAttribute("aria-selected", String(i === sel.selectedIndex)); if (o.disabled) li.setAttribute("aria-disabled", "true");
        list.append(li);
      });
    }
    function mark(i) {
      var items = list.children; active = Math.max(0, Math.min(items.length - 1, i));
      Array.prototype.forEach.call(items, function (li, k) { li.classList.toggle("on", k === active); });
      if (items[active]) items[active].scrollIntoView({ block: "nearest" });
    }
    function open() {
      if (sel.disabled) return;
      closeMenu(false); build(); list.hidden = false; btn.setAttribute("aria-expanded", "true"); openMenu = m;
      var r = btn.getBoundingClientRect(), need = Math.min(list.scrollHeight, 256) + 12;   // open upward only when it does not fit below
      root.classList.toggle("up", window.innerHeight - r.bottom < need && r.top > need);
      mark(sel.selectedIndex);
    }
    function choose(i) {
      var o = sel.options[i]; if (!o || o.disabled) return;
      var changed = sel.selectedIndex !== i; sel.selectedIndex = i; sync(); closeMenu(true);
      if (changed) sel.dispatchEvent(new Event("change", { bubbles: true }));
    }
    btn.onclick = function () { if (openMenu === m) closeMenu(true); else open(); };
    list.onmousedown = function (e) { e.preventDefault(); };
    list.onclick = function (e) { var li = e.target.closest(".opt"); if (li) choose(+li.dataset.i); };
    list.onmousemove = function (e) { var li = e.target.closest(".opt"); if (li) mark(+li.dataset.i); };
    btn.onkeydown = function (e) {
      var k = e.key;
      if (openMenu === m) {
        if (k === "ArrowDown") { mark(active + 1); e.preventDefault(); }
        else if (k === "ArrowUp") { mark(active - 1); e.preventDefault(); }
        else if (k === "Home") { mark(0); e.preventDefault(); }
        else if (k === "End") { mark(list.children.length - 1); e.preventDefault(); }
        else if (k === "Enter" || k === " ") { choose(active); e.preventDefault(); }
        else if (k === "Escape") { closeMenu(true); e.preventDefault(); }
        else if (k === "Tab") closeMenu(false);
        else if (k.length === 1) {                         // type to jump
          var n = Array.prototype.findIndex.call(sel.options, function (o) { return o.text.toLowerCase().indexOf(k.toLowerCase()) === 0; });
          if (n >= 0) mark(n);
        }
      } else if (k === "ArrowDown" || k === "ArrowUp") { open(); e.preventDefault(); }
    };
    sel.addEventListener("change", sync);
    sel.addEventListener("riffle-sync", sync);                  // a script that sets select.value fires this so the label follows
    new MutationObserver(sync).observe(sel, { childList: true, subtree: true, attributes: true, attributeFilter: ["disabled"] });
    // a script sets select.value without an event: look again whenever the page is used
    root.addEventListener("pointerenter", sync);
    sync();
  }
  function scan(rootNode) { (rootNode.querySelectorAll ? rootNode.querySelectorAll("select") : []).forEach(enhance); }
  new MutationObserver(function (muts) {
    muts.forEach(function (m) { m.addedNodes.forEach(function (n) { if (n.nodeType === 1) { if (n.tagName === "SELECT") enhance(n); else scan(n); } }); });
  }).observe(document.documentElement, { childList: true, subtree: true });
  scan(document);

  // -- forms: our own message, not the browser's bubble ---------------------------
  window.riffleFieldError = function (input, message) {
    var host = input.closest("form") || input.parentNode, old = host.querySelector(".ferr");
    if (old) old.remove();
    input.removeAttribute("aria-invalid");
    if (!message) return;
    var e = node("div", "ferr", message); e.setAttribute("role", "alert"); host.append(e);
    input.setAttribute("aria-invalid", "true"); input.focus();
  };
  document.addEventListener("input", function (e) { if (e.target.matches && e.target.matches("input[aria-invalid]")) window.riffleFieldError(e.target, ""); });
})();
