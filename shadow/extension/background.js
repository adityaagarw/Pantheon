// Sends what the content script sees to Pantheon, adds page changes and screenshots.
// Everything lives in chrome.storage so it survives the service worker being suspended.

const KEYS = ["backend", "code", "sessionId", "title", "recording", "startedAt", "exclude"];
const get = () => chrome.storage.local.get(KEYS);

function excluded(url, list) {
  try {
    const u = new URL(url);
    if (!/^https?:$/.test(u.protocol)) return true; // chrome://, extensions, files
    return (list || []).some((d) => u.hostname === d || u.hostname.endsWith("." + d));
  } catch {
    return true;
  }
}

let lastShot = 0;
async function screenshot(windowId) {
  const now = Date.now();
  if (now - lastShot < 1500) return null; // Chrome allows ~2 captures a second
  lastShot = now;
  try {
    return await chrome.tabs.captureVisibleTab(windowId, { format: "jpeg", quality: 55 });
  } catch {
    return null;
  }
}

async function post(s, events) {
  try {
    const r = await fetch(`${s.backend}/api/v1/shadow/${s.sessionId}/events`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Shadow-Code": s.code },
      body: JSON.stringify({ events }),
    });
    if (r.status === 401 || r.status === 404 || (r.ok && !(await r.json()).recording)) await stopLocal();
  } catch {
    // Pantheon unreachable for a moment; this event is lost, recording continues
  }
}

async function record(kind, data, tab, withShot) {
  const s = await get();
  if (!s.recording || !tab || excluded(tab.url, s.exclude)) return;
  const event = { t: (Date.now() - s.startedAt) / 1000, kind, data };
  if (withShot) {
    await new Promise((r) => setTimeout(r, 500)); // let the page react first
    const image = await screenshot(tab.windowId);
    if (image) event.image = image;
  }
  await post(s, [event]);
}

async function stopLocal() {
  await chrome.storage.local.set({ recording: false });
  await chrome.action.setBadgeText({ text: "" });
}

chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  if (msg.type === "event") {
    const { kind, data } = msg.event;
    record(kind, data, sender.tab, kind === "click" || kind === "submit");
    return false;
  }
  if (msg.type === "started") {
    chrome.action.setBadgeBackgroundColor({ color: "#d33" });
    chrome.action.setBadgeText({ text: "REC" });
    chrome.tabs.query({ active: true, currentWindow: true }, ([tab]) => tab && record("navigate", { url: tab.url, title: tab.title }, tab, true));
    return false;
  }
  if (msg.type === "stop") {
    stopLocal().then(() => reply({ ok: true }));
    return true;
  }
  return false;
});

chrome.tabs.onUpdated.addListener((tabId, info, tab) => {
  if (info.status === "complete" && tab.active) record("navigate", { url: tab.url, title: tab.title }, tab, true);
});

chrome.tabs.onActivated.addListener(({ tabId }) => {
  chrome.tabs.get(tabId, (tab) => {
    if (!chrome.runtime.lastError && tab?.url) record("navigate", { url: tab.url, title: tab.title, switched: true }, tab, true);
  });
});
