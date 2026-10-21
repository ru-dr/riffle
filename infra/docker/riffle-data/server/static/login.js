/* Sign-in page: show or hide the token, and a busy state while the form posts. */
(function () {
  var f = document.getElementById("loginform"), t = document.getElementById("token"), b = document.getElementById("reveal"), go = document.getElementById("go");
  if (!f) return;
  b.addEventListener("click", function () {
    var show = t.type === "password";
    t.type = show ? "text" : "password"; b.textContent = show ? "Hide" : "Show"; b.setAttribute("aria-pressed", String(show)); t.focus();
  });
  t.addEventListener("input", function () { t.removeAttribute("aria-invalid"); });
  f.addEventListener("submit", function (e) {
    if (!t.value.trim()) { e.preventDefault(); t.setAttribute("aria-invalid", "true"); t.focus(); return; }
    go.disabled = true; go.textContent = "Signing in…";
  });
})();
