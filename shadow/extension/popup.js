const $ = (id) => document.getElementById(id);

const backendValue = () => $("backend").value.trim().replace(/\/+$/, "");

async function show() {
  const s = await chrome.storage.local.get(["recording", "title", "backend", "exclude"]);
  $("idle").hidden = !!s.recording;
  $("busy").hidden = !s.recording;
  $("title").textContent = s.title || "";
  if (s.backend) $("backend").value = s.backend;
  if (s.exclude) $("exclude").value = s.exclude.join(", ");
  if (!s.recording) void findWaiting();
}

// A recording started on the Shadow tab ("My browser") is offered without typing its code.
async function findWaiting() {
  $("waiting").hidden = true;
  try {
    const r = await fetch(`${backendValue()}/api/v1/shadow/pending?source=browser`);
    const { session } = await r.json();
    if (!session) return;
    $("waiting-title").textContent = session.title;
    $("waiting-agent").textContent = session.agentName ? ` for ${session.agentName}` : "";
    $("waiting-start").onclick = () => start(session.code);
    $("waiting").hidden = false;
  } catch {
    // Pantheon not reachable at this address: the code field still works once it is
  }
}

async function start(code) {
  const backend = backendValue();
  const exclude = $("exclude")
    .value.split(/[,\s]+/)
    .map((d) => d.trim().toLowerCase())
    .filter(Boolean);
  $("error").hidden = true;
  try {
    const r = await fetch(`${backend}/api/v1/shadow/connect`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code: code.trim().toUpperCase() }),
    });
    const body = await r.json();
    if (!r.ok) throw new Error(body.detail || `HTTP ${r.status}`);
    await chrome.storage.local.set({
      backend,
      code: code.trim().toUpperCase(),
      exclude,
      sessionId: body.sessionId,
      title: body.title,
      startedAt: Date.now(),
      recording: true,
    });
    chrome.runtime.sendMessage({ type: "started" });
    show();
  } catch (e) {
    $("error").textContent = `Couldn't start: ${e.message}. Is Pantheon running at ${backend}?`;
    $("error").hidden = false;
  }
}

$("start").addEventListener("click", () => start($("code").value));
$("backend").addEventListener("change", () => void findWaiting());

$("stop").addEventListener("click", async () => {
  const s = await chrome.storage.local.get(["backend", "sessionId"]);
  await chrome.runtime.sendMessage({ type: "stop" });
  fetch(`${s.backend}/api/v1/shadow/${s.sessionId}/stop`, { method: "POST" }).catch(() => {});
  show();
});

show();
