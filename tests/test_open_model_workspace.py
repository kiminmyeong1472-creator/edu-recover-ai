"""Run the real workspace JavaScript without contacting model providers."""
import json
import re
import subprocess
import unittest

from app.services.open_model_workspace import _HTML, chutes_workspace_html, learning_workspace_html
from app.services.open_model_catalog import CHUTES_MODEL_CATALOG


_HARNESS = r"""
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const elements=new Map(),requests=[],readers=[],confirmations=[],clipboardWrites=[],fallbackWrites=[],fallbackNodes=[],responseQueue=[];
let allowSwitch=false,readerFailure=false,autoRead=true,clipboardReject=false,execCopySucceeds=true,holdResponse=false,releaseResponse=null,holdClipboard=false,rejectClipboard=null;
let activeElement=null,selectedText='',selectionRanges=[{label:'original selection',cloneRange(){return {...this}}}],focusCalls=[],selectionRestores=0;
const selection={get rangeCount(){return selectionRanges.length},getRangeAt(index){return selectionRanges[index]},
 removeAllRanges(){selectionRanges=[];selectedText='';selectionRestores++},addRange(range){selectionRanges.push(range)}};
const clipboard={writeText:async text=>{clipboardWrites.push(text);if(holdClipboard)await new Promise((resolve,reject)=>{rejectClipboard=()=>reject(new Error('late clipboard rejection'))});if(clipboardReject)throw new Error('clipboard denied')}};
function element(id,tag=''){
 if(!elements.has(id))elements.set(id,{value:'',disabled:false,hidden:false,children:[],dataset:{},style:{},tag,events:{},attrs:{},maxLength:-1,textContent:'',className:'',files:[],parentNode:null,isConnected:true,
  focus(){activeElement=this;focusCalls.push(this)},select(){selectedText=this.value;this.selectionStart=0;this.selectionEnd=this.value.length},
  setSelectionRange(start,end,direction){this.selectionStart=start;this.selectionEnd=end;this.selectionDirection=direction;selectedText=this.value.slice(start,end)},
  remove(){if(this.parentNode)this.parentNode.removeChild(this);this.isConnected=false},
  appendChild(child){this.append(child);return child},removeChild(child){this.children=this.children.filter(node=>node!==child);child.parentNode=null;return child},
  addEventListener(type,handler){this.events[type]=handler},append(...children){for(const child of children){child.parentNode=this;this.children.push(child)}},
  replaceChildren(){this.children=[]},setAttribute(name,value){this.attrs[name]=value},removeAttribute(name){delete this.attrs[name];if(name==='maxlength')this.maxLength=-1},
  querySelectorAll(selector){
   if(id==='draft-form')return [...elements.values()].filter(node=>['input','select','textarea','button'].includes(node.tag));
   if(id==='sources')return this.children.flatMap(child=>child.children).filter(child=>child.dataset.sourceId);
   return [];
  }});
 return elements.get(id);
}
for(const id of ['task','domain','model','question-format'])element(id,'select');
for(const id of ['question-count','attachments'])element(id,'input');
for(const id of ['generate','add-source','new-conversation','voice-input','copy-answer','copy-conversation'])element(id,'button');
element('question','textarea');element('task').value='direct';element('domain').value='education';
element('question-count').value='5';element('question-format').value='short_answer';element('question').value='시험 질문';
const rows=[
 {model_key:'DeepSeek-V4.1-Flash',display_name:'Flash',family:'deepseek',configured:true,enabled:true,permitted:true,readiness_state:'configured_not_verified',permission_state:'ALLOWED',supports_images:true},
 {model_key:'DeepSeek-V4-Pro',display_name:'Pro',family:'deepseek',configured:true,enabled:true,permitted:true,readiness_state:'configured_not_verified',permission_state:'ALLOWED',supports_images:false},
 {model_key:'unconfigured',display_name:'Unconfigured',family:'deepseek',configured:false,enabled:false,permitted:false}
];
class Reader{
 readAsDataURL(file){this.file=file;readers.push(this);if(autoRead)this.complete()}
 complete(){if(readerFailure){this.onerror();return;}this.result='data:application/octet-stream;base64,'+Buffer.from(this.file.contents||'file body').toString('base64');this.onload()}
}
class Recognition{
 constructor(){Recognition.instance=this;this.starts=0;this.stops=0}
 start(){this.starts++;if(this.onstart)this.onstart()}
 stop(){this.stops++;if(this.onend)this.onend()}
}
const document={getElementById:element,createElement:tag=>element(Symbol(),tag),body:element('body','body'),
 get activeElement(){return activeElement},getSelection:()=>selection,
 execCommand(command){assert.equal(command,'copy');fallbackWrites.push(selectedText);fallbackNodes.push({tag:activeElement.tag,readonly:'readonly' in activeElement.attrs||activeElement.readOnly===true,text:activeElement.value});return execCopySucceeds}};
const context=vm.createContext({document,navigator:{clipboard},getSelection:()=>selection,window:{getSelection:()=>selection},
 FileReader:Reader,confirm:message=>{confirmations.push(message);return allowSwitch},
 ...(process.env.TEST_VOICE==='true'?{SpeechRecognition:Recognition}:{}),
 fetch:async(url,options)=>{
  if(url==='/owner/open-models/catalog')return {ok:true,json:async()=>({models:rows})};
  requests.push({url,body:JSON.parse(options.body)});
  if(holdResponse)await new Promise(resolve=>{releaseResponse=resolve});
  const response=responseQueue.length?responseQueue.shift():{task:'direct',model_key:JSON.parse(options.body).model_key,answer:'<script>untrusted</script>'};
  return {ok:!response._error,json:async()=>response};
 },console});
function run(code){return vm.runInContext(code,context)}
function choose(key){element('model').value=key;element('model').events.change()}
async function submit(){await element('draft-form').events.submit({preventDefault(){}})}
function attach(files){element('attachments').files=files;element('attachments').events.change({target:element('attachments')})}
function file(name,contents='file body',size=null){return {name,contents,size:size===null?Buffer.byteLength(contents):size}}
function selectedNames(){return element('attachment-list').children.map(item=>item.children[0].textContent)}
function switchTask(task){element('task').value=task;element('task').events.change()}
function sourceText(text){element('sources').children[0].children[1].value=text}
async function copyAnswer(){await element('copy-answer').events.click()}
async function copyConversation(){await element('copy-conversation').events.click()}
function tutor(answer){return {task:'tutor',model_key:'Proprietary model label',draft:{answer,answer_source_ids:[],follow_up_questions:[],uncertainties:[]}}}
function assertCopyHidden(){assert.equal(element('result-copy-controls').hidden,true)}
function assertNoPageText(text){for(const unwanted of ['EduRecover','사용 모델:','모델 답변 초안','검토 상태:','선택한 모델의 응답','연결 설정 필요'])assert.equal(text.includes(unwanted),false,unwanted)}
function speechResult(transcript,isFinal=false){return Object.assign([{transcript}],{isFinal})}
(async()=>{
 vm.runInContext(fs.readFileSync(0,'utf8'),context);await new Promise(resolve=>setImmediate(resolve));
 __TEST__
})().catch(error=>{console.error(error);process.exitCode=1});
"""


class OpenModelWorkspaceTests(unittest.TestCase):
    def chutes_harness(self):
        rows = [
            dict(row, configured=True, enabled=True, permitted=True,
                 readiness_state="configured_not_verified", permission_state="ALLOWED",
                 supports_images=row["model_key"] in {
                     "Qwen3.6-27B-FP8", "Qwen3.5-397B-A17B-FP8",
                     "Qwen3.8-27B-FP8", "Gemma-4-31B-IT-NVFP4",
                 },
                 required_api_key_env="CHUTES_API_KEY")
            for row in CHUTES_MODEL_CATALOG
        ]
        harness = re.sub(r"const rows=\[.*?\n\];", lambda _: "const rows=" + json.dumps(rows) + ";", _HARNESS, flags=re.S)
        return harness.replace('/owner/open-models/catalog', '/owner/open-models/chutes/catalog')

    def run_workspace(self, assertions, *, voice=False, html=None, harness=None):
        import os

        script = re.search(r"<script>(.*?)</script>", html or _HTML, re.S).group(1)
        result = subprocess.run(
            ["node", "-e", (harness or _HARNESS).replace("__TEST__", assertions)],
            input=script, capture_output=True, text=True,
            env=dict(os.environ, TEST_VOICE=str(voice).lower()),
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_separate_direct_workspace_keeps_existing_page_and_uses_own_api(self):
        html = chutes_workspace_html()
        self.assertIn('href="/owner/open-models/chutes"', _HTML)
        self.assertIn("fetch('/owner/open-models/catalog'", _HTML)
        self.assertIn('href="/owner/open-models"', html)
        self.assertNotIn('<option value="tutor">', html)
        self.assertNotIn('<option value="questions">', html)
        self.assertNotIn('<option value="source_review">', html)
        self.assertIn('CHUTES_API_KEY', html)
        self.assertNotIn('1,024토큰', html)
        self.assertIn('9개 제공판', html)
        self.assertIn('기존 27개 목록과 OpenRouter 연결', html)
        self.assertNotIn('모델의 MIT 라이선스', html)
        harness = _HARNESS.replace('/owner/open-models/catalog', '/owner/open-models/chutes/catalog')
        harness = harness.replace('DeepSeek-V4.1-Flash', 'DeepSeek-V3.2')
        harness = harness.replace('supports_images:true', 'supports_images:false')
        self.run_workspace(r"""
 choose('DeepSeek-V3.2');
 element('voice-input').events.click();const microphone=Recognition.instance;
 const event={resultIndex:0,results:[speechResult('RAG 예제를 설명하십시오.',true)]};
 microphone.onresult(event);microphone.onresult(event);
 attach([file('notes.txt','문서 근거')]);await submit();
 assert.equal(requests.length,1);
 assert.equal(requests[0].url,'/owner/open-models/chutes/call');
 assert.equal(requests[0].body.model_key,'DeepSeek-V3.2');
 assert.equal(requests[0].body.prompt,'시험 질문\nRAG 예제를 설명하십시오.');
 assert.equal(requests[0].body.attachments.length,1);
 assert.equal('max_tokens' in requests[0].body,false);
 assert.ok(element('result').children.some(node=>node.textContent==='<script>untrusted</script>'));
""", voice=True, html=html, harness=harness)

    def test_chutes_nine_variants_show_precision_and_call_only_selected_route(self):
        expected = [
            'DeepSeek-V3.2', 'Qwen3.6-27B-FP8', 'Qwen3.5-397B-A17B-FP8',
            'Qwen3.8-27B-FP8', 'Qwen3-32B-FP8', 'GLM-5.1-FP8',
            'GLM-5.2-NVFP4', 'Gemma-4-31B-IT-NVFP4',
            'DeepSeek-V4-Flash-0731-FP8-DSpark',
        ]
        self.assertEqual([row['model_key'] for row in CHUTES_MODEL_CATALOG], expected)
        html = chutes_workspace_html()
        self.assertNotIn('innerHTML', html)
        self.assertNotIn('25개 모델', html)
        self.run_workspace(r"""
 assert.match(element('chutes-count').textContent,/총 9개.*실제 응답 성공을 뜻하지/);
 assert.equal(element('chutes-model-list').children.length,9);
 const options=element('model').children.flatMap(group=>group.children);
 assert.equal(options.length,9);
 assert.deepEqual(options.map(option=>option.value).sort(),rows.map(row=>row.model_key).sort());
 for(const row of rows){
  choose(row.model_key);
  assert.equal(element('generate').disabled,false);
  assert.ok(element('model-readiness').textContent.includes('수치 정밀도: '+row.precision));
  assert.ok(element('model-readiness').textContent.includes(row.difference));
  assert.ok(element('model-readiness').textContent.includes('공개 목록 확인일: 2026-10-17'));
  assert.match(element('model-readiness').textContent,/실제 응답.*미확인/);
 }
 choose('DeepSeek-V4-Flash-0731-FP8-DSpark');await submit();
 assert.equal(requests.length,1);
 assert.equal(requests[0].url,'/owner/open-models/chutes/call');
 assert.equal(requests[0].body.model_key,'DeepSeek-V4-Flash-0731-FP8-DSpark');
 assert.equal('max_tokens' in requests[0].body,false);
 assert.match(element('domain-help').textContent,/선택한 Chutes 제공판 하나에만/);
""", html=html, harness=self.chutes_harness())

    def test_chutes_catalog_metadata_is_plain_text_and_image_support_is_per_model(self):
        harness = self.chutes_harness().replace(
            'vm.runInContext(fs.readFileSync',
            "rows[0].display_name='<img src=x onerror=alert(1)>';rows[0].difference='<script>variant</script>';vm.runInContext(fs.readFileSync",
        )
        self.run_workspace(r"""
 const first=element('chutes-model-list').children[0];
 assert.equal(first.children[0].textContent,'<img src=x onerror=alert(1)>');
 assert.ok(first.children[1].textContent.includes('<script>variant</script>'));
 choose('DeepSeek-V3.2');attach([file('image.png')]);await submit();
 assert.equal(requests.length,0);
 choose('Qwen3.6-27B-FP8');await submit();
 assert.equal(requests.length,1);assert.equal(requests[0].body.attachments.length,1);
 assert.equal(requests[0].body.model_key,'Qwen3.6-27B-FP8');
""", html=chutes_workspace_html(), harness=harness)

    def test_learning_collection_keeps_permissions_separate_and_calls_only_its_route(self):
        html = learning_workspace_html()
        self.assertIn('href="/owner/open-models/learning"', _HTML)
        self.assertIn('자체 모델 개발·학습 허용 모델 모음', html)
        self.assertNotIn('CHUTES_API_KEY', html)
        self.assertNotIn('id="chutes-count"', html)
        self.assertNotIn('renderChutesCollection(data.models)', html)
        self.assertNotIn('1,024토큰', html)
        self.assertNotIn('<option value="tutor">', html)
        self.assertNotIn('innerHTML', html)
        harness = _HARNESS.replace('/owner/open-models/catalog', '/owner/open-models/learning/catalog')
        harness = harness.replace("display_name:'Flash',", "display_name:'Flash',policy_scope:'official_api_outputs',policy_label:'출력 학습 허용',policy_source:'https://example.test/terms',")
        harness = harness.replace("display_name:'Pro',", "display_name:'Pro',policy_scope:'official_api_outputs',policy_label:'출력 학습 허용',")
        harness = harness.replace("display_name:'Unconfigured',", "display_name:'Unconfigured',policy_scope:'self_hosted_weights',policy_label:'공개 가중치 수정 허용',license:'MIT',policy_source:'https://example.test/LICENSE',")
        self.run_workspace(r"""
 assert.match(element('learning-count').textContent,/총 3개.*공식 API 출력 학습 허용 2개.*공개 가중치 직접 운영 허용 1개/);
 assert.equal(element('learning-model-list').children.length,3);
 assert.match(element('domain-help').textContent,/이 모음에서 호출 준비가 된 모델에만/);
 const last=element('learning-model-list').children[2];
 assert.match(last.children[1].textContent,/공개 가중치 수정 허용.*MIT.*자체 실행 서버 미연결/);
 assert.equal(last.children[2].href,'https://example.test/LICENSE');
 choose('unconfigured');assert.equal(element('generate').disabled,true);await submit();assert.equal(requests.length,0);
 choose('DeepSeek-V4.1-Flash');assert.match(element('model-readiness').textContent,/출력 학습 허용/);
 attach([file('notes.txt','원문')]);await submit();
 assert.equal(requests.length,1);assert.equal(requests[0].url,'/owner/open-models/learning/call');
 assert.equal(requests[0].body.attachments.length,1);assert.equal('max_tokens' in requests[0].body,false);
 assert.equal(element('question').value,'','accepted text is no longer an unsent draft');
 element('question').value='답변 길이를 지정한 새 질문';element('question').events.input();
 element('learning-output-tokens').value='1000000';await submit();
 assert.equal(requests.length,2);assert.equal(requests[1].body.max_tokens,1000000);
""", html=html, harness=harness)

    def test_separate_direct_workspace_blocks_images_without_fallback(self):
        harness = _HARNESS.replace('/owner/open-models/catalog', '/owner/open-models/chutes/catalog')
        harness = harness.replace('DeepSeek-V4.1-Flash', 'DeepSeek-V3.2')
        harness = harness.replace('supports_images:true', 'supports_images:false')
        self.run_workspace(r"""
 choose('DeepSeek-V3.2');attach([file('image.png')]);await submit();
 assert.equal(requests.length,0);
""", html=chutes_workspace_html(), harness=harness)

    def test_direct_provider_output_budget_is_optional_and_has_no_app_ceiling(self):
        harness = _HARNESS.replace('/owner/open-models/catalog', '/owner/open-models/chutes/catalog')
        harness = harness.replace('DeepSeek-V4.1-Flash', 'DeepSeek-V3.2')
        self.run_workspace(r"""
 choose('DeepSeek-V3.2');
 element('chutes-output-tokens').value='1000000';await submit();
 assert.equal(requests.length,1);assert.equal(requests[0].body.max_tokens,1000000);
 for(const invalid of ['-1','1.5','0','1e3']){element('chutes-output-tokens').value=invalid;await submit();assert.equal(requests.length,1);}
""", html=chutes_workspace_html(), harness=harness)

    def test_direct_ui_omits_token_limit_and_keeps_model_output_as_text(self):
        self.assertNotIn('id="max-tokens"', _HTML)
        self.assertNotIn("max_tokens", _HTML)
        self.assertIn("앱에서 답변 토큰 수를 제한하지 않습니다", _HTML)
        self.assertNotIn("innerHTML", _HTML)
        self.run_workspace(r"""
 assert.equal(element('question').maxLength,-1);
 choose('DeepSeek-V4.1-Flash');await submit();
 assert.equal(requests.length,1);assert.equal(requests[0].body.prompt,'시험 질문');
 assert.equal('max_tokens' in requests[0].body,false);
 assert.ok(element('result').children.some(node=>node.textContent==='<script>untrusted</script>'));
 switchTask('questions');assert.equal(element('question').maxLength,4000);
 assert.equal(element('attachments').disabled,true);assert.equal(element('attachment-options').hidden,true);
 assert.equal(element('question-count').disabled,false);
 switchTask('direct');assert.equal(element('question').maxLength,-1);
 assert.equal(element('question-count').disabled,true);assert.equal(element('attachments').disabled,false);
 """)

    def test_copy_toolbar_is_separate_from_answer_and_shared_by_all_workspaces(self):
        for html in [_HTML, chutes_workspace_html(), learning_workspace_html()]:
            self.assertRegex(html, r'id="result-copy-controls"[^>]*hidden')
            self.assertRegex(html, r'<button[^>]*type="button"[^>]*id="copy-answer"')
            self.assertRegex(html, r'<button[^>]*type="button"[^>]*id="copy-conversation"')
            self.assertIn('id="copy-status"', html)
            self.assertNotIn('innerHTML', html)

    def test_copy_answer_and_current_exchange_exclude_all_page_chrome(self):
        self.run_workspace(r"""
 assertCopyHidden();choose('DeepSeek-V4.1-Flash');
 const question='첫 질문 <b>보존</b>\n둘째 줄';const answer='<script>untrusted</script>\n정확한 답변  공백';
 element('question').value=question;
 responseQueue.push({task:'direct',model_key:'Proprietary model label',answer});await submit();
 assert.equal(element('result-copy-controls').hidden,false);
 assert.equal(element('question').value,'');
 element('question').value='응답 이후 새로 쓴 질문';element('question').events.input();
 await copyAnswer();await copyConversation();
 assert.deepEqual(clipboardWrites,[answer,'사용자: '+question+'\n\nAI: '+answer]);
 clipboardWrites.forEach(assertNoPageText);assert.equal(fallbackWrites.length,0);assert.equal(requests.length,1);
 assert.ok(element('result').children.some(node=>node.textContent===answer),'raw HTML stays text, never executes');
 """)

    def test_copy_uses_the_submitted_question_even_if_draft_changes_in_flight(self):
        self.run_workspace(r"""
 choose('DeepSeek-V4.1-Flash');element('question').value='보낸 원래 질문';
 holdResponse=true;const pending=submit();await new Promise(resolve=>setImmediate(resolve));
 element('question').value='전송 뒤 수정한 초안';holdResponse=false;releaseResponse();await pending;
 await copyConversation();
 assert.equal(clipboardWrites[0],'사용자: 보낸 원래 질문\n\nAI: <script>untrusted</script>');
 assert.equal(element('question').value,'전송 뒤 수정한 초안');assert.equal(requests.length,1);
 """)

    def test_tutor_conversation_copies_previous_turns_and_current_raw_answer(self):
        self.run_workspace(r"""
 choose('DeepSeek-V4.1-Flash');switchTask('tutor');
 element('question').value='첫 튜터 질문';responseQueue.push(tutor('첫 튜터 답변'));await submit();
 element('question').value='두 번째 질문';responseQueue.push(tutor('두 번째 원문 답변'));await submit();
 await copyAnswer();await copyConversation();
 assert.equal(clipboardWrites[0],'두 번째 원문 답변');
 assert.equal(clipboardWrites[1],'사용자: 첫 튜터 질문\n\nAI: 첫 튜터 답변\n\n사용자: 두 번째 질문\n\nAI: 두 번째 원문 답변');
 assert.equal(requests.length,2);clipboardWrites.forEach(assertNoPageText);
 assert.equal(requests[1].body.history.length,2,'copy never adds another tutor turn');
 """)

    def test_clipboard_denial_uses_only_temporary_readonly_text_and_restores_focus(self):
        self.run_workspace(r"""
 choose('DeepSeek-V4.1-Flash');await submit();
 const field=element('question');field.value='남겨 둔 초안';field.focus();field.setSelectionRange(1,3,'forward');
 const originalRange=selectionRanges[0];clipboardReject=true;await copyAnswer();
 assert.deepEqual(fallbackWrites,['<script>untrusted</script>']);
 assert.equal(document.body.children.length,0,'temporary textarea must be removed');
 assert.equal(activeElement,field);assert.equal(field.selectionStart,1);assert.equal(field.selectionEnd,3);
 assert.equal(selectionRanges[0].label,originalRange.label);assert.ok(selectionRestores>0);
 assert.deepEqual(fallbackNodes,[{tag:'textarea',readonly:true,text:'<script>untrusted</script>'}]);
 assert.equal(element('copy-status').className.includes('error'),false);
 assert.equal(requests.length,1);
 """)

    def test_unsupported_clipboard_and_failed_fallback_never_report_success(self):
        self.run_workspace(r"""
 choose('DeepSeek-V4.1-Flash');await submit();delete context.navigator.clipboard;
 await copyAnswer();assert.deepEqual(fallbackWrites,['<script>untrusted</script>']);
 execCopySucceeds=false;await copyConversation();
 assert.equal(fallbackWrites.length,2);assert.equal(document.body.children.length,0);
 assert.match(element('copy-status').textContent,/실패|못했|수 없|직접/);
 assert.equal(element('copy-status').className.includes('error'),true);
 assert.equal(requests.length,1);
 """)

    def test_new_task_new_model_and_new_tutor_conversation_invalidate_old_copy(self):
        self.run_workspace(r"""
 choose('DeepSeek-V4.1-Flash');await submit();switchTask('tutor');assertCopyHidden();
 await copyAnswer();assert.equal(clipboardWrites.length,0);
 element('question').value='튜터 질문';responseQueue.push(tutor('튜터 답변'));await submit();
 element('new-conversation').events.click();assertCopyHidden();await copyConversation();assert.equal(clipboardWrites.length,0);
 element('question').value='새 튜터 질문';responseQueue.push(tutor('새 튜터 답변'));await submit();
 choose('DeepSeek-V4-Pro');assertCopyHidden();await copyAnswer();assert.equal(clipboardWrites.length,0);
 assert.equal(requests.length,3);
 """)

    def test_new_request_busy_and_failed_response_do_not_copy_the_previous_answer(self):
        self.run_workspace(r"""
 choose('DeepSeek-V4.1-Flash');await submit();element('question').value='다음 질문';
 holdResponse=true;const pending=submit();await new Promise(resolve=>setImmediate(resolve));
 assertCopyHidden();await copyAnswer();await copyConversation();assert.equal(clipboardWrites.length,0);
 responseQueue.push({_error:true,detail:{message:'provider unavailable'}});holdResponse=false;releaseResponse();await pending;
 assertCopyHidden();await copyAnswer();assert.equal(clipboardWrites.length,0);assert.equal(requests.length,2);
 """)

    def test_late_clipboard_rejection_after_model_change_never_falls_back_to_old_answer(self):
        self.run_workspace(r"""
 choose('DeepSeek-V4.1-Flash');await submit();holdClipboard=true;
 const pending=copyAnswer();await new Promise(resolve=>setImmediate(resolve));
 choose('DeepSeek-V4-Pro');assertCopyHidden();rejectClipboard();await pending;
 assert.equal(fallbackWrites.length,0,'stale answer must not be selected after task context changes');
 assert.equal(document.body.children.length,0);assert.equal(element('copy-status').textContent,'');assert.equal(requests.length,1);
 """)

    def test_copy_preserves_answer_content_that_matches_page_labels(self):
        self.run_workspace(r"""
 choose('DeepSeek-V4.1-Flash');
 const answer='  EduRecover\n사용 모델: 원문 내용\n검토 상태: 실제 답변이 이 문장을 인용함\n<script>\u0000&lt;b&gt;';
 responseQueue.push({task:'direct',model_key:'Proprietary model label',answer});await submit();await copyAnswer();
 assert.equal(clipboardWrites[0],answer,'copy from the result payload, never delete matching answer words or parse its HTML');
 assert.equal(requests.length,1);
 """)

    def test_structured_questions_and_source_review_copy_body_without_page_metadata(self):
        self.run_workspace(r"""
 choose('DeepSeek-V4.1-Flash');switchTask('questions');sourceText('허가된 교재 원문');
 element('question').value='문제 출제 요청';
 responseQueue.push({task:'questions',model_key:'Proprietary model label',draft:{title:'문항 묶음',items:[{number:1,question:'문제 <img onerror=x>',options:['선지 가','선지 나'],answer:'선지 가',explanation:'교재에 따른 해설',source_ids:['S1']}],uncertainties:['불확실한 교재 판본']}});
 await submit();await copyAnswer();const questions=clipboardWrites[0];
 for(const needed of ['문항 묶음','문제 <img onerror=x>','선지 가','선지 나','교재에 따른 해설','S1','불확실한 교재 판본'])assert.ok(questions.includes(needed),needed);
 assertNoPageText(questions);assert.equal(questions.includes('정답·해설 보기'),false);
 switchTask('source_review');sourceText('새 검토 원문');element('question').value='검토 요청';
 responseQueue.push({task:'source_review',domain:'education',model_key:'Proprietary model label',draft:{summary:'검토 요약',summary_source_ids:['S1'],points:[{text:'대조한 핵심',source_ids:['S1']}],questions:['추가 확인 질문'],uncertainties:['미확인 사실']}});
 await submit();await copyAnswer();const review=clipboardWrites[1];
 for(const needed of ['검토 요약','대조한 핵심','추가 확인 질문','미확인 사실'])assert.ok(review.includes(needed),needed);
 assertNoPageText(review);assert.equal(requests.length,2);
 """)

    def test_file_validation_preserves_existing_selection_and_removes_individual_file(self):
        self.run_workspace(r"""
 attach([file('<b>code.py'),file('notes.TXT')]);assert.equal(selectedNames().length,2);
 assert.match(selectedNames()[0],/^<b>code.py/,'filenames are plain text');
 attach([file('malware.exe')]);assert.equal(selectedNames().length,2);assert.match(element('attachment-status').textContent,/지원하지/);
 attach([file('empty.txt','')]);assert.equal(selectedNames().length,2);assert.match(element('attachment-status').textContent,/빈 파일/);
 attach([file('large.pdf','x',8*1024*1024+1)]);assert.equal(selectedNames().length,2);assert.match(element('attachment-status').textContent,/8MiB/);
 attach([file('third.md'),file('fourth.json'),file('fifth.csv')]);assert.equal(selectedNames().length,2);assert.match(element('attachment-status').textContent,/최대 4개/);
 element('attachment-list').children[0].children[1].events.click();assert.equal(selectedNames().length,1);assert.match(selectedNames()[0],/^notes.TXT/);
 attach([file('data.json'),file('page.html'),file('settings.yaml')]);assert.equal(selectedNames().length,4);
 run('setBusy(true)');element('attachment-list').children[0].children[1].events.click();assert.equal(selectedNames().length,4);
 run('setBusy(false)');assert.equal(element('attachments').disabled,false);
 """)

    def test_files_are_read_before_single_request_and_busy_prevents_duplicates(self):
        self.run_workspace(r"""
 choose('DeepSeek-V4.1-Flash');attach([file('photo.JPG','picture'),file('source.py','print(1)')]);
 autoRead=false;const pending=submit();await new Promise(resolve=>setImmediate(resolve));
 assert.equal(readers.length,2);assert.equal(requests.length,0);assert.equal(element('generate').disabled,true);
 await submit();assert.equal(requests.length,0,'busy submission does not start another request');
 readers[0].complete();await new Promise(resolve=>setImmediate(resolve));assert.equal(requests.length,0,'all file reads must finish');
 readers[1].complete();await pending;assert.equal(requests.length,1);
 assert.equal(requests[0].url,'/owner/open-models/call');
 assert.deepEqual(requests[0].body.attachments,[
  {filename:'photo.JPG',content_type:'image/jpeg',data:Buffer.from('picture').toString('base64')},
  {filename:'source.py',content_type:'text/plain',data:Buffer.from('print(1)').toString('base64')}
 ]);
 assert.equal(requests[0].body.explicit_authorization,true);assert.equal('max_tokens' in requests[0].body,false);
 assert.equal(element('generate').disabled,false);assert.equal(element('attachments').disabled,false);
 assert.equal(selectedNames().length,2,'attachments remain visible for explicit removal or reuse');
 """)

    def test_read_failure_and_unsupported_image_model_never_call_inference(self):
        self.run_workspace(r"""
 choose('DeepSeek-V4-Pro');attach([file('picture.png')]);await submit();
 assert.equal(requests.length,0);assert.equal(readers.length,0);assert.match(element('status').textContent,/사진 입력을 지원하지/);
 choose('DeepSeek-V4.1-Flash');readerFailure=true;await submit();
 assert.equal(requests.length,0);assert.match(element('status').textContent,/첨부파일을 읽지 못했습니다/);assert.equal(element('generate').disabled,false);
 readerFailure=false;element('attachment-list').children[0].children[1].events.click();
 choose('DeepSeek-V4-Pro');attach([file('notes.md')]);await submit();assert.equal(requests.length,1,'text files remain usable without vision');
 """)

    def test_switching_tasks_requires_explicit_attachment_discard(self):
        self.run_workspace(r"""
 attach([file('notes.md')]);switchTask('tutor');
 assert.equal(confirmations.length,1);assert.equal(element('task').value,'direct');assert.equal(selectedNames().length,1);
 assert.equal(element('attachment-options').hidden,false);
 allowSwitch=true;switchTask('questions');assert.equal(confirmations.length,2);
 assert.equal(element('task').value,'questions');assert.equal(selectedNames().length,0);
 assert.equal(element('attachment-options').hidden,true);assert.equal(element('attachments').disabled,true);
 assert.match(element('attachment-status').textContent,/제거했습니다/);assert.equal(requests.length,0);
 """)

    def test_unsupported_browser_explains_voice_input_without_enabling_it(self):
        self.run_workspace(r"""
 assert.equal(element('voice-input').disabled,true);assert.match(element('voice-status').textContent,/지원하지 않습니다/);
 element('voice-input').events.click();assert.equal(requests.length,0);
 run('setBusy(true);setBusy(false)');assert.equal(element('voice-input').disabled,true);
 """)

    def test_voice_input_is_explicit_korean_dictation_without_auto_send(self):
        self.run_workspace(r"""
 const microphone=Recognition.instance;assert.equal(microphone.starts,0,'microphone never starts automatically');
 assert.equal(microphone.lang,'ko-KR');assert.equal(microphone.continuous,true);
 assert.match(element('voice-status').textContent,/브라우저 제공업체로 전송/);
 element('voice-input').events.click();assert.equal(microphone.starts,1);assert.equal(element('voice-input').attrs['aria-pressed'],'true');
 microphone.onresult({resultIndex:0,results:[Object.assign([{transcript:'사진을 설명하십시오.'}],{isFinal:true})]});
 assert.match(element('question').value,/시험 질문\n사진을 설명하십시오\./);assert.equal(requests.length,0);
 element('voice-input').events.click();assert.equal(microphone.stops,1);assert.equal(element('voice-input').attrs['aria-pressed'],'false');
 element('voice-input').events.click();microphone.onerror({error:'not-allowed'});assert.match(element('voice-status').textContent,/마이크 사용이 허용되지/);
 element('voice-input').events.click();run('setBusy(true)');assert.equal(element('voice-input').disabled,true);assert.equal(microphone.stops,2);
 const before=element('question').value;
 microphone.onresult({resultIndex:0,results:[Object.assign([{transcript:'busy late text'}],{isFinal:true})]});assert.equal(element('question').value,before);
 run('setBusy(false)');assert.equal(element('voice-input').disabled,false);assert.equal(requests.length,0);
 """, voice=True)

    def test_repeated_final_voice_event_does_not_repeat_the_same_words(self):
        self.run_workspace(r"""
 const microphone=Recognition.instance;element('voice-input').events.click();
 const event={resultIndex:0,results:[speechResult('변형 생성 문법',true)]};
 microphone.onresult(event);microphone.onresult(event);microphone.onresult(event);
 assert.equal(element('question').value.trimEnd(),'시험 질문\n변형 생성 문법');
 assert.equal(requests.length,0,'dictation must never send a question automatically');
 """, voice=True)

    def test_cumulative_final_replacement_uses_the_latest_transcript(self):
        self.run_workspace(r"""
 const microphone=Recognition.instance;element('voice-input').events.click();
 for(const transcript of ['변형','변형 생성','변형 생성 문법']){
  microphone.onresult({resultIndex:0,results:[speechResult(transcript,true)]});
  assert.equal(element('question').value.trimEnd(),'시험 질문\n'+transcript);
 }
 """, voice=True)

    def test_interim_voice_replacement_and_shrinking_remove_stale_words(self):
        self.run_workspace(r"""
 const microphone=Recognition.instance;element('voice-input').events.click();
 for(const transcript of ['변형 생성 문법 설명','변형','변형 생성 문법']){
  microphone.onresult({resultIndex:0,results:[speechResult(transcript)]});
  assert.equal(element('question').value.trimEnd(),'시험 질문\n'+transcript);
 }
 microphone.onresult({resultIndex:0,results:[speechResult('변형 생성 문법',true)]});
 assert.equal(element('question').value.trimEnd(),'시험 질문\n변형 생성 문법');
 """, voice=True)

    def test_stable_final_and_revised_interim_remain_once_each(self):
        self.run_workspace(r"""
 const microphone=Recognition.instance;element('voice-input').events.click();
 microphone.onresult({resultIndex:0,results:[speechResult('첫 문장.',true)]});
 for(const transcript of ['두 번째 문장 설명','두 번째','두 번째 문장.']){
  microphone.onresult({resultIndex:1,results:[speechResult('첫 문장.',true),speechResult(transcript)]});
  assert.equal(element('question').value.trimEnd(),'시험 질문\n첫 문장. '+transcript);
 }
 microphone.onresult({resultIndex:1,results:[speechResult('첫 문장.',true),speechResult('두 번째 문장.',true)]});
 assert.equal(element('question').value.trimEnd(),'시험 질문\n첫 문장. 두 번째 문장.');
 """, voice=True)

    def test_deliberate_repeated_words_are_preserved(self):
        self.run_workspace(r"""
 const microphone=Recognition.instance;element('voice-input').events.click();
 microphone.onresult({resultIndex:0,results:[speechResult('아니 아니, RAG 코드를 만들어 주십시오.',true)]});
 assert.equal(element('question').value.trimEnd(),'시험 질문\n아니 아니, RAG 코드를 만들어 주십시오.');
 """, voice=True)

    def test_each_microphone_start_uses_current_text_and_resets_results(self):
        self.run_workspace(r"""
 const microphone=Recognition.instance;element('question').value='직접 입력한 내용';
 element('voice-input').events.click();
 microphone.onresult({resultIndex:0,results:[speechResult('첫 녹음',true)]});
 element('voice-input').events.click();
 element('question').value='수정한 내용\n첫 녹음';
 element('voice-input').events.click();
 microphone.onresult({resultIndex:0,results:[speechResult('새 녹음',true)]});
 assert.equal(element('question').value.trimEnd(),'수정한 내용\n첫 녹음\n새 녹음');
 assert.equal(microphone.starts,2);assert.equal(requests.length,0);
 """, voice=True)

    def test_late_voice_result_after_stop_does_not_modify_question(self):
        self.run_workspace(r"""
 const microphone=Recognition.instance;element('voice-input').events.click();
 microphone.onresult({resultIndex:0,results:[speechResult('확인한 내용',true)]});
 element('voice-input').events.click();const before=element('question').value;
 microphone.onresult({resultIndex:0,results:[speechResult('중지 후 도착한 내용',true)]});
 assert.equal(element('question').value,before);assert.equal(requests.length,0);
 """, voice=True)


if __name__ == "__main__":
    unittest.main()

