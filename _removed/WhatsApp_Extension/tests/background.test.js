"use strict";
// Runs background.js against a fake browser and a fake backend.
// Run with:  node --test WhatsApp_Extension/tests
const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const SHARED_SOURCE = fs.readFileSync(path.join(__dirname, "..", "shared.js"), "utf8");
const BACKGROUND_SOURCE = fs.readFileSync(path.join(__dirname, "..", "background.js"), "utf8");

const plain = (value) => JSON.parse(JSON.stringify(value));

function start({ store = {}, backend = () => ({ ok: true, json: async () => ({}) }), tabs = [] } = {}) {
  const calls = [];                       // every request the extension made to the backend
  const created = [];                     // tabs it opened
  let onMessage, onAlarm;

  const sandbox = {
    console,
    JSON, Date, Promise, Object, Boolean, URL, AbortController, setTimeout, clearTimeout,
    fetch: async (url, options = {}) => {
      calls.push({ url, method: options.method || "GET", body: options.body ? JSON.parse(options.body) : null });
      return backend(url, options);
    },
    chrome: {
      runtime: { onMessage: { addListener: (fn) => { onMessage = fn; } } },
      storage: {
        local: {
          async get(keys) {
            const list = Array.isArray(keys) ? keys : [keys];
            return Object.fromEntries(list.filter((k) => k in store).map((k) => [k, store[k]]));
          },
          async set(values) { Object.assign(store, values); },
        },
      },
      alarms: { create() {}, onAlarm: { addListener: (fn) => { onAlarm = fn; } } },
      tabs: {
        query: async () => tabs,
        create: async (options) => { created.push(options); },
        update: async () => {},
      },
      windows: { update: async () => {} },
    },
  };
  sandbox.importScripts = () => vm.runInContext(SHARED_SOURCE, sandbox);
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(BACKGROUND_SOURCE, sandbox);

  const send = (message) => new Promise((resolve) => onMessage(message, {}, resolve));
  return { send, calls, created, store, alarm: () => onAlarm({ name: "autoOpen" }) };
}

const online = (body = {}) => ({ ok: true, json: async () => body });
const offline = () => { throw new Error("connection refused"); };

test("a heartbeat is passed on to the backend and remembered", async () => {
  const { send, calls, store } = start();
  const answer = await send({ type: "heartbeat", state: "connected", number: "13055550111" });

  assert.deepStrictEqual(plain(answer), { enabled: true });
  assert.strictEqual(calls[0].url, "http://localhost:8000/whatsapp/heartbeat");
  assert.deepStrictEqual(calls[0].body, { state: "connected", number: "13055550111", extension_version: "1.0.0" });
  assert.strictEqual(store.lastState, "connected");
  assert.strictEqual(store.backendOk, true);
});

test("results that cannot be delivered are kept and sent later, in order", async () => {
  let down = true;
  const { send, calls, store } = start({ backend: () => (down ? offline() : online()) });

  await send({ type: "report", payload: { message_id: 1, status: "sent" } });
  await send({ type: "inbound", payload: { phone: "13055550100", text: "hi", message_id: "m1" } });
  assert.strictEqual(store.pending.length, 2);

  down = false;
  calls.length = 0;
  await send({ type: "heartbeat", state: "connected", number: null });
  assert.deepStrictEqual(calls.map((c) => c.url.replace("http://localhost:8000", "")),
    ["/whatsapp/heartbeat", "/whatsapp/report", "/whatsapp/inbound"]);
  assert.deepStrictEqual(plain(store.pending), []);
});

test("retrying stops at the first failure and keeps the rest", async () => {
  let failFrom = 0;
  const { send, store } = start({ backend: (url) => {
    if (url.endsWith("/whatsapp/heartbeat")) return online();
    if (failFrom++ >= 1) return offline();
    return online();
  } });
  store.pending = [
    { path: "/whatsapp/report", payload: { message_id: 1, status: "sent" } },
    { path: "/whatsapp/report", payload: { message_id: 2, status: "sent" } },
    { path: "/whatsapp/report", payload: { message_id: 3, status: "sent" } },
  ];
  await send({ type: "heartbeat", state: "connected", number: null });
  assert.deepStrictEqual(plain(store.pending).map((p) => p.payload.message_id), [2, 3]);
});

test("sent messages are counted", async () => {
  const { send, store } = start();
  await send({ type: "report", payload: { message_id: 1, status: "sent" } });
  await send({ type: "report", payload: { message_id: 2, status: "failed", error: "x" } });
  await send({ type: "report", payload: { message_id: 3, status: "sent" } });
  assert.strictEqual(store.sentCount, 2);
});

test("the queue is only requested while sending is enabled", async () => {
  const messages = [{ message_id: 5, phone: "13055550100", text: "Hello" }];
  const on = start({ backend: () => online({ messages }) });
  assert.deepStrictEqual(plain(await on.send({ type: "queue" })), { messages });
  assert.strictEqual(on.calls[0].url, "http://localhost:8000/whatsapp/queue?limit=1");

  const off = start({ store: { enabled: false } });
  assert.deepStrictEqual(plain(await off.send({ type: "queue" })), { messages: [] });
  assert.strictEqual(off.calls.length, 0);
});

test("an unreachable backend gives an empty queue, not an error", async () => {
  const { send } = start({ backend: offline });
  assert.deepStrictEqual(plain(await send({ type: "queue" })), { messages: [] });
});

test("settings are validated and normalised", async () => {
  const { send, store } = start();
  assert.ok((await send({ type: "saveSettings", backendUrl: "not a url", enabled: true, autoOpen: false })).error);
  const saved = await send({ type: "saveSettings", backendUrl: "http://192.168.1.5:8000/", enabled: false, autoOpen: true });
  assert.strictEqual(saved.backendUrl, "http://192.168.1.5:8000");
  assert.deepStrictEqual(plain({ url: store.backendUrl, enabled: store.enabled, autoOpen: store.autoOpen }),
    { url: "http://192.168.1.5:8000", enabled: false, autoOpen: true });
});

test("the popup is told the state and how many results are waiting", async () => {
  const { send } = start({ store: { lastState: "connected", sentCount: 4, pending: [{}, {}] } });
  const info = await send({ type: "popupState" });
  assert.strictEqual(info.lastState, "connected");
  assert.strictEqual(info.sentCount, 4);
  assert.strictEqual(info.pendingCount, 2);
  assert.strictEqual(info.settings.backendUrl, "http://localhost:8000");
});

test("testBackend reports whether the backend answered", async () => {
  const good = start({ backend: () => online({ whatsapp: { state: "offline" } }) });
  assert.strictEqual((await good.send({ type: "testBackend" })).ok, true);
  const bad = start({ backend: offline });
  assert.strictEqual((await bad.send({ type: "testBackend" })).ok, false);
});

test("auto-open: opens WhatsApp Web only when messages wait, it is offline and no tab exists", async () => {
  const waiting = { whatsapp: { waiting: 3, online: false } };

  const opens = start({ store: { autoOpen: true }, backend: () => online(waiting) });
  await opens.alarm();
  assert.deepStrictEqual(plain(opens.created), [{ url: "https://web.whatsapp.com/", pinned: true, active: false }]);

  const alreadyOpen = start({ store: { autoOpen: true }, backend: () => online(waiting), tabs: [{ id: 1, windowId: 1 }] });
  await alreadyOpen.alarm();
  assert.strictEqual(alreadyOpen.created.length, 0);

  const nothingWaiting = start({ store: { autoOpen: true }, backend: () => online({ whatsapp: { waiting: 0, online: false } }) });
  await nothingWaiting.alarm();
  assert.strictEqual(nothingWaiting.created.length, 0);

  const switchedOff = start({ backend: () => online(waiting) });
  await switchedOff.alarm();
  assert.strictEqual(switchedOff.created.length, 0);
});
