// File-upload capturer (project 1).
//
// Captures files the user adds to the page via any of the three DOM paths —
// the native file picker (<input type=file> change), drag-and-drop, and paste.
// It only observes; it never calls preventDefault, so the upload to ChatGPT
// proceeds normally (non-blocking, per the assignment).
//
// Emits each captured PDF File to the provided callback.
window.startFileUploadCapturer = function startFileUploadCapturer(onFile) {
  const isPdf = (f) =>
    !!f && (f.type === "application/pdf" || /\.pdf$/i.test(f.name || ""));

  const handleFiles = (fileList) => {
    if (!fileList) return;
    for (const f of Array.from(fileList)) {
      if (isPdf(f)) onFile(f);
    }
  };

  // 1) File picker selection. Capture phase + document-level so it works even
  //    for inputs that are created dynamically by the SPA.
  document.addEventListener(
    "change",
    (e) => {
      const t = e.target;
      if (t && t.tagName === "INPUT" && t.type === "file") handleFiles(t.files);
    },
    true
  );

  // 2) Drag and drop onto the page.
  document.addEventListener(
    "drop",
    (e) => {
      if (e.dataTransfer && e.dataTransfer.files) handleFiles(e.dataTransfer.files);
    },
    true
  );

  // 3) Paste (e.g. a copied PDF).
  document.addEventListener(
    "paste",
    (e) => {
      if (e.clipboardData && e.clipboardData.files) handleFiles(e.clipboardData.files);
    },
    true
  );

  console.log("[inspector] file-upload capturer active (picker + drag-drop + paste)");
};
