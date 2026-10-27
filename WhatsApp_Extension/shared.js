"use strict";
/*
 * shared.js — small pure helpers used by the page script, the content script and
 * the background worker (each one loads its own copy). Nothing here touches the
 * page or the network, so it can be tested with plain Node (see tests/).
 */
(function (root) {
  const LS = {};

  // "false_13055550100@c.us_3EB0ABC" -> { fromMe: false, chatWid: "13055550100@c.us", user: "13055550100", server: "c.us" }
  LS.parseMessageId = function (id) {
    const match = /^(true|false)_([^_]+)_/.exec(String(id || ""));
    if (!match) return null;
    const wid = match[2];
    const at = wid.indexOf("@");
    if (at < 1) return null;
    return { fromMe: match[1] === "true", chatWid: wid, user: wid.slice(0, at), server: wid.slice(at + 1) };
  };

  // One-to-one chats only: "c.us" (phone number) or "lid" (hidden number). Groups and status are ignored.
  LS.isPrivateChat = function (server) {
    return server === "c.us" || server === "lid";
  };

  // The phone number behind a chat id, or null when it is not a plain number (for example a "lid").
  LS.phoneFromChat = function (parsed) {
    if (!parsed || parsed.server !== "c.us") return null;
    const digits = parsed.user.split(":")[0];
    return /^\d{8,15}$/.test(digits) ? digits : null;
  };

  // connected | logged_out | loading, from what is visible in the WhatsApp Web page
  LS.stateFromPage = function (page) {
    if (page.loggedIn) return "connected";
    if (page.hasQr) return "logged_out";
    return "loading";
  };

  LS.randomBetween = function (min, max, rand) {
    return Math.round(min + (rand || Math.random)() * (max - min));
  };

  // "http://localhost:8000/" -> "http://localhost:8000"; anything that is not http(s) -> null
  LS.normalizeUrl = function (value) {
    try {
      const url = new URL(String(value || "").trim());
      if (url.protocol !== "http:" && url.protocol !== "https:") return null;
      return url.origin;
    } catch (e) {
      return null;
    }
  };

  // Keep only the newest `limit` entries of an { key: value } map (insertion order).
  LS.capMap = function (map, limit) {
    const keys = Object.keys(map);
    const out = {};
    keys.slice(Math.max(0, keys.length - limit)).forEach((k) => { out[k] = map[k]; });
    return out;
  };

  root.LS = LS;
  if (typeof module !== "undefined" && module.exports) module.exports = LS;
})(typeof globalThis !== "undefined" ? globalThis : this);
