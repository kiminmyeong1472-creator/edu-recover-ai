const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const script = fs.readFileSync(0, 'utf8');
const nodes = new Map();
function node(key) {
  if (!nodes.has(key)) nodes.set(key, {textContent: '', innerHTML: '', value: ''});
  return nodes.get(key);
}

const segment = {segment_id: 'question', kind: 'PRACTICE', title: '현재 문제',
  text: '무엇을 구하나요?', choices: ['A', 'B']};
let state = {lesson: [segment], state: {current_segment_id: 'question', playback: 'PAUSED'}};
const context = vm.createContext({
  document: {querySelector: node},
  fetch: async () => ({ok: true, json: async () => state}),
  setInterval: () => 1,
});

async function run() {
  vm.runInContext(script, context);
  await new Promise(setImmediate);
  await new Promise(setImmediate);
  assert.equal(node('#title').textContent, '현재 문제');
  assert.match(node('#choices').innerHTML, /data-choice='A'/);

  state = {lesson: [segment], state: {current_segment_id: 'question', playback: 'STOPPED'}};
  await vm.runInContext('load()', context);
  assert.equal(node('#title').textContent, '수업 중지');
  assert.equal(node('#text').textContent, '');
  assert.equal(node('#choices').innerHTML, '', 'stale answer buttons must disappear');

  state = {lesson: [segment], state: {current_segment_id: 'question', playback: 'PAUSED'}};
  await vm.runInContext('load()', context);
  assert.equal(node('#title').textContent, '현재 문제');
  assert.match(node('#choices').innerHTML, /data-choice='A'/,
    'the current prompt must return when the lesson resumes');
}
run().catch(error => {console.error(error); process.exitCode = 1;});
