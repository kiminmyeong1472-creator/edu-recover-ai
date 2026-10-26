"""Exercise the real editor JavaScript without running submitted code."""

import os
import re
import subprocess
import unittest

from app.services.code_execution_workspace import code_execution_workspace_html


_HARNESS = r"""
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const elements=new Map(),requests=[],timers=new Map(),blobs=[];let timerId=0;
let configured=process.env.CONFIGURED!=='false',runReply={id:'run-1',status:'running'},statusReply={id:'run-1',status:'running'};
let pollFails=false,createFails=false,generationFails=false;let generateReply={model_key:'DeepSeek-V4.1-Flash',route:'learning',execution_state:'not_executed',requires_execution_authorization:true,files:[{path:'main.py',content:'print(7)'}],manifest:[],draft_sha256:'draft-hash'};
function element(id,tag=''){
 if(!elements.has(id))elements.set(id,{value:'',disabled:false,hidden:false,checked:false,files:[],children:[],events:{},textContent:'',className:'',tag,
  addEventListener(type,callback){this.events[type]=callback},append(...children){this.children.push(...children)},replaceChildren(){this.children=[]},click(){if(this.events.click)return this.events.click()}});
 return elements.get(id);
}
for(const id of ['mode','model-route','model','file-list','evaluation-case'])element(id,'select');
element('mode').value='manual';element('model-route').value='learning';element('file-path').value='main.py';element('command').value='python3 main.py';
const modelRows=[{model_key:'DeepSeek-V4.1-Flash',display_name:'DeepSeek-V4.1-Flash',configured:true,enabled:true,permitted:true},
 {model_key:'not-ready',display_name:'Not ready',configured:false,enabled:true,permitted:true}];
const caseRows=[{id:'sum',category:'problem-solving',title:'합계 검증',prompt:'합계 함수를 작성하십시오.',command:'python -m unittest -q',files:[{path:'test_sum.py',content:'assert sum([1,2]) == 3'}]}];
const context=vm.createContext({document:{getElementById:element,createElement:tag=>element(Symbol(),tag)},console,Blob,atob,URL:{createObjectURL(blob){blobs.push(blob);return 'blob:test'},revokeObjectURL(){}},
 setTimeout(callback,delay){const id=++timerId;timers.set(id,callback);return id},clearTimeout(id){timers.delete(id)},
 fetch:async(url,options={})=>{
  requests.push({url,options,body:options.body?JSON.parse(options.body):null});
  let data,ok=true,status=200;
  if(url==='/owner/code-workspace/status')data={configured,external_generation:{enabled:true},message:configured?'분리된 환경 연결됨':'분리된 실행 환경 설정 필요'};
  else if(url==='/owner/open-models/learning/catalog')data={models:modelRows};
  else if(url==='/owner/open-models/chutes/catalog')data={models:[{model_key:'DeepSeek-V3.2',display_name:'DeepSeek-V3.2 · Chutes',configured:true,enabled:true,permitted:true}]};
  else if(url==='/owner/code-workspace/generate'){if(generationFails){ok=false;status=502;data={detail:{message:'초안 요청 실패'}}}else data=generateReply;}
  else if(url==='/owner/code-workspace/evaluation-cases')data={cases:caseRows};
  else if(url==='/owner/code-workspace/runs'&&options.method==='POST'){if(createFails){ok=false;status=503;data={detail:{message:'실행 환경을 사용할 수 없습니다.'}}}else data=runReply;}
  else if(url==='/owner/code-workspace/runs')data={runs:[{id:'run-1',status:'interrupted',created_at:1792224000,model_key:'DeepSeek-V4.1-Flash',evaluation_case:'sum'}]};
  else if(url.endsWith('/cancel'))data={id:'run-1',status:'cancelled'};
  else if(url==='/owner/code-workspace/runs/run-1'){if(pollFails)throw new Error('연결 중단');data=statusReply;}
  else throw new Error('Unexpected URL '+url);
  return {ok,status,json:async()=>data};
 }});
function run(code){return vm.runInContext(code,context)}
function event(id,type='click',arg){return element(id).events[type](arg)}
function edits(id,value){element(id).value=value;return event(id,'input')}
function runRequests(){return requests.filter(item=>item.url==='/owner/code-workspace/runs'&&item.options.method==='POST')}
async function tick(){const entry=timers.entries().next().value;assert.ok(entry,'expected a scheduled poll');timers.delete(entry[0]);await entry[1]()}
(async()=>{vm.runInContext(fs.readFileSync(0,'utf8'),context);await new Promise(resolve=>setImmediate(resolve));__TEST__})().catch(error=>{console.error(error);process.exitCode=1});
"""


class CodeExecutionWorkspaceTests(unittest.TestCase):
    def run_workspace(self, assertions, *, configured=True):
        script = re.search(r"<script>(.*?)</script>", code_execution_workspace_html(), re.S).group(1)
        result = subprocess.run(
            ["node", "-e", _HARNESS.replace("__TEST__", assertions)],
            input=script,
            capture_output=True,
            text=True,
            env=dict(os.environ, CONFIGURED=str(configured).lower()),
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_external_draft_needs_transmission_approval_then_fresh_execution_approval(self):
        self.run_workspace(r"""
element('mode').value='generate';event('mode','change');element('model').value='DeepSeek-V4.1-Flash';event('model','change');
element('prompt').value='7을 출력하십시오.';element('authorize').checked=true;
await event('run');assert.equal(runRequests().length,0);await event('generate-code');
assert.equal(requests.some(item=>item.url.endsWith('/generate')),false);
assert.match(element('draft-status').textContent,/승인/);
element('generate-authorize').checked=true;await event('generate-code');
const calls=requests.filter(item=>item.url.endsWith('/generate'));assert.equal(calls.length,1);
assert.equal(calls[0].body.model_key,'DeepSeek-V4.1-Flash');assert.equal(calls[0].body.route,'learning');
assert.equal('command' in calls[0].body,false);assert.equal(element('command').value,'python3 main.py');
assert.equal(element('code').value,'print(7)');assert.equal(element('mode').value,'manual');
assert.equal(element('authorize').checked,false);assert.equal(element('generate-authorize').checked,false);
assert.equal(runRequests().length,0);await event('run');assert.equal(runRequests().length,0);
element('authorize').checked=true;await event('run');assert.equal(runRequests().length,1);
assert.equal('model_key' in runRequests()[0].body,false);
""")

    def test_draft_failure_preserves_editor_and_does_not_retry(self):
        self.run_workspace(r"""
element('mode').value='generate';event('mode','change');element('model').value='DeepSeek-V4.1-Flash';event('model','change');
edits('code','print(3)');element('prompt').value='고치십시오.';element('generate-authorize').checked=true;generationFails=true;
await event('generate-code');assert.equal(element('code').value,'print(3)');assert.equal(runRequests().length,0);
assert.equal(element('generate-authorize').checked,false);assert.match(element('draft-status').textContent,/실패/);
assert.equal(requests.filter(item=>item.url.endsWith('/generate')).length,1);
""")

    def test_draft_can_be_requested_without_a_configured_execution_vm(self):
        self.run_workspace(r"""
element('mode').value='generate';event('mode','change');element('model').value='DeepSeek-V4.1-Flash';event('model','change');
element('prompt').value='초안만 작성하십시오.';element('generate-authorize').checked=true;await event('generate-code');
assert.equal(requests.filter(item=>item.url.endsWith('/generate')).length,1);
assert.equal(element('run').disabled,true);assert.equal(runRequests().length,0);
""", configured=False)

    def test_feature_disabled_and_edits_clear_external_transmission_approval(self):
        self.run_workspace(r"""
element('mode').value='generate';event('mode','change');element('model').value='DeepSeek-V4.1-Flash';event('model','change');
element('generate-authorize').checked=true;edits('code','print(5)');assert.equal(element('generate-authorize').checked,false);
element('generate-authorize').checked=true;event('model-route','change');assert.equal(element('generate-authorize').checked,false);
run('generationEnabled=false');await event('generate-code');assert.equal(requests.some(item=>item.url.endsWith('/generate')),false);
""")

    def test_manual_execution_requires_explicit_review_and_has_no_model_call(self):
        self.run_workspace(r"""
assert.equal(runRequests().length,0);await event('run');assert.equal(runRequests().length,0);
assert.match(element('run-status').textContent,/실행 승인/);
element('authorize').checked=true;await event('run');await event('run');
assert.equal(runRequests().length,1);const body=runRequests()[0].body;
assert.equal(body.explicit_authorization,true);assert.equal(body.command,'python3 main.py');
assert.equal(body.files[0].path,'main.py');assert.equal('model_key' in body,false);
assert.equal(element('cancel').disabled,false);assert.equal(element('run').disabled,true);
statusReply={id:'run-1',status:'succeeded',stdout:'2\n',stderr:'',exit_code:0};await tick();
assert.equal(element('stdout').textContent,'2\n');assert.equal(element('exit-code').textContent,'0');
assert.equal(element('run').disabled,false);assert.equal(element('cancel').disabled,true);
""")

    def test_assisted_execution_uses_one_explicit_model_and_keeps_untrusted_output_as_text(self):
        self.run_workspace(r"""
element('mode').value='assisted';event('mode','change');element('authorize').checked=true;
element('prompt').value='합계 코드를 작성하십시오.';await event('run');assert.equal(runRequests().length,0);
element('model').value='not-ready';event('model','change');element('authorize').checked=true;await event('run');assert.equal(runRequests().length,0);
element('model').value='DeepSeek-V4.1-Flash';event('model','change');element('authorize').checked=true;
runReply={id:'run-1',status:'completed',model_key:'DeepSeek-V4.1-Flash',result:{answer:'<script>attack()</script>',stdout:'<img src=x onerror=attack()>',exit_code:0,files:[{path:'<b>evil.py</b>',content:'<script>file()</script>'}],steps:[{tool:'run_command',result:'<b>untrusted</b>'}]}};
await event('run');assert.equal(runRequests().length,1);
assert.equal(runRequests()[0].body.model_key,'DeepSeek-V4.1-Flash');
assert.equal(element('answer').textContent,'<script>attack()</script>');
assert.equal(element('stdout').textContent,'<img src=x onerror=attack()>');
assert.equal(element('artifacts').children[0].children[0].textContent,'<b>evil.py</b>');
assert.equal(element('artifacts').children[0].children[2].textContent,'<script>file()</script>');
assert.equal(element('steps-section').hidden,false);
""")

    def test_file_edits_revoke_review_and_paths_cannot_escape_workspace(self):
        self.run_workspace(r"""
element('authorize').checked=true;edits('code','print(42)');assert.equal(element('authorize').checked,false);
for(const path of ['../secret','/etc/passwd','C:/secret','dir\\secret','a/../b','a//b','a/./b','x\u0000y']){
 edits('file-path',path);element('authorize').checked=true;await event('run');assert.equal(runRequests().length,0);
}
edits('file-path','src/main.py');element('authorize').checked=true;await event('run');
assert.equal(runRequests().length,1);assert.equal(runRequests()[0].body.files[0].path,'src/main.py');
assert.equal(runRequests()[0].body.files[0].content,'print(42)');
""")

    def test_file_selection_saves_each_edit_and_rejects_duplicate_paths(self):
        self.run_workspace(r"""
edits('code','first body');event('add-file');assert.equal(element('file-list').children.length,2);
edits('file-path','second.py');edits('code','second body');element('file-list').value='0';event('file-list','change');
assert.equal(element('code').value,'first body');edits('file-path','second.py');element('authorize').checked=true;
await event('run');assert.equal(runRequests().length,0);assert.match(element('run-status').textContent,/중복/);
""")

    def test_evaluation_load_is_explicit_and_never_invents_a_score(self):
        self.run_workspace(r"""
assert.equal(runRequests().length,0);assert.equal(run('selectedCase'),null);
element('evaluation-case').value='sum';event('evaluation-case','change');assert.equal(element('file-path').value,'main.py');
event('load-case');assert.equal(element('file-path').value,'test_sum.py');assert.equal(runRequests().length,0);
assert.equal(element('authorize').checked,false);element('authorize').checked=true;
runReply={id:'run-1',status:'succeeded',evaluation_case:'sum',evaluation:{category:'problem-solving',passed:true},exit_code:0};
await event('run');assert.equal(runRequests()[0].body.evaluation_case,'sum');
assert.match(element('score-problem-solving').textContent,/실행 확인됨 · 통과/);
assert.equal(element('score-science').textContent,'');
edits('code','changed solution');assert.equal(run('selectedCase'),'sum');event('clear-case');assert.equal(run('selectedCase'),null);
""")

    def test_failed_poll_keeps_cancellation_and_prevents_duplicate_execution(self):
        self.run_workspace(r"""
element('authorize').checked=true;await event('run');pollFails=true;await tick();
assert.equal(element('run').disabled,true);assert.equal(element('cancel').disabled,false);
assert.match(element('run-status').textContent,/실행 중 여부가 확인되지/);
await event('run');assert.equal(runRequests().length,1);await event('cancel');
assert.equal(element('run-badge').textContent,'실행 중지됨');assert.equal(element('run').disabled,false);
""")

    def test_unconfigured_runtime_keeps_editor_available_without_fake_execution(self):
        self.run_workspace(r"""
assert.equal(element('run').disabled,true);assert.equal(element('code').disabled,false);
assert.match(element('service-status').textContent,/설정 필요/);
element('authorize').checked=true;await event('run');assert.equal(runRequests().length,0);
""", configured=False)

    def test_import_is_atomic_and_disallows_binary_or_duplicate_names(self):
        self.run_workspace(r"""
const target={value:'upload',files:[{name:'new.py',size:10,text:async()=> 'print(2)'},{name:'main.py',size:2,text:async()=> 'x'}]};
await event('import-files','change',{target});assert.equal(element('file-list').children.length,1);
target.files=[{name:'binary.txt',size:2,text:async()=> '\u0000'}];await event('import-files','change',{target});
assert.equal(element('file-list').children.length,1);
target.files=[{name:'new.py',size:10,text:async()=> 'print(2)'}];await event('import-files','change',{target});
assert.equal(element('file-list').children.length,2);assert.equal(element('file-path').value,'new.py');
assert.equal(runRequests().length,0);
""")

    def test_page_keeps_published_benchmarks_separate_from_local_cases(self):
        html = code_execution_workspace_html()
        for name in ("LiveCodeBench", "SWE-bench", "Terminal-Bench", "SciCode"):
            self.assertIn(name, html)
        self.assertEqual(html.count('class="category-score">미실시'), 4)
        self.assertIn("공개 성능 지표의 공식 시험이나 점수로 표시하지 않습니다", html)
        self.assertIn("자동으로 수정하거나 배포하지 않습니다", html)
        self.assertNotIn("innerHTML", html)
        self.assertNotIn("eval(", html)
        self.assertNotIn("new Function", html)

    def test_actual_api_category_and_evaluation_status_shapes_are_supported(self):
        self.run_workspace(r"""
caseRows[0].category='problem';element('evaluation-case').value='sum';event('load-case');
edits('code','assert sum([1,2]) == 3');element('authorize').checked=true;
runReply={id:'run-1',status:'completed',evaluation_case:'sum',evaluation:{case_id:'sum',status:'passed',official_benchmark_score:null},elapsed_seconds:2.31,cost:null,cost_state:'제공자 실제 비용 미확인'};
await event('run');assert.equal(runRequests()[0].body.evaluation_case,'sum');
assert.match(element('score-problem-solving').textContent,/통과/);assert.equal(element('elapsed').textContent,'2.31초');
assert.equal(element('cost').textContent,'제공자 실제 비용 미확인');
""")

    def test_saved_history_is_read_only_and_interrupted_runs_do_not_poll_forever(self):
        self.run_workspace(r"""
element('run-history').value='run-1';statusReply={id:'run-1',status:'interrupted',message:'서버가 재시작되었습니다.'};
await event('view-history');assert.equal(runRequests().length,0);assert.equal(element('run').disabled,false);
assert.equal(element('run-badge').textContent,'서버 재시작으로 중단됨');assert.equal(timers.size,0);
assert.match(element('history-status').textContent,/새 실행 요청은 보내지 않았습니다/);
""")

    def test_binary_artifact_download_preserves_bytes_and_oversize_metadata_has_no_download(self):
        self.run_workspace(r"""
element('authorize').checked=true;
runReply={id:'run-1',status:'completed',files:[{path:'image.png',content:'AAECA/8=',encoding:'base64',bytes:5,sha256:'abc'},
 {path:'large.zip',bytes:9999999,exported:false,reason:'export limit'}]};
await event('run');const result=element('artifacts').children;
assert.match(result[0].children[2].textContent,/바이너리/);result[0].children[3].click();
assert.deepEqual([...new Uint8Array(await blobs[0].arrayBuffer())],[0,1,2,3,255]);
assert.match(result[1].children[1].textContent,/9999999바이트/);
assert.equal(result[1].children.some(item=>item.tag==='button'),false);
assert.match(result[1].children[2].textContent,/export limit/);
""")

    def test_nested_backend_configuration_error_is_shown_without_fake_result(self):
        self.run_workspace(r"""
createFails=true;element('authorize').checked=true;await event('run');
assert.equal(runRequests().length,1);assert.equal(element('run').disabled,false);
assert.match(element('run-status').textContent,/실행 환경을 사용할 수 없습니다/);
assert.equal(element('exit-code').textContent,'');
""")

    def test_route_switch_clears_prior_model_and_never_falls_back(self):
        self.run_workspace(r"""
element('mode').value='assisted';event('mode','change');element('model').value='DeepSeek-V4.1-Flash';event('model','change');
element('model-route').value='chutes';event('model-route','change');await new Promise(resolve=>setImmediate(resolve));
assert.equal(element('model').value,'');assert.equal(element('run').disabled,true);
element('model').value='DeepSeek-V3.2';event('model','change');element('prompt').value='코드를 작성하십시오.';element('authorize').checked=true;
await event('run');assert.equal(runRequests().length,1);assert.equal(runRequests()[0].body.route,'chutes');
assert.equal(runRequests()[0].body.model_key,'DeepSeek-V3.2');
""")

    def test_text_only_completion_is_never_labelled_as_executed_code(self):
        self.run_workspace(r"""
element('authorize').checked=true;runReply={id:'run-1',status:'completed',execution_state:'not_executed',checks_state:'not_run',answer:'print(2)'};
await event('run');assert.equal(element('run-badge').textContent,'작업 종료 · 코드 실행 없음');
assert.equal(element('execution-state').textContent,'실제 명령 실행 없음');assert.equal(element('checks-state').textContent,'미실시');
""")


if __name__ == "__main__":
    unittest.main()
