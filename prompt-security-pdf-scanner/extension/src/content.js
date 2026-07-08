// Content script entry point.
//
// Runs only on hosts declared in manifest.json. Picks the site profile, wires
// up the file-upload capturer, and turns captured PDFs into inspection requests
// (sent to the background service worker, which talks to the backend). On a
// positive result it shows a non-blocking alert.
(function () {
  const profile = window.getSiteProfile ? window.getSiteProfile() : null;
  console.log("[inspector] content script active on", location.hostname, profile);

  function inspectCapturedFile(file) {
    console.log("[inspector] captured PDF, inspecting:", file.name, file.type, file.size + " bytes");
    const reader = new FileReader();
    reader.onerror = () => console.warn("[inspector] could not read file", file.name);
    reader.onload = () => {
      // reader.result is a data URL: "data:application/pdf;base64,XXXX"
      const base64 = String(reader.result).split(",")[1] || "";
      if (!base64) return;
      console.log("[inspector] sending", file.name, "to background for inspection");
      chrome.runtime.sendMessage(
        {
          type: "INSPECT_FILE",
          name: file.name,
          mime: file.type || "application/pdf",
          dataBase64: base64,
        },
        (resp) => {
          if (chrome.runtime.lastError) {
            console.warn("[inspector] messaging error:", chrome.runtime.lastError.message);
            window.showInspectionError();
            return;
          }
          console.log("[inspector] result for", file.name, resp);
          if (!resp || !resp.ok) {
            console.warn("[inspector] inspection failed:", resp && resp.error);
            window.showInspectionError();
            return;
          }
          if (resp.result && resp.result.has_secrets) {
            window.showInspectionAlert(file.name, resp.result);
          }
        }
      );
    };
    reader.readAsDataURL(file);
  }

  // The file-upload capturer captures PDF uploads (picker / drag-drop / paste).
  if (window.startFileUploadCapturer) {
    window.startFileUploadCapturer(inspectCapturedFile);
  }
})();
