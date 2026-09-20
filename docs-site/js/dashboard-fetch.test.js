// Run with: node --test docs-site/js/dashboard-fetch.test.js
import assert from "node:assert/strict";
import { once } from "node:events";
import { createServer } from "node:http";
import test from "node:test";
import { fetchJson, fetchText } from "./dashboard-core.js";

async function serve(t, handler) {
  const server = createServer(handler);
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  t.after(() => {
    server.closeAllConnections();
    server.close();
  });
  return `http://127.0.0.1:${server.address().port}`;
}

test("dashboard readers preserve successful JSON and text responses", async (t) => {
  const url = await serve(t, (request, response) => {
    response.end(request.url.startsWith("/status") ? '{"ok":true}' : "activity\n");
  });
  assert.deepEqual(await fetchJson(`${url}/status`), { ok: true });
  assert.equal(await fetchText(`${url}/activity`), "activity\n");
});

test("activity read stops when the audit endpoint never sends headers", async (t) => {
  const url = await serve(t, () => {});
  await assert.rejects(fetchText(url, { timeoutMs: 50 }), { name: "TimeoutError" });
});

test("activity timeout also covers a response body that never finishes", async (t) => {
  let sentHeaders = false;
  const url = await serve(t, (_request, response) => {
    response.writeHead(200, { "Content-Type": "text/plain" });
    response.write("unfinished activity");
    sentHeaders = true;
  });
  await assert.rejects(fetchText(url, { timeoutMs: 100 }), { name: "TimeoutError" });
  assert.equal(sentHeaders, true);
});
