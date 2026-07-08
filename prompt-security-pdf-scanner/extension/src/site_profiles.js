// Site-profile registry — the extension-side reuse seam.
//
// Static match patterns in manifest.json decide *whether* the content script
// runs. This registry (keyed by hostname) decides *how* to capture on a given
// site. Adding a new AI site = add a match pattern + an entry here; no core
// logic changes.
window.SITE_PROFILES = {
  "chatgpt.com": {
    name: "ChatGPT",
    uploadZones: ["form", "main"],
  },
  "chat.openai.com": {
    name: "ChatGPT (legacy domain)",
    uploadZones: ["form", "main"],
  },
  // Extend later, e.g. "claude.ai": { name: "Claude" }, "gemini.google.com": { ... }
};

window.getSiteProfile = function getSiteProfile() {
  return window.SITE_PROFILES[location.hostname] || null;
};
