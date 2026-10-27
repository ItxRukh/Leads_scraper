"use strict";

const $ = (id) => document.getElementById(id);
const ask = (message) => chrome.runtime.sendMessage(message);

const STATE_TEXT = {
  connected: ["Connected", "ok"],
  loading: ["Starting…", "warn"],
  logged_out: ["Scan the QR code", "bad"],
};

function setText(id, text, tone) {
  const el = $(id);
  el.textContent = text;
  el.className = tone || "";
}

function say(text, tone) {
  setText("message", text, tone);
}

async function refresh(fillForm) {
  const info = await ask({ type: "popupState" });

  // No signal for 90 s means no WhatsApp Web tab is running the extension.
  const fresh = info.lastSeen && Date.now() - info.lastSeen < 90000;
  const [stateText, stateTone] = fresh ? STATE_TEXT[info.lastState] || ["Unknown", "warn"] : ["Not open", "warn"];
  setText("state", stateText, stateTone);
  setText("backend", info.backendOk === false ? "Not reachable" : info.backendOk ? "Connected" : "Not checked yet",
    info.backendOk === false ? "bad" : info.backendOk ? "ok" : "warn");
  $("sent").textContent = info.sentCount || 0;
  $("pendingRow").hidden = !info.pendingCount;
  $("pending").textContent = info.pendingCount || 0;

  if (fillForm) {
    $("url").value = info.settings.backendUrl;
    $("enabled").checked = info.settings.enabled;
    $("autoOpen").checked = info.settings.autoOpen;
  }
}

async function save() {
  const url = LS.normalizeUrl($("url").value);
  if (!url) return say("Enter a full address such as http://localhost:8000", "bad");

  // Addresses other than localhost need the browser's permission to be contacted.
  if (!/^https?:\/\/(localhost|127\.0\.0\.1)(:\d+)?$/.test(url)) {
    const granted = await chrome.permissions.request({ origins: [`${url}/*`] });
    if (!granted) return say("Permission to reach that address was not given.", "bad");
  }
  const result = await ask({ type: "saveSettings", backendUrl: url, enabled: $("enabled").checked, autoOpen: $("autoOpen").checked });
  if (result.error) return say(result.error, "bad");
  $("url").value = result.backendUrl;
  say("Saved.", "ok");
  refresh(false);
}

async function test() {
  say("Testing…");
  const result = await ask({ type: "testBackend" });
  say(result.ok ? "The backend answered." : result.error, result.ok ? "ok" : "bad");
}

$("open").addEventListener("click", () => ask({ type: "openWhatsApp" }));
$("save").addEventListener("click", save);
$("test").addEventListener("click", test);

refresh(true);
setInterval(() => refresh(false), 3000);
