// Run with: node tests/test_provider_ui.js
// Exercises the actual provider-choice code without a browser or an API key.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('app/pilot_page.py', 'utf8');
const code = source.slice(source.indexOf('async function showCrossVerifyPrompt('),
                          source.indexOf('async function refreshAuthoringAssets('));
const elements = new Map();
const element = id => {
  if (!elements.has(id)) elements.set(id, {
    children: [], handlers: {}, hidden: false, disabled: false, textContent: '', value: '',
    set innerHTML(value) { this.children = []; this.value = ''; },
    appendChild(child) { this.children.push(child); if (!this.value) this.value = child.value; },
    addEventListener(event, handler) { this.handlers[event] = handler; },
  });
  return elements.get(id);
};
const calls = [];
let providers = {openai: {verify_ready: true}, gemini: {verify_ready: true}, claude: {verify_ready: false}};
let verificationReply = async () => ({ok: true, text: async () => JSON.stringify({verdict: 'PASS', provider: 'gemini', reasons: []})});
const context = vm.createContext({
  generationInFlight: false, verificationInFlight: false, workspaceRevision: 0,
  document: {getElementById: element, createElement: () => ({})},
  workspace: {items: [{question: 'q1'}, {question: 'q2'}, {question: 'q3'}],
    lastGenerationProvider: 'openai', lastGenerationReceipt: {invocation_id: 'test-invocation', origin_proof: 'test-origin-proof'}},
  fetch: async (path, options) => {
    calls.push({path, options});
    if(path === '/owner/authoring/revise') return {ok: true, json: async () => ({provider: 'openai', invocation_id: 'revised-invocation', origin_proof: 'revised-origin-proof'})};
    if(options?.method === 'POST') return verificationReply();
    return {ok: true, json: async () => ({providers}),
            text: async () => JSON.stringify({verdict: 'PASS', provider: 'gemini', reasons: []})};
  },
  providerLabel: provider => provider,
  persist: () => {},
  showView: view => { context.currentView = view; },
});
vm.runInContext(code, context);
(async () => {
  await context.showCrossVerifyPrompt('openai', 3);
  assert.equal(element('crossVerifyModal').hidden, false);
  assert.deepEqual(element('crossVerifyProvider').children.map(option => option.value), ['gemini']);
  assert.match(element('crossVerifyMessage').textContent, /3문항/);
  element('crossVerifyNo').handlers.click();
  assert.equal(context.workspace.crossVerificationState, 'SKIPPED');
  assert.equal(context.workspace.items.length, 3);
  assert.equal(context.currentView, 'workspace');
  assert.equal(calls.filter(call => call.options?.method === 'POST').length, 0);

  await context.showCrossVerifyPrompt('openai', 3);
  await context.runCrossVerification();
  const posts = calls.filter(call => call.options?.method === 'POST');
  assert.equal(posts.length, 1);
  assert.equal(posts[0].path, '/channel/gemini/verify');
  const payload = JSON.parse(posts[0].options.body);
  assert.equal(payload.problems.length, 3);
  assert.equal(payload.origin_provider, 'openai');
  assert.equal(payload.explicit_authorization, true);
  assert.equal(context.workspace.crossVerificationState, 'PASS');

  providers = {openai: {verify_ready: true}, gemini: {verify_ready: false}, claude: {verify_ready: false}};
  await context.showCrossVerifyPrompt('openai', 3);
  assert.equal(element('crossVerifyYes').disabled, true);
  assert.equal(element('crossVerifyProvider').children.length, 0);
  assert.equal(calls.filter(call => call.options?.method === 'POST').length, 1);

  const invalidate = source.slice(source.indexOf('function invalidateWorkspaceReview('),
                                  source.indexOf('function requestWorkspaceVerification('));
  vm.runInContext(invalidate, context);
  context.workspace.teacherApproved = true;
  context.invalidateWorkspaceReview();
  assert.equal(context.workspace.teacherApproved, false);
  assert.equal(context.workspace.lastCrossVerification, null);
  assert.equal(context.workspace.crossVerificationState, 'EDITED_REVIEW_REQUIRED');
  // Editing rebinds the batch through the owner API before verification.
  providers.gemini.verify_ready = true;
  await context.showCrossVerifyPrompt('openai', 3);
  await context.runCrossVerification();
  const editCalls = calls.filter(call => call.options?.method === 'POST').slice(-2);
  assert.equal(editCalls[0].path, '/owner/authoring/revise');
  assert.equal(editCalls[1].path, '/channel/gemini/verify');
  const editedPayload = JSON.parse(editCalls[1].options.body);
  assert.equal(editedPayload.origin_proof, 'revised-origin-proof');
  assert.equal(editedPayload.origin_invocation_id, 'revised-invocation');
  assert.equal(editedPayload.origin_provider, 'openai');

  // A late PASS must never attach to a different/currently edited batch.
  context.workspace.authoringRevisionRequired = false;
  providers.gemini.verify_ready = true;
  await context.showCrossVerifyPrompt('openai', 3);
  let releaseReply;
  verificationReply = () => new Promise(resolve => { releaseReply = resolve; });
  const pending = context.runCrossVerification();
  context.workspace.items[0].question = 'changed during verification';
  context.invalidateWorkspaceReview();
  releaseReply({ok: true, text: async () => JSON.stringify({verdict: 'PASS', provider: 'gemini', reasons: []})});
  await pending;
  assert.equal(context.workspace.lastCrossVerification, null);
  assert.notEqual(context.workspace.crossVerificationState, 'PASS');
  assert.match(element('crossVerifyResult').textContent, /변경/);
  assert.equal(context.verificationInFlight, false);
  const beforeMissingProof = calls.length;
  context.workspace.lastGenerationReceipt.origin_proof = null;
  await context.runCrossVerification();
  assert.equal(calls.length, beforeMissingProof);
  assert.match(element('crossVerifyResult').textContent, /출처/);
  console.log('Provider UI: selection, full batch, decline, edit rebinding, stale PASS rejection, missing proof block passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });
