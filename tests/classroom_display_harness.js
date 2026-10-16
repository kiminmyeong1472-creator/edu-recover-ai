const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

// Run the real display script, including refresh and focus controls, without
// starting a browser or calling a model or production service.
const fullScript = fs.readFileSync(0, 'utf8');
const script = fullScript.split("if(DISPLAY_MODE!=='ROBOT')q('#caps')")[0];
const mode = process.argv[2];
assert.ok(['BOARD', 'ROBOT'].includes(mode));
assert.ok(script.includes('function toggleDisplayFocus'));
const nodes = new Map();
function node(key) {
  if (!nodes.has(key)) nodes.set(key, {
    innerHTML: '', textContent: '', style: {display: 'none'}, attributes: {},
    setAttribute(name, value) {this.attributes[name] = value;},
  });
  return nodes.get(key);
}
let cancelled = 0, spoken = 0, networkCalls = 0, boardResizes = 0;
const classes = new Set(mode === 'ROBOT' ? ['display-focus', 'robot-screen'] : []);
const speech = {cancel() {cancelled++;}, speak() {spoken++;}};
const context = vm.createContext({
  window: {speechSynthesis: speech}, speechSynthesis: speech,
  SpeechSynthesisUtterance: class {},
  location: {origin: 'https://display.example'}, URL, Promise,
  document: {
    querySelector: selector => selector === '#vid' || selector === '#youtubePlayer' ? null : node(selector),
    body: {classList: {toggle(name, enabled) {enabled ? classes.add(name) : classes.delete(name);}}},
    documentElement: {requestFullscreen() {}},
  },
  fetch: async () => {networkCalls++; return {ok: false, text: async () => 'publication withdrawn'};},
  setInterval() {}, clearInterval() {},
  resizeBoard() {boardResizes++;},
});

async function run() {
  vm.runInContext(script + '\nconst sharedBoard={size:resizeBoard};', context);
  vm.runInContext(`S={lesson_digest:'reviewed-v1',lesson:[{
    segment_id:'scene',kind:'DEMONSTRATE',title:'현재 장면',text:'기존 설명',
    conditions:{'함수':'x³'},logic_links:[{premise:'검토한 전제',conclusion:'검토한 결론',
      condition_keys:['함수'],rule:'검토한 추론'}]}],
    state:{current_segment_id:'scene',playback:'PAUSED',play_command_revision:0}};render();`, context);
  assert.equal(node('#title').textContent, '현재 장면');
  assert.match(node('#structure').innerHTML, /<svg/);
  assert.match(node('#structure').innerHTML, /검토한 추론/);
  assert.equal(node('#structure').innerHTML.includes("class='card' open"), mode === 'ROBOT');

  if (mode === 'BOARD') {
    vm.runInContext('toggleDisplayFocus()', context);
    assert.ok(classes.has('display-focus'));
    assert.equal(node('#focusDisplay').textContent, '교사 제어 보기');
    assert.equal(node('#focusDisplay').attributes['aria-pressed'], 'true');
    assert.match(node('#structure').innerHTML, /class='card' open/);
    assert.equal(boardResizes, 1);
    vm.runInContext('toggleDisplayFocus()', context);
    assert.ok(!classes.has('display-focus'));
    assert.equal(node('#focusDisplay').attributes['aria-pressed'], 'false');
  } else {
    await vm.runInContext('loadResults()', context);
    assert.equal(networkCalls, 0, 'body display must not request student results');
  }

  vm.runInContext("S.lesson_digest='reviewed-v2';S.lesson[0].text='새로 검토한 설명';render();", context);
  assert.equal(node('#text').textContent, '새로 검토한 설명', 'a changed reviewed lesson must redraw the same segment');
  vm.runInContext("S.state.playback='PLAYING';render();", context);
  assert.equal(spoken, 1);
  const beforeStop = cancelled;
  vm.runInContext("S.state.playback='STOPPED';render();", context);
  assert.ok(cancelled > beforeStop, 'teacher STOP must cancel speech on every display');

  const beforeHold = cancelled;
  node('#whiteWrap').style.display = 'block';
  await vm.runInContext('refresh()', context);
  assert.ok(cancelled > beforeHold);
  assert.equal(node('#title').textContent, '자료 제공 대기');
  assert.equal(node('#text').textContent, '교사 검증 또는 연결 상태를 확인해 주세요.');
  assert.equal(node('#media').innerHTML, '');
  assert.equal(node('#structure').innerHTML, '');
  assert.equal(node('#choices').innerHTML, '');
  assert.equal(node('#whiteWrap').style.display, 'none', 'withdrawal must also hide the previous board strokes');
  assert.equal(vm.runInContext('S', context), null);
  console.log(mode + ' display focus, reviewed revision, STOP and withdrawal passed');
}
run().catch(error => {console.error(error); process.exitCode = 1;});
