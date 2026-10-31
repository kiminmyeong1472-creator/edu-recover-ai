"use strict";

// Verify that an expired owner session cannot become a false successful job.
// Uses only Node's built-in modules and the actual HTML response helper.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const html = fs.readFileSync(
  path.join(__dirname, "..", "app", "services", "media_workspace.html"),
  "utf8",
);
const helperStart = html.indexOf("function loginNeeded()");
const helperEnd = html.indexOf("function refreshSubmit()", helperStart);
assert.ok(helperStart >= 0 && helperEnd > helperStart, "API helper must exist");
const helper = html.slice(helperStart, helperEnd);
const notice = { hidden: true };
let response;
const context = vm.createContext({
  URL,
  location: { href: "https://edu.test/owner/media" },
  $: (id) => {
    assert.equal(id, "login-needed");
    return notice;
  },
  fetch: async () => response,
});
vm.runInContext(helper, context, { filename: "media_workspace_api.js" });

function makeResponse(overrides = {}) {
  return {
    redirected: false,
    url: "https://edu.test/owner/media/jobs",
    status: 200,
    ok: true,
    headers: { get: () => "application/json; charset=utf-8" },
    json: async () => ({ jobs: [] }),
    ...overrides,
  };
}

async function run() {
  response = makeResponse({
    redirected: true,
    url: "https://edu.test/owner/login",
    headers: { get: () => "text/html" },
  });
  await assert.rejects(
    () => context.api("/owner/media/jobs"),
    /로그인이 필요/,
    "A redirected login page must fail",
  );
  assert.equal(notice.hidden, false, "Show the login link after a redirect");

  notice.hidden = true;
  response = makeResponse({ headers: { get: () => "text/html" } });
  await assert.rejects(
    () => context.api("/owner/media/jobs"),
    /로그인이 필요/,
    "HTML with HTTP 200 must not become successful empty JSON",
  );
  assert.equal(notice.hidden, false, "Show the login link for unexpected HTML");

  notice.hidden = true;
  response = makeResponse({ status: 401, ok: false });
  await assert.rejects(
    () => context.api("/owner/media/jobs"),
    /로그인이 필요/,
    "HTTP 401 must require login even when its body is JSON",
  );
  assert.equal(notice.hidden, false, "Show the login link for HTTP 401");

  response = makeResponse();
  const data = await context.api("/owner/media/jobs");
  assert.deepEqual(data, { jobs: [] });
  assert.equal(notice.hidden, true, "A successful JSON request clears the notice");

  response = makeResponse({
    json: async () => {
      throw new Error("invalid JSON");
    },
  });
  await assert.rejects(
    () => context.api("/owner/media/jobs"),
    /응답을 읽지 못/,
    "Malformed JSON must fail instead of returning an empty object",
  );

  console.log(
    "PASS: media UI responses (5 cases: login redirect, HTML 200, HTTP 401, valid JSON, malformed JSON)",
  );
}

run().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
