'use strict';
// Real DOM Range/Selection behavior catches errors a hand-written UI stub misses.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {JSDOM} = require('jsdom');
const {html, pathname} = JSON.parse(fs.readFileSync(0, 'utf8'));
const dom = new JSDOM(html, {url: 'https://example.test' + pathname, runScripts: 'outside-only', pretendToBeVisual: true});
const {window} = dom;
const {document} = window;
const answer = '첫 문장 <script>literal</script>\n끝 문장';
const row = {model_key: 'DeepSeek-V4.1-Flash', display_name: '시험 모델', family: 'deepseek', configured: true, enabled: true, permitted: true, readiness_state: 'configured_not_verified', permission_state: 'ALLOWED', supports_images: true, policy_scope: 'official_api_outputs', policy_label: '출력 학습 허용'};
const requests = [];
window.fetch = async (url, options) => {
  if (url.endsWith('/catalog')) return {ok: true, json: async () => ({models: [row]})};
  requests.push(JSON.parse(options.body));
  return {ok: true, json: async () => ({task: 'direct', model_key: row.model_key, answer})};
};
window.confirm = () => true;
const byId = id => document.getElementById(id);
function select(range) {
  const selection = window.getSelection();
  selection.removeAllRanges();
  selection.addRange(range);
}
function copy() {
  const written = [];
  const event = new window.Event('copy', {bubbles: true, cancelable: true});
  Object.defineProperty(event, 'clipboardData', {value: {setData(type, text) {written.push({type, text});}}});
  document.dispatchEvent(event);
  return {event, written};
}
function allContents(node) {
  const range = document.createRange();
  range.selectNodeContents(node);
  select(range);
}
(async () => {
  for (const script of document.querySelectorAll('script')) window.eval(script.textContent);
  await new Promise(resolve => setImmediate(resolve));
  byId('model').value = row.model_key;
  byId('model').dispatchEvent(new window.Event('change'));
  byId('question').value = '시험 질문';
  byId('draft-form').dispatchEvent(new window.Event('submit', {cancelable: true, bubbles: true}));
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(requests.length, 1, pathname);
  const region = byId('result-answer');
  assert.ok(region, 'an independent answer body exists');
  assert.equal(region.querySelector('script'), null, 'untrusted output remains literal text');
  assert.equal(window.literal, undefined);
  assert.equal(region.textContent, answer);
  assert.ok(!region.textContent.includes('사용 모델:'));
  // Android's Select All may select the whole page: clip it to the answer.
  allContents(document.body);
  let result = copy();
  assert.equal(result.event.defaultPrevented, true);
  assert.deepEqual(result.written, [{type: 'text/plain', text: answer}]);
  const text = region.querySelector('p').firstChild;
  let range = document.createRange();
  range.setStart(text, 2); range.setEnd(text, 8); select(range);
  result = copy();
  assert.deepEqual(result.written, [{type: 'text/plain', text: answer.slice(2, 8)}], 'partial selection remains partial');
  // Selections spanning metadata and part of the answer only copy their overlap.
  range = document.createRange();
  range.setStart(byId('result').querySelector('h2').firstChild, 0); range.setEnd(text, 8); select(range);
  result = copy();
  assert.deepEqual(result.written, [{type: 'text/plain', text: answer.slice(0, 8)}]);
  range = document.createRange();
  range.setStart(text, 5); range.setEnd(byId('result').lastElementChild.firstChild, 4); select(range);
  result = copy();
  assert.deepEqual(result.written, [{type: 'text/plain', text: answer.slice(5)}]);
  // A field's own copy action must keep its ordinary browser behavior.
  const question = byId('question');
  question.value = '직접 복사할 질문'; question.focus(); question.select();
  allContents(document.body);
  result = copy();
  assert.equal(result.event.defaultPrevented, false);
  assert.deepEqual(result.written, []);
  question.blur();
  const outside = document.createElement('div');outside.contentEditable = 'true';outside.setAttribute('contenteditable', 'true');outside.tabIndex = 0;outside.textContent = '편집하는 원문';document.body.append(outside);outside.focus();
  result = copy();assert.equal(result.event.defaultPrevented, false);outside.setAttribute('contenteditable', 'plaintext-only');
  result = copy();assert.equal(result.event.defaultPrevented, false);outside.blur();outside.remove();
  // Ctrl/Cmd+A inside an answer scopes the browser selection to that answer.
  region.focus();
  const selectAll = new window.KeyboardEvent('keydown', {key: 'a', ctrlKey: true, bubbles: true, cancelable: true});
  region.dispatchEvent(selectAll);
  assert.equal(selectAll.defaultPrevented, true);
  assert.equal(window.getSelection().toString(), answer);
  result = copy();assert.deepEqual(result.written, [{type: 'text/plain', text: answer}]);
  // A model/task change invalidates even a still-highlighted previous answer.
  window.eval('clearHistory()');
  result = copy();
  assert.equal(result.event.defaultPrevented, true);
  assert.deepEqual(result.written, [], 'invalidated answers cannot be recopied');
  dom.window.close();
})().catch(error => {console.error(error);dom.window.close();process.exitCode = 1;});
