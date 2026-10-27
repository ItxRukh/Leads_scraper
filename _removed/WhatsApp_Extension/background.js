"use strict";
/*
 * background.js — the extension's service worker. Only this file talks to the backend
 * (the WhatsApp page may not call another origin). It keeps the settings, the last
 * known state for the popup, and a small retry list for results that could not be
 * delivered while the backend was unreachable.
 */
importScripts("shared.js");

const DEFAULTS = { backendUrl: "http://localhost:8000", enabled: true, autoOpen: false };
const WHATSAPP_URL = "https://web.whatsapp.com/";
const PENDING_LIMIT = 200;

async function getSettings() {
  return { ...DEFAULTS, ...(await chrome.storage.local.get(Object.keys(DEFAULTS))) };
}

async function api(path, options = {}, timeoutMs = 15000) {
  const { backendUrl } = await getSettings();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(`${backendUrl}${path}`, {
      ...options,
      headers: { "Content-Type": "application/json" },
      signal: controller.signal,
    });
    if (!response.ok) throw new Error(`Backend answered ${response.status}`);
    return await response.json();
  } finally {
    clearTimeout(timer);
  }
}

const post = (path, payload) => api(path, { method: "POST", body: JSON.stringify(payload) });

// ─── results that could not be delivered are kept and retried ─────────────────

async function keepForLater(path, payload) {
  const { pending = [] } = await chrome.storage.local.get("pending");
  pending.push({ path, payload });
  await chrome.storage.local.set({ pending: pending.slice(-PENDING_LIMIT) });
}

async function flushPending() {
  const { pending = [] } = await chrome.storage.local.get("pending");
  const stillPending = [];
  for (let i = 0; i < pending.length; i++) {
    try {
      await post(pending[i].path, pending[i].payload);
    } catch (e) {
      stillPending.push(...pending.slice(i));
      break;
    }
  }
  if (stillPending.length !== pending.length) await chrome.storage.local.set({ pending: stillPending });
}

async function deliverOrKeep(path, payload) {
  try {
    await post(path, payload);
  } catch (e) {
    await keepForLater(path, payload);
  }
}

// ─── messages from the WhatsApp tab and from the popup ───────────────────────

async function handle(message) {
  const settings = await getSettings();

  switch (message.type) {
    case "heartbeat": {
      let backendOk = true;
      try {
        await post("/whatsapp/heartbeat", { state: message.state, number: message.number, extension_version: "1.0.0" });
        await flushPending();
      } catch (e) {
        backendOk = false;
      }
      await chrome.storage.local.set({
        lastState: message.state, lastNumber: message.number, lastSeen: Date.now(), backendOk,
      });
      return { enabled: settings.enabled };
    }

    case "queue":
      if (!settings.enabled) return { messages: [] };
      try {
        return await api("/whatsapp/queue?limit=1");
      } catch (e) {
        return { messages: [] };
      }

    case "report": {
      await deliverOrKeep("/whatsapp/report", message.payload);
      if (message.payload.status === "sent") {
        const { sentCount = 0 } = await chrome.storage.local.get("sentCount");
        await chrome.storage.local.set({ sentCount: sentCount + 1 });
      }
      return {};
    }

    case "inbound":
      await deliverOrKeep("/whatsapp/inbound", message.payload);
      return {};

    case "popupState": {
      const stored = await chrome.storage.local.get(["lastState", "lastNumber", "lastSeen", "backendOk", "sentCount", "pending"]);
      return { settings, ...stored, pendingCount: (stored.pending || []).length };
    }

    case "saveSettings": {
      const url = LS.normalizeUrl(message.backendUrl);
      if (!url) return { error: "Enter a full address such as http://localhost:8000" };
      await chrome.storage.local.set({
        backendUrl: url, enabled: Boolean(message.enabled), autoOpen: Boolean(message.autoOpen),
      });
      return { ok: true, backendUrl: url };
    }

    case "testBackend":
      try {
        const connections = await api("/connections", {}, 8000);
        return { ok: true, whatsapp: connections.whatsapp };
      } catch (e) {
        return { ok: false, error: "Could not reach the backend. Is it running, and is the address right?" };
      }

    case "openWhatsApp":
      await openWhatsApp(true);
      return { ok: true };

    default:
      return { error: "Unknown request" };
  }
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  handle(message).then(sendResponse).catch((e) => sendResponse({ error: String(e) }));
  return true;                                      // the answer is sent asynchronously
});

// ─── WhatsApp tab ─────────────────────────────────────────────────────────────

async function openWhatsApp(focus) {
  const tabs = await chrome.tabs.query({ url: `${WHATSAPP_URL}*` });
  if (tabs.length) {
    if (focus) {
      await chrome.tabs.update(tabs[0].id, { active: true });
      await chrome.windows.update(tabs[0].windowId, { focused: true });
    }
    return;
  }
  await chrome.tabs.create({ url: WHATSAPP_URL, pinned: true, active: focus });
}

// Optional: open WhatsApp Web by itself when messages are waiting and no tab is open.
chrome.alarms.create("autoOpen", { periodInMinutes: 1 });
chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (alarm.name !== "autoOpen") return;
  const settings = await getSettings();
  if (!settings.enabled || !settings.autoOpen) return;
  try {
    const { whatsapp } = await api("/connections", {}, 8000);
    if (whatsapp.waiting > 0 && !whatsapp.online) await openWhatsApp(false);
  } catch (e) { /* backend not reachable: nothing to do */ }
});
