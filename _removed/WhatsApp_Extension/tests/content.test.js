"use strict";
// Runs content.js against a fake WhatsApp page and a fake background worker.
// Run with:  node --test WhatsApp_Extension/tests
const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const LS = require("../shared.js");
const CONTENT_SOURCE = fs.readFileSync(path.join(__dirname, "..", "content.js"), "utf8");

/**
 * Starts content.js in a sandbox and returns handles to drive it.
 *   page:  how the fake WhatsApp page answers ({ loggedIn, number, send(), resolve() })
 *   hello: what the fake background answers to a heartbeat
 *   queue: messages the fake background hands out (once)
 */
function start({ page = {}, hello = { enabled: true }, queue = [], hasPane = true, hasQr = false, storage = {} } = {}) {
  const listeners = [];
  const seen = { heartbeats: [], queueCalls: 0, reports: [], inbound: [], pageCalls: [] };
  let handedOut = false;

  const windowObj = {
    LS,
    addEventListener(type, fn) { if (type === "message") listeners.push(fn); },
    // postMessage is delivered synchronously, as a message event from the same window
    postMessage(data) { listeners.forEach((fn) => fn({ source: windowObj, data })); },
  };

  // The fake page script: answers commands from the content script synchronously
  windowObj.addEventListener("message", async (event) => {
    const d = event.data;
    if (!d || d.source !== "LS_CONTENT") return;
    seen.pageCalls.push(d.type);
    const reply = (extra) => windowObj.postMessage({ source: "LS_PAGE", type: "reply", id: d.id, ...extra });
    if (d.type === "status") reply({ loggedIn: page.loggedIn ?? true, number: page.number ?? "13055550111" });
    else if (d.type === "resolve") reply({ phone: page.resolve ? page.resolve(d.chatWid) : null });
    else if (d.type === "send") reply(page.send ? await page.send(d) : { ok: true, chatWid: `${d.phone}@c.us`, messageId: "ABC" });
  });

  const store = { ...storage };
  const sandbox = {
    window: windowObj,
    document: { querySelector: (sel) => ((sel === "#pane-side" && hasPane) || (sel.includes("canvas") && hasQr) ? {} : null) },
    chrome: {
      runtime: {
        async sendMessage(message) {
          if (message.type === "heartbeat") { seen.heartbeats.push(message); return hello; }
          if (message.type === "queue") {
            seen.queueCalls++;
            if (handedOut) return { messages: [] };
            handedOut = true;
            return { messages: queue };
          }
          if (message.type === "report") { seen.reports.push(message.payload); return {}; }
          if (message.type === "inbound") { seen.inbound.push(message.payload); return {}; }
          return {};
        },
      },
      storage: {
        local: {
          async get(key) { return { [key]: store[key] }; },
          async set(values) { Object.assign(store, values); },
        },
      },
    },
    // timers: run soon instead of waiting, and never loop
    setTimeout: (fn) => setImmediate(fn),
    setInterval: () => 0,
    Date, Math, Promise, Boolean, String, Object,
  };
  vm.createContext(sandbox);
  vm.runInContext(CONTENT_SOURCE, sandbox);
  return { seen, store, windowObj };
}

// Objects made inside the sandbox have their own prototypes; compare them as plain data.
const same = (actual, expected) => assert.deepStrictEqual(JSON.parse(JSON.stringify(actual)), expected);

const until = async (condition, ms = 1000) => {
  const end = Date.now() + ms;
  while (!condition()) {
    if (Date.now() > end) throw new Error("condition not met in time");
    await new Promise((resolve) => setImmediate(resolve));
  }
};
const settle = () => new Promise((resolve) => setTimeout(resolve, 30));

test("sends a queued message and reports it as sent", async () => {
  const { seen, store } = start({ queue: [{ message_id: 7, lead_id: 1, phone: "13055550100", text: "Hello" }] });
  await until(() => seen.reports.length === 1);

  same(seen.heartbeats[0], { type: "heartbeat", state: "connected", number: "13055550111" });
  same(seen.reports[0], { message_id: 7, status: "sent", external_id: "ABC" });
  same(store.chatPhones, { "13055550100@c.us": "13055550100" });
});

test("reports a number that is not on WhatsApp", async () => {
  const { seen } = start({
    queue: [{ message_id: 8, phone: "13055550101", text: "Hi" }],
    page: { send: () => ({ ok: false, notOnWhatsApp: true, error: "This number is not on WhatsApp" }) },
  });
  await until(() => seen.reports.length === 1);
  same(seen.reports[0], { message_id: 8, status: "not_on_whatsapp" });
});

test("reports a failure with the reason, trimmed to 300 characters", async () => {
  const { seen } = start({
    queue: [{ message_id: 9, phone: "13055550102", text: "Hi" }],
    page: { send: () => ({ ok: false, error: "x".repeat(500) }) },
  });
  await until(() => seen.reports.length === 1);
  assert.strictEqual(seen.reports[0].status, "failed");
  assert.strictEqual(seen.reports[0].error.length, 300);
});

test("does not ask for messages while WhatsApp is not logged in", async () => {
  const { seen } = start({ hasPane: false, hasQr: true, page: { loggedIn: false }, queue: [{ message_id: 1, phone: "13055550100", text: "x" }] });
  await until(() => seen.heartbeats.length === 1);
  await settle();
  assert.strictEqual(seen.heartbeats[0].state, "logged_out");
  assert.strictEqual(seen.queueCalls, 0);
});

test("reports loading while the page is still starting", async () => {
  const { seen } = start({ hasPane: false, hasQr: false, page: { loggedIn: false } });
  await until(() => seen.heartbeats.length === 1);
  assert.strictEqual(seen.heartbeats[0].state, "loading");
});

test("does not send while the extension is paused", async () => {
  const { seen } = start({ hello: { enabled: false }, queue: [{ message_id: 1, phone: "13055550100", text: "x" }] });
  await until(() => seen.heartbeats.length === 1);
  await settle();
  assert.strictEqual(seen.queueCalls, 0);
  assert.strictEqual(seen.reports.length, 0);
});

test("forwards a reply from a phone chat", async () => {
  const { seen, windowObj } = start();
  await until(() => seen.heartbeats.length === 1);
  windowObj.postMessage({ source: "LS_PAGE", type: "incoming", messageId: "false_13055550100@c.us_AB",
    chatWid: "13055550100@c.us", user: "13055550100", server: "c.us", body: "Yes, interested", timestamp: 1 });
  await until(() => seen.inbound.length === 1);
  same(seen.inbound[0], { phone: "13055550100", text: "Yes, interested", message_id: "false_13055550100@c.us_AB" });
});

test("identifies a hidden-id chat from the numbers we sent to", async () => {
  const { seen, windowObj } = start({ storage: { chatPhones: { "987654321@lid": "13055550100" } } });
  await until(() => seen.heartbeats.length === 1);
  windowObj.postMessage({ source: "LS_PAGE", type: "incoming", messageId: "false_987654321@lid_AB",
    chatWid: "987654321@lid", user: "987654321", server: "lid", body: "STOP", timestamp: 1 });
  await until(() => seen.inbound.length === 1);
  assert.strictEqual(seen.inbound[0].phone, "13055550100");
});

test("asks the page to resolve an unknown hidden id, and drops it if that fails", async () => {
  const { seen, windowObj } = start({ page: { resolve: (wid) => (wid === "111@lid" ? "13055550199" : null) } });
  await until(() => seen.heartbeats.length === 1);
  const incoming = (wid, id) => windowObj.postMessage({ source: "LS_PAGE", type: "incoming", messageId: id,
    chatWid: wid, user: wid.split("@")[0], server: "lid", body: "hello", timestamp: 1 });
  incoming("111@lid", "m1");
  incoming("222@lid", "m2");
  await until(() => seen.inbound.length === 1);
  await settle();
  same(seen.inbound.map((m) => m.phone), ["13055550199"]);
});
