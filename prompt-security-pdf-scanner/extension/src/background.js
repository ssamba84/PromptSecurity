// Background service worker.
//
// Receives captured files from the content script, forwards them to the local
// inspection service (multipart POST /inspect/file), records results for the
// popup, and returns the verdict to the content script. Running the network
// call here (rather than in the content script) keeps it off the page and
// unaffected by the page's Content-Security-Policy.

console.log("[inspector] background service worker loaded");

const DEFAULTS = { backendUrl: "http://127.0.0.1:8000", enabled: true };

async function getConfig() {
  const stored = await chrome.storage.local.get(DEFAULTS);
  return {
    backendUrl: (stored.backendUrl || DEFAULTS.backendUrl).replace(/\/+$/, ""),
    enabled: stored.enabled !== false,
  };
}

function base64ToBlob(base64, mime) {
  const bin = atob(base64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new Blob([bytes], { type: mime || "application/pdf" });
}

async function inspectFile(msg, backendUrl) {
  const blob = base64ToBlob(msg.dataBase64, msg.mime);
  const form = new FormData();
  form.append("file", blob, msg.name || "upload.pdf");
  const resp = await fetch(backendUrl + "/inspect/file", { method: "POST", body: form });
  if (!resp.ok) {
    const detail = await resp.text().catch(() => "");
    throw new Error(`service responded ${resp.status} ${detail}`);
  }
  return resp.json();
}

async function recordResult(name, result) {
  const { recent = [] } = await chrome.storage.local.get({ recent: [] });
  recent.unshift({
    name,
    hasSecrets: !!result.has_secrets,
    provider: result.provider,
    findings: (result.findings || []).map((f) => f.type),
    severity: result.severity || null,
    ts: Date.now(),
  });
  await chrome.storage.local.set({ recent: recent.slice(0, 20) });
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg && msg.type === "INSPECT_FILE") {
    (async () => {
      try {
        const { backendUrl, enabled } = await getConfig();
        console.log("[inspector] INSPECT_FILE received:", msg.name, "-> POST", backendUrl + "/inspect/file");
        if (!enabled) {
          sendResponse({ ok: true, skipped: true });
          return;
        }
        const result = await inspectFile(msg, backendUrl);
        await recordResult(msg.name, result);
        console.log("[inspector] result for", msg.name, result);
        sendResponse({ ok: true, result });
      } catch (err) {
        console.error("[inspector] inspection failed:", err);
        sendResponse({ ok: false, error: String(err) });
      }
    })();
    return true; // keep the message channel open for the async response
  }
  return false;
});
