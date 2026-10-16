const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

// Execute the actual generation controls, with delayed local promises instead
// of network/provider calls, so races remain observable.
const editor = fs.readFileSync(0, 'utf8');
const start = editor.indexOf('let audiovisualModels=');
const end = editor.indexOf("if(q('#avModel'))loadAudiovisualModels();", start);
assert.ok(start >= 0 && end > start);
const program = editor.slice(start, end);
const selected = {model_key: 'DeepSeek-V3.2', display_name: 'DeepSeek', configured: true, enabled: true, permitted: true};
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => {resolve = yes; reject = no;});
  return {promise, resolve, reject};
}
const tick = () => new Promise(setImmediate);

async function scenario(fail) {
  const inputs = new Map();
  function node(selector) {
    if (!inputs.has(selector)) inputs.set(selector, {value: '', disabled: false, checked: false, textContent: '', innerHTML: ''});
    return inputs.get(selector);
  }
  const originals = {avSource: '  원래 교재  ', avTarget: '  원래 목표  ', avModel: selected.model_key,
                     avSubject: '과학', avSchool: 'MIDDLE', avGrade: '2', materialJson: '교사의 기존 초안'};
  for (const [id, value] of Object.entries(originals)) node('#' + id).value = value;
  node('#avAuthorize').checked = true;
  node('#publishMaterial').disabled = true;
  node('#alreadyDisabled').disabled = true;
  const controls = [...Object.keys(originals).map(id => node('#' + id)), node('#avAuthorize'),
                    node('#avGenerate'), node('#publishMaterial'), node('#alreadyDisabled')];
  node('#avGenerate').closest = () => ({querySelectorAll: () => controls});
  const setup = deferred(), generation = deferred();
  let setupCalls = 0, generationCalls = 0, sent = null, previews = 0;
  const context = vm.createContext({
    Map, Array, JSON, encodeURIComponent,
    q: node, esc: String, materialSession: {session_id: 'local-session', teacher_token: 'local-teacher-token'},
    materialPreviewedSignature: 'old-preview',
    materialPreview() {previews++;},
    ensureMaterialSession() {setupCalls++; return setup.promise;},
    async api(url, options) {
      if (url === '/owner/open-models/catalog') return {models: [selected]};
      if (url.includes('/audiovisual-context?')) return {context_digest: 'context-at-start'};
      assert.ok(url.includes('/structured-materials/generate?'));
      generationCalls++; sent = JSON.parse(options.body);
      return generation.promise;
    },
  });
  vm.runInContext(program, context);
  await vm.runInContext('loadAudiovisualModels()', context);
  node('#avModel').value = selected.model_key;
  vm.runInContext('refreshAudiovisualModel()', context);
  assert.equal(node('#avGenerate').disabled, false);
  const first = vm.runInContext('generateAudiovisualMaterial()', context);
  assert.equal(setupCalls, 1);
  assert.ok(controls.every(control => control.disabled), 'all material editing controls must be locked');
  await vm.runInContext('generateAudiovisualMaterial()', context);
  assert.equal(setupCalls, 1, 'a second click must not start another request');
  await vm.runInContext('loadAudiovisualModels()', context);
  vm.runInContext('refreshAudiovisualModel()', context);
  assert.equal(node('#avGenerate').disabled, true, 'model refresh must not unlock an in-flight call');
  // Programmatic changes deliberately bypass disabled inputs to test that the
  // request uses its original snapshot, not later reads after an await.
  for (const id of ['avSource', 'avTarget', 'avSubject', 'avSchool', 'avGrade']) node('#' + id).value = 'changed-during-await';
  setup.resolve();
  await tick();
  assert.equal(generationCalls, 1);
  assert.deepEqual(sent, {model_key: selected.model_key, explicit_authorization: true,
    source_text: '원래 교재', target: '원래 목표', subject: '과학', school_level: 'MIDDLE', grade: '2',
    expected_digest: 'context-at-start'});
  await vm.runInContext('generateAudiovisualMaterial()', context);
  assert.equal(generationCalls, 1, 'a second click during inference must also be ignored');
  if (fail) generation.reject(new Error('local simulated failure'));
  else generation.resolve({bundle: {revision_id: 'generated-draft'}, publication_issues: []});
  await first;
  assert.equal(node('#avGenerate').disabled, false);
  assert.equal(vm.runInContext('audiovisualBusy', context), false);
  assert.equal(node('#alreadyDisabled').disabled, true, 'previously disabled controls must stay disabled');
  for (const id of Object.keys(originals)) assert.equal(node('#' + id).disabled, false);
  assert.equal(node('#avAuthorize').disabled, false);
  assert.equal(node('#publishMaterial').disabled, fail, 'publish is enabled only after a generated draft arrives');
  if (fail) {
    assert.equal(node('#materialJson').value, originals.materialJson);
    assert.equal(previews, 0);
    assert.match(node('#avGenerationStatus').textContent, /초안 생성 실패/);
  } else {
    assert.equal(JSON.parse(node('#materialJson').value).revision_id, 'generated-draft');
    assert.equal(previews, 1);
    assert.match(node('#avGenerationStatus').textContent, /아직 공개되지 않았습니다/);
  }
}
async function run() {
  await scenario(false);
  await scenario(true);
  console.log('audiovisual editor duplicate-call guard, locking, refresh, snapshots and recovery passed');
}
run().catch(error => {console.error(error); process.exitCode = 1;});
