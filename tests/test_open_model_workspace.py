"""Run the real workspace JavaScript without contacting model providers."""
import re
import subprocess
import unittest

from app.services.open_model_workspace import _HTML, chutes_workspace_html


_HARNESS = r"""
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const elements=new Map(),requests=[],readers=[],confirmations=[];
let allowSwitch=false,readerFailure=false,autoRead=true;
function element(id,tag=''){
 if(!elements.has(id))elements.set(id,{value:'',disabled:false,hidden:false,children:[],dataset:{},tag,events:{},attrs:{},maxLength:-1,textContent:'',className:'',files:[],
  addEventListener(type,handler){this.events[type]=handler},append(...children){this.children.push(...children)},
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
for(const id of ['generate','add-source','new-conversation','voice-input'])element(id,'button');
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
const context=vm.createContext({document:{getElementById:element,createElement:tag=>element(Symbol(),tag)},
 FileReader:Reader,confirm:message=>{confirmations.push(message);return allowSwitch},
 ...(process.env.TEST_VOICE==='true'?{SpeechRecognition:Recognition}:{}),
 fetch:async(url,options)=>{
  if(url==='/owner/open-models/catalog')return {ok:true,json:async()=>({models:rows})};
  requests.push({url,body:JSON.parse(options.body)});return {ok:true,json:async()=>({task:'direct',model_key:JSON.parse(options.body).model_key,answer:'<script>untrusted</script>'})};
 },console});
function run(code){return vm.runInContext(code,context)}
function choose(key){element('model').value=key;element('model').events.change()}
async function submit(){await element('draft-form').events.submit({preventDefault(){}})}
function attach(files){element('attachments').files=files;element('attachments').events.change({target:element('attachments')})}
function file(name,contents='file body',size=null){return {name,contents,size:size===null?Buffer.byteLength(contents):size}}
function selectedNames(){return element('attachment-list').children.map(item=>item.children[0].textContent)}
function switchTask(task){element('task').value=task;element('task').events.change()}
function speechResult(transcript,isFinal=false){return Object.assign([{transcript}],{isFinal})}
(async()=>{
 vm.runInContext(fs.readFileSync(0,'utf8'),context);await new Promise(resolve=>setImmediate(resolve));
 __TEST__
})().catch(error=>{console.error(error);process.exitCode=1});
"""


class OpenModelWorkspaceTests(unittest.TestCase):
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
        self.assertIn('1,024토큰', html)
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
