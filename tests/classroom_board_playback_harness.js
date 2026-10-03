const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

// The Python test passes the actual rendered board script. Evaluate the playback
// portion against a small DOM and the documented YouTube Player interface.
const fullScript = fs.readFileSync(0, 'utf8');
const script = fullScript.split('async function cmd(a)')[0];
assert.ok(script.includes('function setupYouTubePlayer'));

const nodes = new Map();
function node(key) {
  if (!nodes.has(key)) nodes.set(key, {innerHTML: '', textContent: '', style: {display: 'none'}});
  return nodes.get(key);
}
const iframe = {id: 'youtubePlayer'};
const players = [];
const intervals = new Map();
let nextInterval = 0;
let spoken = 0;
const yt = {Player: class {
  constructor(frame, options) {
    assert.equal(frame, iframe);
    this.calls = [];
    this.currentTime = 0;
    this.events = options.events;
    players.push(this);
    setImmediate(() => this.events.onReady());
  }
  playVideo() {this.calls.push('play');}
  pauseVideo() {this.calls.push('pause');}
  stopVideo() {this.calls.push('stop');}
  destroy() {this.calls.push('destroy');}
  getCurrentTime() {return this.currentTime;}
}};
const window = {
  YT: yt,
  speechSynthesis: {cancel() {}, speak() {spoken++;}},
};
const context = vm.createContext({
  window, speechSynthesis: window.speechSynthesis,
  location: {origin: 'https://board.example'}, URL, Promise,
  document: {
    querySelector: selector => {
      if (selector === '#youtubePlayer') {
        return node('#media').innerHTML.includes("id='youtubePlayer'") ? iframe : null;
      }
      if (selector === '#vid') return null;
      return node(selector);
    },
    createElement: () => ({src: '', onerror: null}),
    head: {appendChild() {throw new Error('API already supplied');}},
  },
  setInterval: cb => {const id = ++nextInterval; intervals.set(id, cb); return id;},
  clearInterval: id => intervals.delete(id),
  SpeechSynthesisUtterance: class {},
});

async function run() {
  vm.runInContext(script, context);
  vm.runInContext(`S={lesson:[{segment_id:'video',kind:'VIDEO',title:'수업',text:'영상 질문',
    media_type:'video/mp4',media_url:'https://youtu.be/M7lc1UVf-VE',pause_at_sec:8}],
    state:{current_segment_id:'video',playback:'PLAYING',play_command_revision:1}};render();`, context);
  await new Promise(setImmediate);
  await new Promise(setImmediate);
  assert.match(node('#media').innerHTML, /enablejsapi=1/);
  assert.match(node('#media').innerHTML, /origin=https%3A%2F%2Fboard\.example/);
  assert.deepEqual(players[0].calls, ['play']);
  assert.equal(spoken, 0, 'YouTube video must not be replaced by TTS');

  vm.runInContext("S.state.playback='PAUSED';render();", context);
  assert.equal(players[0].calls.at(-1), 'pause');
  vm.runInContext("S.state.playback='PLAYING';render();", context);
  assert.equal(players[0].calls.at(-1), 'play');
  players[0].currentTime = 8;
  for (const callback of intervals.values()) callback();
  assert.equal(players[0].calls.at(-1), 'pause');
  assert.match(node('#choices').innerHTML, /영상 질문/);
  const pausedCallCount = players[0].calls.length;
  vm.runInContext("S.state.last_command_at='unrelated-command';render();", context);
  assert.equal(players[0].calls.length, pausedCallCount, 'an unrelated command must not restart the video');
  vm.runInContext('S.state.play_command_revision=2;render();', context);
  assert.equal(players[0].calls.at(-1), 'play', 'a repeated PLAY command must resume a timed pause');
  const resumedCallCount = players[0].calls.length;
  for (const callback of intervals.values()) callback();
  assert.equal(players[0].calls.length, resumedCallCount, 'the same timed pause must not fire again');

  players[0].events.onAutoplayBlocked();
  assert.match(node('#playbackHelp').textContent, /자동 재생/);
  assert.equal(node('#boardPlayFallback').style.display, 'inline-block');
  vm.runInContext('resumeBoardPlayback()', context);
  assert.equal(node('#boardPlayFallback').style.display, 'none');

  vm.runInContext("S.state.playback='STOPPED';render();", context);
  assert.equal(players[0].calls.at(-1), 'stop');
  vm.runInContext(`S.lesson.push({segment_id:'image',kind:'EXPLAIN',title:'그림',text:'새 단계',
    media_type:'image/png',media_url:'https://board.example/image.png'});
    S.state.current_segment_id='image';render();`, context);
  assert.ok(players[0].calls.includes('destroy'));
  assert.equal(intervals.size, 0, 'old video timer must be removed');
  console.log('classroom YouTube playback, pause, stop, timed question, and cleanup passed');
}
run().catch(error => {console.error(error); process.exitCode = 1;});
