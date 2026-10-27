"use strict";
/*
 * page-bridge.js — runs inside the WhatsApp Web page itself (MAIN world), at document start.
 *
 * It does three things for the content script, which cannot reach WhatsApp's own code:
 *   status   is WhatsApp logged in, and as which number
 *   send     send one text message to a phone number
 *   incoming report messages that people write to us
 *
 * It talks to the content script with window.postMessage. It relies on WhatsApp Web's
 * internal modules, so if WhatsApp changes them a send reports a clear error instead of
 * failing silently.
 */
(function () {
  if (window.__lsBridge) return;
  window.__lsBridge = true;

  const LS = window.LS;
  const FROM_CONTENT = "LS_CONTENT";
  const FROM_PAGE = "LS_PAGE";
  const SEND_TIMEOUT_MS = 25000;

  const post = (message) => window.postMessage({ source: FROM_PAGE, ...message }, "*");

  function mod(name) {
    try {
      return typeof window.require === "function" ? window.require(name) : null;
    } catch (e) {
      return null;
    }
  }

  // ─── status ───────────────────────────────────────────────────────────────

  function status() {
    try {
      const me = mod("WAWebUserPrefsMeUser");
      const wid = me && typeof me.getMaybeMePnUser === "function" ? me.getMaybeMePnUser() : null;
      if (wid) {
        const digits = String(wid.user || wid._serialized || "").split("@")[0].replace(/\D/g, "");
        return { loggedIn: true, number: digits || null };
      }
    } catch (e) { /* fall through */ }
    return { loggedIn: false, number: null };
  }

  // ─── send ─────────────────────────────────────────────────────────────────

  const withTimeout = (promise, ms) =>
    Promise.race([promise, new Promise((_, reject) => setTimeout(() => reject(new Error("timed out")), ms))]);

  async function send({ phone, text }) {
    const widFactory = mod("WAWebWidFactory");
    const collections = mod("WAWebCollections");
    const findChat = mod("WAWebFindChatAction");
    const sendAction = mod("WAWebSendMsgChatAction");
    const msgKeyRaw = mod("WAWebMsgKey");
    if (!widFactory || !collections || !findChat || !sendAction || !msgKeyRaw
        || typeof widFactory.createWid !== "function"
        || typeof findChat.findOrCreateLatestChat !== "function"
        || typeof sendAction.addAndSendMsgToChat !== "function") {
      return { ok: false, error: "WhatsApp Web internals not available. WhatsApp Web may have changed." };
    }
    if (!/^\d{8,15}$/.test(String(phone))) return { ok: false, error: "Invalid phone number" };

    const wid = widFactory.createWid(`${phone}@c.us`);

    // Is this number on WhatsApp at all?
    const exists = mod("WAWebQueryExistsJob");
    if (exists && typeof exists.queryWidExists === "function") {
      try {
        const found = await withTimeout(exists.queryWidExists(wid), 15000);
        if (!found) return { ok: false, notOnWhatsApp: true, error: "This number is not on WhatsApp" };
      } catch (e) { /* could not check: try to send anyway */ }
    }

    let chat = collections.Chat && typeof collections.Chat.get === "function" ? collections.Chat.get(wid) : null;
    if (!chat) {
      const found = await findChat.findOrCreateLatestChat(wid);
      chat = found && (found.chat || found);
    }
    if (!chat) return { ok: false, error: "Chat not found" };

    const userPrefs = mod("WAWebUserPrefsMeUser");
    const MsgKey = typeof msgKeyRaw === "function" ? msgKeyRaw : msgKeyRaw.default;
    if (typeof msgKeyRaw.newId !== "function" || typeof MsgKey !== "function") {
      return { ok: false, error: "WhatsApp Web message API missing" };
    }
    const chatId = chat.id;
    const lidUser = userPrefs && typeof userPrefs.getMaybeMeLidUser === "function" ? userPrefs.getMaybeMeLidUser() : null;
    const meUser = userPrefs && typeof userPrefs.getMaybeMePnUser === "function" ? userPrefs.getMaybeMePnUser() : null;
    const from = chatId && typeof chatId.isLid === "function" && chatId.isLid() ? lidUser : meUser;

    const key = new MsgKey({ from, to: chatId, id: await msgKeyRaw.newId.call(msgKeyRaw), selfDir: "out" });
    const ephemeral = mod("WAWebGetEphemeralFieldsMsgActionsUtils");
    const message = {
      id: key, ack: 0, body: text, from, to: chatId, local: true, self: "out",
      t: Math.floor(Date.now() / 1000), isNewMsg: true, type: "chat",
      ...(ephemeral && typeof ephemeral.getEphemeralFields === "function" ? ephemeral.getEphemeralFields(chat) : {}),
    };

    const ret = sendAction.addAndSendMsgToChat(chat, message);
    const added = Array.isArray(ret) ? ret[0] : ret;
    const delivered = Array.isArray(ret) ? ret[1] : null;
    if (added && typeof added.then === "function") await withTimeout(added, SEND_TIMEOUT_MS);
    if (delivered && typeof delivered.then === "function") {
      const result = await withTimeout(delivered, SEND_TIMEOUT_MS);
      const outcome = result && result.messageSendResult;
      if (outcome && outcome !== "OK") return { ok: false, error: `WhatsApp refused the message (${outcome})` };
    }
    return { ok: true, chatWid: chatId && chatId._serialized, messageId: key._serialized || null };
  }

  // Best effort: the phone number behind a chat that WhatsApp addresses by a hidden id.
  function resolvePhone(chatWid) {
    try {
      const collections = mod("WAWebCollections");
      const chat = collections && collections.Chat && collections.Chat.get(chatWid);
      const contact = chat && chat.contact;
      const number = contact && (contact.phoneNumber || contact.userid);
      const raw = number && (number._serialized || number.user || String(number));
      const digits = String(raw || "").split("@")[0].replace(/\D/g, "");
      return /^\d{8,15}$/.test(digits) ? digits : null;
    } catch (e) {
      return null;
    }
  }

  // ─── incoming messages ────────────────────────────────────────────────────
  // WhatsApp Web stores every message in the browser's IndexedDB. Watching those writes
  // is more stable than reading its internal objects.

  const reported = new Set();

  function textOf(model) {
    for (const part of [model.body, model.text, model.content, model.message, model.caption]) {
      if (typeof part === "string" && part.length > 0) return part;
    }
    return "";
  }

  function lateText(id) {
    try {
      const collections = mod("WAWebCollections");
      const model = collections && collections.Msg && collections.Msg.get(id);
      return model ? textOf(model) : "";
    } catch (e) {
      return "";
    }
  }

  function report(id, parsed, timestamp, body) {
    if (reported.has(id)) return;
    reported.add(id);
    if (reported.size > 500) reported.delete(reported.values().next().value);
    post({ type: "incoming", messageId: id, chatWid: parsed.chatWid, user: parsed.user,
           server: parsed.server, body, timestamp });
  }

  function onMessageWrite(value) {
    if (!value || !value.id || value.type !== "chat") return;
    const parsed = LS.parseMessageId(value.id);
    if (!parsed || parsed.fromMe || !LS.isPrivateChat(parsed.server)) return;

    const timestamp = (value.t || 0) * 1000;
    const body = textOf(value);
    if (body) return report(value.id, parsed, timestamp, body);

    // The text is sometimes added a moment after the row is written.
    const started = Date.now();
    const timer = setInterval(() => {
      const late = lateText(value.id);
      if (late || Date.now() - started > 2000) {
        clearInterval(timer);
        if (late) report(value.id, parsed, timestamp, late);
      }
    }, 50);
  }

  for (const method of ["put", "add"]) {
    const original = IDBObjectStore.prototype[method];
    IDBObjectStore.prototype[method] = function (value, key) {
      try {
        if (this.name === "message") onMessageWrite(value);
      } catch (e) { /* never break WhatsApp */ }
      return original.call(this, value, key);
    };
  }

  // ─── commands from the content script ─────────────────────────────────────

  window.addEventListener("message", async (event) => {
    const data = event.data;
    if (event.source !== window || !data || data.source !== FROM_CONTENT) return;

    if (data.type === "status") {
      post({ type: "reply", id: data.id, ...status() });
    } else if (data.type === "resolve") {
      post({ type: "reply", id: data.id, phone: resolvePhone(data.chatWid) });
    } else if (data.type === "send") {
      let result;
      try {
        result = await send(data);
      } catch (e) {
        result = { ok: false, error: String((e && e.message) || e) };
      }
      post({ type: "reply", id: data.id, ...result });
    }
  });
})();
