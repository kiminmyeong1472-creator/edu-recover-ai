"""Run real draft storage and workspace JavaScript without paid model calls."""
import json
import os
import re
import subprocess
import unittest
from unittest.mock import patch

from app.services.chat_drafts import draft_store_script
from app.services.open_model_workspace import _HTML, chutes_workspace_html, learning_workspace_html


SETUP = r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const saved=new Map(); let blocked=false;
const storage={getItem(k){if(blocked)throw Error('blocked');return saved.get(k)??null},
 setItem(k,v){if(blocked)throw Error('quota');saved.set(k,v)},removeItem(k){if(blocked)throw Error('blocked');saved.delete(k)}};
globalThis.localStorage=storage;
'''

UI = r'''
function start(script,path='/owner/open-models',failure=false){
 const elements=new Map(),events={},docEvents={},calls=[];
 function el(id,tag=''){
  if(!elements.has(id))elements.set(id,{value:'',disabled:false,hidden:false,children:[],dataset:{},tag,events:{},attrs:{},maxLength:-1,textContent:'',className:'',files:[],
   addEventListener(t,f){this.events[t]=f},append(...items){this.children.push(...items)},replaceChildren(){this.children=[]},
   setAttribute(k,v){this.attrs[k]=v},removeAttribute(k){delete this.attrs[k];if(k==='maxlength')this.maxLength=-1},
   querySelectorAll(){if(id==='draft-form')return [...elements.values()];if(id==='sources')return this.children.flatMap(x=>x.children).filter(x=>x.dataset?.sourceId);return []}});
  return elements.get(id);
 }
 el('task').value='direct';el('domain').value='education';el('question-count').value='5';el('question-format').value='short_answer';
 const rows=['M1','M2'].map(model_key=>({model_key,display_name:model_key,family:'deepseek',configured:true,enabled:true,permitted:true,readiness_state:'configured_not_verified',permission_state:'ALLOWED',supports_images:true}));
 const context=vm.createContext({console,localStorage:storage,location:{pathname:path},confirm:()=>true,
  addEventListener:(t,f)=>events[t]=f,
  document:{hidden:false,getElementById:el,createElement:tag=>el(Symbol(),tag),addEventListener:(t,f)=>docEvents[t]=f},
  fetch:async(url,options={})=>{if(!options.method)return {ok:true,json:async()=>({models:rows})};
   calls.push({url,body:JSON.parse(options.body)});return {ok:!failure,json:async()=>failure?{detail:{message:'network failed'}}:{task:'direct',answer:'ok',model_key:'M1'}};}
 });
 vm.runInContext(script,context);
 return {el,events,docEvents,calls,context};
}
async function ready(){await new Promise(resolve=>setImmediate(resolve))}
function type(ui,text){ui.el('question').value=text;ui.el('question').events.input()}
function choose(ui,key){ui.el('model').value=key;ui.el('model').events.change()}
async function send(ui){await ui.el('draft-form').events.submit({preventDefault(){}})}
'''


class ChatDraftTests(unittest.TestCase):
    def js(self, code):
        result = subprocess.run(['node', '-e', SETUP + code], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def ui(self, assertions, html=_HTML):
        script = re.search(r'<script>(.*?)</script>', html, re.S).group(1)
        self.js(UI + '\nconst script=' + json.dumps(script) + ';\n(async()=>{' + assertions +
                '\n})().catch(e=>{console.error(e);process.exitCode=1});')

    def test_store_survives_a_new_instance_and_preserves_exact_text(self):
        self.js(draft_store_script() + r'''
const a=EduRecoverDraftStore.create({workspace:'chat'});a.set('one','  한글\n<script>literal</script>  ');
const b=EduRecoverDraftStore.create({workspace:'chat'});assert.equal(b.get('one'),'  한글\n<script>literal</script>  ');
assert.equal(b.get('two'),'');assert.equal(EduRecoverDraftStore.create({workspace:'other'}).get('one'),'');
''')

    def test_failed_storage_keeps_memory_and_reports_failure(self):
        self.js(draft_store_script() + r'''
let warning='';const a=EduRecoverDraftStore.create({workspace:'chat',onStatus:m=>warning=m});
blocked=true;assert.equal(a.set('one','원문'),false);assert.equal(a.get('one'),'원문');assert.equal(a.persisted,false);assert.match(warning,/보관하지 못/);
''')

    def test_corrupt_saved_data_is_not_executed(self):
        self.js(draft_store_script() + r'''
const a=EduRecoverDraftStore.create({workspace:'chat'});a.set('one','safe');const key=[...saved.keys()][0];saved.set(key,'<script>throw 1</script>');
const b=EduRecoverDraftStore.create({workspace:'chat'});assert.equal(b.get('one'),'');assert.equal(b.persisted,false);
''')

    def test_storage_failure_does_not_replace_new_text_with_an_old_saved_copy(self):
        self.js(draft_store_script() + r'''
const a=EduRecoverDraftStore.create({workspace:'chat'});a.set('one','이전 문장');blocked=true;
a.set('one','저장에 실패한 새 문장');assert.equal(a.get('one'),'저장에 실패한 새 문장');
blocked=false;a.set('two','다른 대화');assert.equal(a.persisted,false);assert.equal(a.get('one'),'저장에 실패한 새 문장');assert.equal(a.persisted,true);
const b=EduRecoverDraftStore.create({workspace:'chat'});assert.equal(b.get('one'),'저장에 실패한 새 문장');
''')

    def test_success_does_not_erase_another_tabs_newer_text(self):
        self.js(draft_store_script() + r'''
const a=EduRecoverDraftStore.create({workspace:'chat'}),b=EduRecoverDraftStore.create({workspace:'chat'});
a.set('one','보낸 문장');b.set('one','다른 창의 새 문장');assert.equal(a.delete('one','보낸 문장'),false);a.set('one','');assert.equal(b.get('one'),'다른 창의 새 문장');
''')

    def test_unchanged_old_tab_does_not_overwrite_newer_draft(self):
        self.js(draft_store_script() + r'''
const a=EduRecoverDraftStore.create({workspace:'chat'}),b=EduRecoverDraftStore.create({workspace:'chat'});
a.set('one','old');assert.equal(a.get('one'),'old');b.set('one','new');a.set('one','old');assert.equal(b.get('one'),'new');
''')

    def test_owner_namespaces_differ_and_do_not_include_passwords(self):
        with patch.dict(os.environ, {'EDURECOVER_OWNER_USER':'owner-A','EDURECOVER_OWNER_PASSWORD':'secret-A'}):
            first = draft_store_script()
        with patch.dict(os.environ, {'EDURECOVER_OWNER_USER':'owner-B','EDURECOVER_OWNER_PASSWORD':'secret-B'}):
            second = draft_store_script()
        self.assertNotEqual(first, second)
        self.assertNotIn('secret-A', first)
        self.assertNotIn('owner-A', first)

    def test_legacy_reload_restores_text_and_model_without_sending(self):
        self.ui(r'''
const a=start(script);await ready();choose(a,'M1');type(a,'  작성 중\n두 번째 줄  ');
const b=start(script);await ready();assert.equal(b.el('question').value,'  작성 중\n두 번째 줄  ');assert.equal(b.el('model').value,'M1');assert.equal(b.calls.length,0);
''')

    def test_model_switch_preserves_the_current_workspace_text(self):
        self.ui(r'''
const a=start(script);await ready();choose(a,'M1');type(a,'아직 안 보냄');choose(a,'M2');assert.equal(a.el('question').value,'아직 안 보냄');assert.equal(a.calls.length,0);
''')

    def test_send_failure_preserves_draft_after_reload(self):
        self.ui(r'''
const a=start(script,'/owner/open-models',true);await ready();choose(a,'M1');type(a,'실패 시 보존');await send(a);assert.equal(a.el('question').value,'실패 시 보존');
const b=start(script);await ready();assert.equal(b.el('question').value,'실패 시 보존');assert.equal(a.calls.length,1);assert.equal(b.calls.length,0);
''')

    def test_success_clears_only_the_sent_draft(self):
        self.ui(r'''
const a=start(script);await ready();choose(a,'M1');type(a,'전송할 문장');await send(a);assert.equal(a.el('question').value,'');a.events.pagehide();
const b=start(script);await ready();assert.equal(b.el('question').value,'');assert.equal(a.calls.length,1);assert.equal(b.calls.length,0);
''')

    def test_visibility_pagehide_and_korean_composition_save(self):
        self.ui(r'''
const a=start(script);await ready();a.el('question').value='한글 조합';a.el('question').events.compositionend();
let b=start(script);await ready();assert.equal(b.el('question').value,'한글 조합');a.el('question').value='다른 앱으로 이동';a.context.document.hidden=true;a.docEvents.visibilitychange();
b=start(script);await ready();assert.equal(b.el('question').value,'다른 앱으로 이동');a.el('question').value='창 나가기';a.events.pagehide();
b=start(script);await ready();assert.equal(b.el('question').value,'창 나가기');
''')

    def test_explicit_clear_and_failed_storage_warning(self):
        self.ui(r'''
const a=start(script);await ready();type(a,'지울 문장');a.el('clear-chat-draft').events.click();let b=start(script);await ready();assert.equal(b.el('question').value,'');
blocked=true;type(a,'잃지 말아야 할 문장');assert.match(a.el('chat-draft-status').textContent,/보관하지 못/);let warned=false;a.events.beforeunload({preventDefault(){warned=true}});assert.equal(warned,true);assert.equal(a.el('question').value,'잃지 말아야 할 문장');
''')

    def test_chutes_and_learning_pages_keep_their_own_drafts(self):
        for html, path in [(chutes_workspace_html(),'/owner/open-models/chutes'),
                           (learning_workspace_html(),'/owner/open-models/learning')]:
            with self.subTest(path=path):
                self.ui('const a=start(script,' + json.dumps(path) + r''');await ready();choose(a,'M1');type(a,'별도 화면 문장');
const other=start(script,'/owner/open-models');await ready();assert.equal(other.el('question').value,'');
const b=start(script,''' + json.dumps(path) + r''');await ready();assert.equal(b.el('question').value,'별도 화면 문장');assert.equal(b.calls.length,0);
''', html)


if __name__ == '__main__':
    unittest.main()
