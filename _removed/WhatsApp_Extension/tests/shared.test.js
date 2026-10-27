"use strict";
// Run with:  node --test WhatsApp_Extension/tests
const test = require("node:test");
const assert = require("node:assert");
const LS = require("../shared.js");

test("parseMessageId reads the chat out of a stored message id", () => {
  assert.deepStrictEqual(LS.parseMessageId("false_13055550100@c.us_3EB0ABC123"), {
    fromMe: false, chatWid: "13055550100@c.us", user: "13055550100", server: "c.us",
  });
  assert.strictEqual(LS.parseMessageId("true_13055550100@c.us_3EB0")?.fromMe, true);
  assert.strictEqual(LS.parseMessageId("false_987654321@lid_ABC")?.server, "lid");
  assert.strictEqual(LS.parseMessageId("garbage"), null);
  assert.strictEqual(LS.parseMessageId(""), null);
  assert.strictEqual(LS.parseMessageId(undefined), null);
  assert.strictEqual(LS.parseMessageId("false_nowid_ABC"), null);
});

test("only one-to-one chats count", () => {
  assert.ok(LS.isPrivateChat("c.us"));
  assert.ok(LS.isPrivateChat("lid"));
  assert.ok(!LS.isPrivateChat("g.us"));
  assert.ok(!LS.isPrivateChat("status@broadcast"));
});

test("phoneFromChat gives a number only for plain phone chats", () => {
  assert.strictEqual(LS.phoneFromChat(LS.parseMessageId("false_13055550100@c.us_X")), "13055550100");
  assert.strictEqual(LS.phoneFromChat(LS.parseMessageId("false_13055550100:12@c.us_X")), "13055550100");
  assert.strictEqual(LS.phoneFromChat(LS.parseMessageId("false_987654321@lid_X")), null);
  assert.strictEqual(LS.phoneFromChat(LS.parseMessageId("false_123@c.us_X")), null);   // too short to be a number
  assert.strictEqual(LS.phoneFromChat(null), null);
});

test("stateFromPage", () => {
  assert.strictEqual(LS.stateFromPage({ loggedIn: true, hasQr: true }), "connected");
  assert.strictEqual(LS.stateFromPage({ loggedIn: false, hasQr: true }), "logged_out");
  assert.strictEqual(LS.stateFromPage({ loggedIn: false, hasQr: false }), "loading");
});

test("randomBetween stays inside the range", () => {
  assert.strictEqual(LS.randomBetween(2000, 6000, () => 0), 2000);
  assert.strictEqual(LS.randomBetween(2000, 6000, () => 1), 6000);
  assert.strictEqual(LS.randomBetween(2000, 6000, () => 0.5), 4000);
});

test("normalizeUrl accepts http(s) addresses only", () => {
  assert.strictEqual(LS.normalizeUrl(" http://localhost:8000/ "), "http://localhost:8000");
  assert.strictEqual(LS.normalizeUrl("https://api.example.com/path?x=1"), "https://api.example.com");
  assert.strictEqual(LS.normalizeUrl("ftp://x.com"), null);
  assert.strictEqual(LS.normalizeUrl("localhost:8000"), null);
  assert.strictEqual(LS.normalizeUrl(""), null);
});

test("capMap keeps the newest entries", () => {
  assert.deepStrictEqual(LS.capMap({ a: 1, b: 2, c: 3 }, 2), { b: 2, c: 3 });
  assert.deepStrictEqual(LS.capMap({ a: 1 }, 5), { a: 1 });
});
