// Records what you do on a page while a Pantheon recording is running: clicks (with the
// element's label), form values, submits and Enter. Never passwords, card numbers, one-time
// codes or anything that looks like a secret: those values are replaced with [hidden].
(() => {
  const SENSITIVE = /pass|pwd|secret|token|api.?key|card|cc-|cvv|cvc|ssn|iban|otp|one-time|\bpin\b|security.?code/i;
  let recording = false;
  chrome.storage.local.get("recording", (s) => (recording = !!s.recording));
  chrome.storage.onChanged.addListener((c) => {
    if (c.recording) recording = !!c.recording.newValue;
  });

  const text = (s) => (s || "").replace(/\s+/g, " ").trim().slice(0, 120);
  const label = (el) =>
    text(
      el.getAttribute?.("aria-label") ||
        el.labels?.[0]?.innerText ||
        el.getAttribute?.("placeholder") ||
        el.getAttribute?.("title") ||
        el.getAttribute?.("alt") ||
        (el.tagName !== "INPUT" && el.tagName !== "TEXTAREA" ? el.innerText : "") ||
        el.getAttribute?.("name") ||
        el.id,
    );
  const sensitive = (el) =>
    el.type === "password" ||
    (el.autocomplete || "").startsWith("cc-") ||
    el.autocomplete === "one-time-code" ||
    SENSITIVE.test([el.name, el.id, el.autocomplete, label(el)].join(" "));

  const send = (kind, data) => {
    if (!recording) return;
    try {
      chrome.runtime.sendMessage({ type: "event", event: { kind, data } });
    } catch {
      recording = false; // the extension was reloaded
    }
  };

  document.addEventListener(
    "click",
    (e) => {
      const el =
        e.target.closest?.("a,button,input,select,textarea,summary,label,[role=button],[role=link],[role=tab],[role=menuitem],[role=option],[role=checkbox]") ||
        e.target;
      if (!el) return;
      if (el.tagName === "INPUT" && sensitive(el)) return send("click", { tag: "input", label: "[hidden field]" });
      const data = { tag: (el.getAttribute?.("role") || el.tagName || "").toLowerCase(), label: label(el) };
      if (el.tagName === "A" && el.href && !el.href.startsWith("javascript:")) data.href = el.href.slice(0, 300);
      send("click", data);
    },
    true,
  );

  document.addEventListener(
    "change",
    (e) => {
      const el = e.target;
      if (!(el instanceof HTMLInputElement || el instanceof HTMLSelectElement || el instanceof HTMLTextAreaElement)) return;
      let value;
      if (sensitive(el)) value = "[hidden]";
      else if (el.type === "checkbox" || el.type === "radio") value = el.checked ? "checked" : "unchecked";
      else if (el instanceof HTMLSelectElement) value = text(el.selectedOptions[0]?.innerText);
      else if (el.type === "file") value = `${el.files?.length || 0} file(s)`;
      else value = String(el.value).slice(0, 500);
      send("input", { label: label(el), name: el.name || undefined, value });
    },
    true,
  );

  document.addEventListener(
    "submit",
    (e) => {
      const f = e.target;
      const button = f.querySelector?.("button[type=submit],input[type=submit],button:not([type])");
      send("submit", { label: text(f.getAttribute("aria-label") || f.getAttribute("name") || (button && label(button)) || "") });
    },
    true,
  );

  document.addEventListener(
    "keydown",
    (e) => {
      if (e.key === "Enter" && !e.isComposing && e.target instanceof HTMLElement && !sensitive(e.target)) {
        send("key", { key: "Enter", label: label(e.target) });
      }
    },
    true,
  );
})();
