"use strict";
/*
 * content.js — lives in the WhatsApp Web tab.
 *
 * Every TICK_MS it tells the backend whether WhatsApp is ready, asks for messages to
 * send, delivers them one at a time with a human-looking pause, and reports the result.
 * Replies written by leads are forwarded to the backend as they arrive.
 * Network calls go through the background worker; WhatsApp itself is reached through
 * page-bridge.js.
 */
(function () {
  const LS = window.LS;
  const TICK_MS = 20000;
  const MIN_BETWEEN_SENDS_MS = 20000;
  const PHONE_MAP_LIMIT = 500;

  let busy = false;
  let lastSendAt = 0;
  let nextRequestId = 0;
  const waiting = new Map();                      // request id -> resolve

  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  // ─── talking to the page script and the background worker ──────────────────

  window.addEventListener("message", (event) => {
    const data = event.data;
    if (event.source !== window || !data || data.source !== "LS_PAGE") return;
    if (data.type === "incoming") {
      handleIncoming(data);
    } else if (data.type === "reply" && waiting.has(data.id)) {
      waiting.get(data.id)(data);
      waiting.delete(data.id);
    }
  });

  function askPage(type, payload, timeoutMs) {
    return new Promise((resolve) => {
      const id = ++nextRequestId;
      waiting.set(id, resolve);
      window.postMessage({ source: "LS_CONTENT", id, type, ...payload }, "*");
      setTimeout(() => {
        if (waiting.delete(id)) resolve({ ok: false, error: "WhatsApp Web did not answer" });
      }, timeoutMs || 30000);
    });
  }

  async function askBackground(message) {
    try {
      return (await chrome.runtime.sendMessage(message)) || {};
    } catch (e) {
      return {};                                   // the extension was reloaded: this tab needs a refresh
    }
  }

  // ─── chat id -> phone number memory (for replies that arrive under a hidden id) ──

  async function rememberPhone(chatWid, phone) {
    if (!chatWid) return;
    const { chatPhones = {} } = await chrome.storage.local.get("chatPhones");
    delete chatPhones[chatWid];
    chatPhones[chatWid] = phone;
    await chrome.storage.local.set({ chatPhones: LS.capMap(chatPhones, PHONE_MAP_LIMIT) });
  }

  async function phoneFor(data) {
    const direct = LS.phoneFromChat({ server: data.server, user: data.user });
    if (direct) return direct;
    const { chatPhones = {} } = await chrome.storage.local.get("chatPhones");
    if (chatPhones[data.chatWid]) return chatPhones[data.chatWid];
    const resolved = await askPage("resolve", { chatWid: data.chatWid }, 5000);
    return resolved.phone || null;
  }

  // ─── incoming ───────────────────────────────────────────────────────────────

  async function handleIncoming(data) {
    const phone = await phoneFor(data);
    if (!phone || !data.body) return;              // not a lead we can identify, or no text
    askBackground({ type: "inbound", payload: { phone, text: data.body, message_id: data.messageId } });
  }

  // ─── outgoing ───────────────────────────────────────────────────────────────

  async function deliver(item) {
    await sleep(LS.randomBetween(2000, 6000));      // a short, varied pause before each message
    const result = await askPage("send", { phone: item.phone, text: item.text }, 45000);
    lastSendAt = Date.now();

    let payload;
    if (result.ok) {
      await rememberPhone(result.chatWid, item.phone);
      payload = { message_id: item.message_id, status: "sent", external_id: result.messageId || null };
    } else if (result.notOnWhatsApp) {
      payload = { message_id: item.message_id, status: "not_on_whatsapp" };
    } else {
      payload = { message_id: item.message_id, status: "failed", error: String(result.error || "unknown error").slice(0, 300) };
    }
    await askBackground({ type: "report", payload });
  }

  async function tick() {
    const page = await askPage("status", {}, 5000);
    const state = LS.stateFromPage({
      loggedIn: Boolean(page.loggedIn) || Boolean(document.querySelector("#pane-side")),
      hasQr: Boolean(document.querySelector("canvas[aria-label], div[data-ref] canvas")),
    });

    const hello = await askBackground({ type: "heartbeat", state, number: page.number || null });
    if (state !== "connected" || busy || !hello.enabled) return;
    if (Date.now() - lastSendAt < MIN_BETWEEN_SENDS_MS) return;

    busy = true;
    try {
      const queue = await askBackground({ type: "queue" });
      for (const item of queue.messages || []) await deliver(item);
    } finally {
      busy = false;
    }
  }

  setTimeout(tick, 3000);
  setInterval(tick, TICK_MS);
})();
