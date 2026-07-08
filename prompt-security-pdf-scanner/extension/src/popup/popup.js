const DEFAULTS = { backendUrl: "http://127.0.0.1:8000", enabled: true };

const $ = (id) => document.getElementById(id);

async function load() {
  const cfg = await chrome.storage.local.get(DEFAULTS);
  $("backendUrl").value = cfg.backendUrl || DEFAULTS.backendUrl;
  $("enabled").checked = cfg.enabled !== false;
  await renderRecent();
}

async function renderRecent() {
  const { recent = [] } = await chrome.storage.local.get({ recent: [] });
  const ul = $("recent");
  ul.textContent = "";
  if (!recent.length) {
    const li = document.createElement("li");
    li.className = "empty";
    li.textContent = "No inspections yet.";
    ul.appendChild(li);
    return;
  }
  for (const r of recent) {
    const li = document.createElement("li");
    li.className = r.hasSecrets ? "hit" : "ok";

    const name = document.createElement("div");
    name.className = "name";
    name.textContent = r.name || "(file)";

    const meta = document.createElement("div");
    meta.className = "meta";
    const when = new Date(r.ts).toLocaleTimeString();
    meta.textContent = r.hasSecrets
      ? `⚠️ ${r.findings.join(", ") || "secret"} · ${r.provider} · ${when}`
      : `✓ clean · ${r.provider} · ${when}`;

    li.appendChild(name);
    li.appendChild(meta);
    ul.appendChild(li);
  }
}

$("save").addEventListener("click", async () => {
  await chrome.storage.local.set({
    backendUrl: $("backendUrl").value.trim() || DEFAULTS.backendUrl,
    enabled: $("enabled").checked,
  });
  const s = $("status");
  s.textContent = "Saved";
  setTimeout(() => (s.textContent = ""), 1500);
});

$("clear").addEventListener("click", async () => {
  await chrome.storage.local.set({ recent: [] });
  await renderRecent();
});

// Keep the list fresh if an inspection happens while the popup is open.
chrome.storage.onChanged.addListener((changes) => {
  if (changes.recent) renderRecent();
});

load();
