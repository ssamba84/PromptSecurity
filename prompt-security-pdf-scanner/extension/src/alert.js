// Non-blocking alert modal, injected into the page by the content script.
//
// Built entirely with createElement + textContent (no innerHTML). This is
// required because chatgpt.com enforces Trusted Types, which makes any
// `element.innerHTML = ...` assignment throw. It's also safe by construction —
// finding data can never be interpreted as markup.
window.showInspectionAlert = function showInspectionAlert(filename, result) {
  try {
    const findings = (result && result.findings) || [];
    const types = Array.from(new Set(findings.map((f) => f.type)));

    const existing = document.getElementById("__inspector_alert");
    if (existing) existing.remove();

    const el = document.createElement("div");
    el.id = "__inspector_alert";
    el.className = "inspector-alert";
    el.setAttribute("role", "alert");

    const icon = document.createElement("div");
    icon.className = "inspector-alert__icon";
    icon.setAttribute("aria-hidden", "true");
    icon.textContent = "⚠️";

    const body = document.createElement("div");
    body.className = "inspector-alert__body";

    const title = document.createElement("div");
    title.className = "inspector-alert__title";
    title.textContent = "Potential secret detected";

    const file = document.createElement("div");
    file.className = "inspector-alert__file";
    file.textContent = filename;

    const typesEl = document.createElement("div");
    typesEl.className = "inspector-alert__types";
    typesEl.textContent = types.length ? "Found: " + types.join(", ") : "";

    const hint = document.createElement("div");
    hint.className = "inspector-alert__hint";
    hint.textContent = "The upload was not blocked — review before sending.";

    body.append(title, file, typesEl, hint);

    const close = document.createElement("button");
    close.className = "inspector-alert__close";
    close.setAttribute("aria-label", "Dismiss");
    close.textContent = "×";
    close.addEventListener("click", () => el.remove());

    el.append(icon, body, close);
    document.body.appendChild(el);

    // Auto-dismiss after 15s so it never lingers.
    setTimeout(() => {
      if (el.isConnected) el.remove();
    }, 15000);

    console.log("[inspector] alert shown for", filename);
  } catch (err) {
    console.error("[inspector] failed to render alert:", err);
  }
};

// Non-blocking, unobtrusive toast shown when the inspection service can't be
// reached — so a captured file failing to be inspected isn't silent.
window.showInspectionError = function showInspectionError(message) {
  try {
    const existing = document.getElementById("__inspector_error");
    if (existing) existing.remove();

    const el = document.createElement("div");
    el.id = "__inspector_error";
    el.className = "inspector-alert inspector-alert--error";
    el.setAttribute("role", "status");

    const icon = document.createElement("div");
    icon.className = "inspector-alert__icon";
    icon.setAttribute("aria-hidden", "true");
    icon.textContent = "🔌";

    const body = document.createElement("div");
    body.className = "inspector-alert__body";

    const title = document.createElement("div");
    title.className = "inspector-alert__title";
    title.textContent = "Inspection unavailable";

    const msg = document.createElement("div");
    msg.className = "inspector-alert__hint";
    msg.textContent = message || "Couldn't reach the inspection service — the file was not scanned.";

    body.append(title, msg);

    const close = document.createElement("button");
    close.className = "inspector-alert__close";
    close.setAttribute("aria-label", "Dismiss");
    close.textContent = "×";
    close.addEventListener("click", () => el.remove());

    el.append(icon, body, close);
    document.body.appendChild(el);
    setTimeout(() => {
      if (el.isConnected) el.remove();
    }, 8000);
  } catch (err) {
    console.error("[inspector] failed to render error toast:", err);
  }
};
