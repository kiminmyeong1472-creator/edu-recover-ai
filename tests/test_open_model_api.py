"""Owner authentication, UI escaping and no-inference catalogue contract."""
import base64
import json
import os
import re
import subprocess
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.services import open_model_api as api
from app.services.open_model_attachments import AttachmentInput
from app.services.open_model_catalog import (
    AWS_ADDITIONAL_MODEL_CATALOG, CLOUD_SERVICE_MODEL_CATALOG, MODEL_CATALOG,
    SOURCE_MODEL_CATALOG, model_family,
)
from app.services.open_model_transport import OpenModelError
from app.services.owner_basic_auth import owner_auth_router


def payload(domain="education"):
    return {"model_key": "gpt-oss-20b", "domain": domain, "question": "자료를 설명해 주세요.",
            "sources": [{"source_id": "S1", "text": "시험용 원문"}], "explicit_authorization": True}


SELECTED_MODEL_KEYS = (
    "DeepSeek-V4-Pro", "DeepSeek-V4-Flash", "DeepSeek-R1", "DeepSeek-V4.1-Flash",
    "Qwen3.6-27B", "Qwen3.6-35B-A3B", "Qwen3.5-397B-A17B", "Qwen3.8-27B",
    "gpt-oss-20b", "gpt-oss-120b", "gemma-4-E2B-it", "gemma-4-E4B-it",
    "gemma-4-12B-it", "gemma-4-26B-A4B-it", "gemma-4-31B-it",
    "GLM-5.1", "GLM-5.2", "GLM-5.3-Flash", "Mistral-Small-3.2-24B-Instruct-2506",
    "Mistral-Large-3-675B-Base-2512", "Mistral-Small-4-119B-2603", "phi-4",
    "NVIDIA-Nemotron-3.5-Lightning-30B-A3B-BF16", "NVIDIA-Nemotron-3-Nano-4B-BF16",
    "NVIDIA-Nemotron-3-Super-120B-A12B-BF16",
)


def direct_payload(**changes):
    return dict({"model_key": "gpt-oss-20b", "prompt": "원하는 내용을 설명하십시오.",
                 "explicit_authorization": True}, **changes)


class OpenModelAPITests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"EDURECOVER_OWNER_USER": "owner-test",
            "EDURECOVER_OWNER_PASSWORD": "test-password-only",
            "EDURECOVER_ALLOWED_PROVIDERS": "gpt-oss", "EDURECOVER_AI_HOLD": "false",
            "EDURECOVER_CONTROL_MANIFEST_JSON": "", "EDURECOVER_OPEN_MODEL_BINDINGS_JSON": "{}",
            "EDURECOVER_OPEN_MODELS_ENABLED": "false"})
        self.env.start(); self.addCleanup(self.env.stop)
        app = FastAPI(); app.include_router(owner_auth_router); app.include_router(api.router)
        self.client = TestClient(app, base_url="https://testserver")
        self.addCleanup(self.client.close)
        self.auth = ("owner-test", "test-password-only")

    def test_anonymous_requests_never_generate(self):
        with patch.object(api, "generate_draft", new_callable=AsyncMock) as generate:
            response = self.client.get("/owner/open-models", follow_redirects=False)
            self.assertEqual(response.status_code, 303)
            self.assertEqual(response.headers["location"], "/owner/login")
            response = self.client.post("/owner/open-models/draft", json=payload())
            self.assertEqual(response.status_code, 401)
            generate.assert_not_awaited()

    def test_catalogue_has_all_models_but_never_calls_inference(self):
        with patch("app.services.open_model_transport.call_model", new_callable=AsyncMock) as call:
            response = self.client.get("/owner/open-models/catalog", auth=self.auth)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data["models"]), 30)
        self.assertTrue({"qwen", "deepseek", "gpt-oss", "gemma", "glm", "mistral", "phi", "nemotron"}
                        <= {m["family"] for m in data["models"]})
        self.assertTrue(all(not m["configured"] for m in data["models"]))
        self.assertEqual({m["model_key"] for m in data["models"] if m["selection_group"] == "selected_25"},
                         set(SELECTED_MODEL_KEYS))
        self.assertTrue(all(m["selection_group"] == "aws_additional" for m in data["models"]
                            if m["family"] == "nova"))
        self.assertEqual({m["model_key"] for m in data["models"] if m["selection_group"] == "aws_additional"},
                         {"Qwen3-32B", "DeepSeek-V3.2", "Amazon-Nova-2-Lite", "Amazon-Nova-Pro"})
        cloud = [m for m in data["models"] if m["selection_group"] == "cloud_service"]
        self.assertEqual(len(cloud), 1)
        self.assertEqual(cloud[0]["model_key"], "GPT-6-Luna")
        self.assertEqual(cloud[0]["family"], "openai")
        self.assertFalse(cloud[0]["permitted"])
        self.assertEqual(data["readiness_basis"], "configuration_only_not_live_provider_success")
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertNotIn("endpoint_name", response.text)
        call.assert_not_awaited()

    def test_workspace_uses_safe_text_nodes_for_model_output(self):
        response = self.client.get("/owner/open-models", auth=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertIn("교육·의료 AI 작업", response.text)
        self.assertIn("부작용·과잉진료", response.text)
        for term in ('value="direct"', 'value="tutor"', 'value="questions"', 'value="source_review"',
                     "Qwen", "DeepSeek", "Amazon Nova", "새 튜터 대화", "문제·정답·해설"):
            self.assertIn(term, response.text)
        self.assertIn("node.textContent=text", response.text)
        self.assertNotIn("innerHTML", response.text)
        self.assertIn("frame-ancestors 'none'", response.headers["content-security-policy"])

    def test_direct_ui_explains_selected_models_and_uses_plain_text_and_readiness_labels(self):
        response = self.client.get("/owner/open-models", auth=self.auth)
        for text in ("8개 계열·25개 모델", "모델에 직접 질문", "AWS 추가 후보", "인증 정보 필요",
                     "GPT-6 Luna", "Azure 서비스", "OpenRouter", "저장하지 않는 조건(ZDR)",
                     "configured_not_verified", "missing_credentials", "/owner/open-models/call",
                     "addText(box,'p',result.answer)", "byId('sources').hidden=direct",
                     "byId('add-source').hidden=direct"):
            self.assertIn(text, response.text)

    def test_task_changes_disable_irrelevant_fields_before_browser_validation(self):
        # Execute the actual workspace script with a tiny DOM so this checks
        # task changes and the request-completion path, not JavaScript spelling.
        script = re.search(r"<script>(.*?)</script>", api._HTML, re.S).group(1)
        harness = r"""
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const elements=new Map();
function element(id){
 if(!elements.has(id))elements.set(id,{value:'',className:'',disabled:false,children:[],dataset:{},
  setAttribute(name,value){this[name]=value},removeAttribute(name){delete this[name]},
  addEventListener(){},append(...children){this.children.push(...children)},
  replaceChildren(){this.children=[]},querySelectorAll(selector){
   if(id==='draft-form')return [...elements.values()];
   if(id==='sources')return this.children.flatMap(child=>child.children).filter(child=>child.dataset.sourceId);
   return [];
  }});
 return elements.get(id);
}
element('task').value='direct';element('domain').value='education';
element('question-count').value='5';
const context=vm.createContext({document:{getElementById:element,createElement:()=>element(Symbol())},
 fetch:async()=>({ok:true,json:async()=>({models:[]})}),console});
vm.runInContext(fs.readFileSync(0,'utf8'),context);
function run(code){vm.runInContext(code,context)}
assert.equal(element('attachments').disabled,false);
assert.equal(element('question-count').disabled,true);
assert.equal(element('sources').querySelectorAll('textarea')[0].disabled,true);
element('task').value='questions';run('updateTask()');
assert.equal(element('attachments').disabled,true,'hidden uploads must not be sent with draft tasks');
assert.equal(element('question-count').disabled,false);
assert.equal(element('question-format').disabled,false);
assert.equal(element('sources').querySelectorAll('textarea')[0].disabled,false);
run('setBusy(true);setBusy(false)');
assert.equal(element('attachments').disabled,true,'completion must preserve task-specific disabled state');
element('question-count').value='0';element('task').value='direct';run('updateTask();setBusy(true);setBusy(false)');
assert.equal(element('question-count').disabled,true,'invalid hidden question count must not block direct calls');
assert.equal(element('question-format').disabled,true);
assert.equal(element('attachments').disabled,false);
element('task').value='source_review';run('updateTask()');
assert.equal(element('domain').disabled,false);
assert.equal(element('attachments').disabled,true);
assert.equal(element('question-count').disabled,true);
"""
        result = subprocess.run(["node", "-e", harness], input=script,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_model_selection_allows_inspection_but_sends_only_the_selected_ready_model(self):
        # Run the real script with browser events and a mocked same-origin fetch.
        # No configured fixture endpoint or inference provider is contacted.
        rows = [dict(row, configured=False, enabled=False, permitted=False,
                     readiness_state="not_configured", permission_state="OWNER_PERMISSION_REQUIRED",
                     selection_group=group)
                for group, entries in (("selected_25", SOURCE_MODEL_CATALOG),
                                       ("aws_additional", AWS_ADDITIONAL_MODEL_CATALOG),
                                       ("cloud_service", CLOUD_SERVICE_MODEL_CATALOG))
                for row in entries]
        special = {
            "gpt-oss-20b": dict(configured=True, enabled=True, permitted=True,
                                readiness_state="configured_not_verified", permission_state="ALLOWED"),
            "Qwen3.6-27B": dict(readiness_state="missing_credentials", permitted=True,
                                 permission_state="ALLOWED"),
            "phi-4": dict(configured=True, readiness_state="disabled", permitted=True,
                          permission_state="ALLOWED"),
            "DeepSeek-R1": dict(configured=True, enabled=True,
                                 readiness_state="configured_not_verified", permission_state="HOLD"),
        }
        for row in rows:
            row.update(special.get(row["model_key"], {}))
        script = re.search(r"<script>(.*?)</script>", api._HTML, re.S).group(1)
        harness = r"""
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const catalogRows=__ROWS__,elements=new Map(),requests=[];
function element(id,tag=''){
 if(!elements.has(id))elements.set(id,{value:'',className:'',disabled:false,children:[],dataset:{},tag,events:{},
  setAttribute(name,value){this[name]=value},removeAttribute(name){delete this[name]},
  addEventListener(type,handler){this.events[type]=handler},append(...children){this.children.push(...children)},
  replaceChildren(){this.children=[]},querySelectorAll(selector){
   if(id==='draft-form')return [...elements.values()].filter(node=>['input','select','textarea','button'].includes(node.tag));
   if(id==='sources')return this.children.flatMap(child=>child.children).filter(child=>child.dataset.sourceId);
   return [];
  }});
 return elements.get(id);
}
for(const id of ['task','domain','model','question-format'])element(id,'select');
for(const id of ['attachments','question-count'])element(id,'input');
for(const id of ['generate','add-source','new-conversation'])element(id,'button');
element('question','textarea');element('task').value='direct';element('domain').value='education';
element('question-count').value='5';element('question').value='시험용 질문';
const context=vm.createContext({document:{getElementById:element,createElement:tag=>element(Symbol(),tag)},
 fetch:async(url,options)=>{
  if(url==='/owner/open-models/catalog')return {ok:true,json:async()=>({models:catalogRows})};
  requests.push({url,options});return {ok:true,json:async()=>({task:'direct',model_key:JSON.parse(options.body).model_key,answer:'시험용 답변'})};
 },console});
function run(code){return vm.runInContext(code,context)}
function choose(key){element('model').value=key;element('model').events.change()}
async function submit(){await element('draft-form').events.submit({preventDefault(){}})}
(async()=>{
 vm.runInContext(fs.readFileSync(0,'utf8'),context);await new Promise(resolve=>setImmediate(resolve));
 const options=element('model').children.flatMap(node=>node.tag==='optgroup'?node.children:[]);
 assert.equal(options.length,30);
 assert.ok(options.every(option=>option.disabled===false),'all registered options remain selectable, including held models');
 const cloudGroup=element('model').children.find(node=>node.tag==='optgroup'&&node.label==='OpenAI · Azure 서비스');
 assert.ok(cloudGroup);assert.equal(cloudGroup.children.length,1);
 assert.equal(cloudGroup.children[0].value,'GPT-6-Luna');
 const awsOptions=element('model').children.filter(node=>node.tag==='optgroup'&&node.label.endsWith(' · AWS 추가 후보')).flatMap(node=>node.children);
 assert.equal(awsOptions.length,4);
 assert.equal(element('generate').disabled,true,'a ready model elsewhere must not enable the placeholder selection');
 await submit();assert.equal(requests.length,0,'programmatic placeholder submission must not call any model');
 choose('GPT-6-Luna');assert.equal(element('generate').disabled,true);
 await submit();assert.equal(requests.length,0,'unconfigured Azure service must not be called');
 choose('DeepSeek-V4-Pro');assert.equal(element('model').value,'DeepSeek-V4-Pro');
 assert.equal(element('generate').disabled,true);
 assert.match(element('model-readiness').textContent,/연결 상태: 연결 설정 필요/);
 assert.match(element('model-readiness').textContent,/사용 권한: 소유자 사용 허용 필요/);
 run('setBusy(true);setBusy(false)');assert.equal(element('model').disabled,false);
 assert.equal(element('generate').disabled,true,'busy completion must check the selected model, not any ready row');
 await submit();assert.equal(requests.length,0,'unconfigured selection must never be sent');
 choose('Qwen3.6-27B');assert.match(element('model-readiness').textContent,/인증 정보 필요/);
 assert.match(element('model-readiness').textContent,/사용 권한: 소유자 사용 허용/);
 await submit();assert.equal(requests.length,0,'missing credentials must prevent sending');
 choose('DeepSeek-R1');assert.match(element('model-readiness').textContent,/소유자가 전체 AI 호출을 중지함/);
 assert.match(element('model-readiness').textContent,/실제 응답 미확인/);
 await submit();assert.equal(requests.length,0,'owner-held selection must prevent sending');
 choose('phi-4');assert.match(element('model-readiness').textContent,/호출 기능 꺼짐/);
 await submit();assert.equal(requests.length,0,'disabled feature must prevent sending');
 choose('gpt-oss-20b');assert.equal(element('generate').disabled,false);
 assert.match(element('model-readiness').textContent,/아직 확인하지 않았습니다/);
 await submit();assert.equal(requests.length,1);assert.equal(requests[0].url,'/owner/open-models/call');
 assert.equal(JSON.parse(requests[0].options.body).model_key,'gpt-oss-20b');
 assert.equal(element('generate').disabled,false,'completed request restores the ready selected model');
 choose('DeepSeek-V4-Pro');assert.equal(element('generate').disabled,true);
 element('generate').disabled=false;await submit();assert.equal(requests.length,1,'changing the button alone cannot bypass the selected model guard');
 choose('unknown');await submit();assert.equal(requests.length,1,'an unknown selected value is not callable');
 assert.equal(element('generate').disabled,true);
 // Even if no row is ready, the original 29 and Azure service remain selectable.
 for(const row of catalogRows){row.configured=false;row.enabled=false;row.permitted=false;row.readiness_state='not_configured'}
 await run('loadModels()');
 assert.equal(element('model').children.flatMap(node=>node.tag==='optgroup'?node.children:[]).length,30);
 choose('gpt-oss-20b');assert.equal(element('model').value,'gpt-oss-20b');assert.equal(element('generate').disabled,true);
 await submit();assert.equal(requests.length,1);
})().catch(error=>{console.error(error);process.exitCode=1});
""".replace("__ROWS__", json.dumps(rows, ensure_ascii=False))
        result = subprocess.run(["node", "-e", harness], input=script,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_catalogue_reports_connection_and_owner_policy_states_independently(self):
        cases = (
            ({}, "ALLOWED", True),
            ({"EDURECOVER_AI_HOLD": "true"}, "HOLD", False),
            ({"EDURECOVER_ALLOWED_PROVIDERS": "qwen"}, "OWNER_PERMISSION_REQUIRED", False),
            ({"EDURECOVER_BLOCKED_PROVIDERS": "gpt-oss"}, "BLOCKED", False),
            ({"EDURECOVER_RESTRICTED_PROVIDERS": "gpt-oss"}, "RESTRICTED_VERIFY_ONLY", False),
            ({"EDURECOVER_CONTROL_MANIFEST_JSON": "invalid"}, "CONTROL_CONFIG_INVALID", False),
        )
        with patch.object(api.transport, "call_model", new_callable=AsyncMock) as invoke:
            for control, expected_state, permitted in cases:
                with self.subTest(state=expected_state), patch.dict(os.environ, control):
                    response = self.client.get("/owner/open-models/catalog", auth=self.auth)
                    self.assertEqual(response.status_code, 200)
                    row = next(row for row in response.json()["models"] if row["model_key"] == "gpt-oss-20b")
                    self.assertEqual(row["permission_state"], expected_state)
                    self.assertEqual(row["permitted"], permitted)
                    self.assertEqual(row["readiness_state"], "not_configured")
                    self.assertFalse(row["configured"])
            invoke.assert_not_awaited()

    def test_direct_call_accepts_all_25_exact_keys_and_unstructured_base_model_output(self):
        self.assertEqual(tuple(row["model_key"] for row in SOURCE_MODEL_CATALOG), SELECTED_MODEL_KEYS)
        families = ",".join(sorted({row["family"] for row in SOURCE_MODEL_CATALOG}))
        answer = "자유 형식의 생성 초안입니다.\n<script>this is plain text</script>"
        with patch.dict(os.environ, {"EDURECOVER_ALLOWED_PROVIDERS": families}):
            for model_key in SELECTED_MODEL_KEYS:
                with self.subTest(model_key=model_key), patch.object(api.transport, "call_model",
                        new_callable=AsyncMock, return_value=answer) as invoke:
                    response = self.client.post("/owner/open-models/call",
                                                json=direct_payload(model_key=model_key), auth=self.auth)
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(response.json(), {
                        "task": "direct", "model_key": model_key, "answer": answer,
                        "review": {"state": "DRAFT", "official_source_check": "NOT_PERFORMED",
                                   "independent_ai_check": "NOT_PERFORMED"},
                    })
                    self.assertEqual(response.headers["cache-control"], "no-store")
                    invoke.assert_awaited_once_with(
                        model_key, api._DIRECT_INSTRUCTIONS, direct_payload()["prompt"], max_tokens=None,
                    )
                    self.assertNotIn("Return only one JSON", invoke.await_args.args[1])

    def test_azure_service_preserves_original_29_names_and_has_separate_openai_family(self):
        self.assertEqual(tuple(row["model_key"] for row in SOURCE_MODEL_CATALOG), SELECTED_MODEL_KEYS)
        aws_keys = ("Qwen3-32B", "DeepSeek-V3.2", "Amazon-Nova-2-Lite", "Amazon-Nova-Pro")
        self.assertEqual(tuple(row["model_key"] for row in AWS_ADDITIONAL_MODEL_CATALOG), aws_keys)
        self.assertEqual(tuple(row["model_key"] for row in MODEL_CATALOG[:29]), SELECTED_MODEL_KEYS + aws_keys)
        self.assertEqual(len(MODEL_CATALOG), 30)
        self.assertEqual(len({row["model_key"] for row in MODEL_CATALOG}), 30)
        self.assertEqual(CLOUD_SERVICE_MODEL_CATALOG, (
            {"model_key": "GPT-6-Luna", "family": "openai", "display_name": "GPT-6 Luna · Azure 경유"},
        ))
        self.assertEqual(model_family("GPT-6-Luna"), "openai")
        self.assertEqual(model_family("gpt-oss-20b"), "gpt-oss")
        self.assertEqual(model_family("gpt-oss-120b"), "gpt-oss")

    def test_azure_service_uses_existing_openai_owner_permission_before_inference(self):
        controls = (
            ({"EDURECOVER_ALLOWED_PROVIDERS": "openai,gpt-oss"}, "ALLOWED", 200),
            ({"EDURECOVER_ALLOWED_PROVIDERS": "gpt-oss"}, "OWNER_PERMISSION_REQUIRED", 423),
            ({"EDURECOVER_ALLOWED_PROVIDERS": "openai,gpt-oss", "EDURECOVER_AI_HOLD": "true"}, "HOLD", 423),
            ({"EDURECOVER_ALLOWED_PROVIDERS": "openai,gpt-oss", "EDURECOVER_BLOCKED_PROVIDERS": "openai"}, "BLOCKED", 423),
            ({"EDURECOVER_ALLOWED_PROVIDERS": "openai,gpt-oss", "EDURECOVER_RESTRICTED_PROVIDERS": "openai"}, "RESTRICTED_VERIFY_ONLY", 423),
        )
        for control, permission, expected in controls:
            with self.subTest(permission=permission), patch.dict(os.environ, {
                "EDURECOVER_BLOCKED_PROVIDERS": "", "EDURECOVER_RESTRICTED_PROVIDERS": "", **control,
            }), patch.object(api.transport, "call_model", new_callable=AsyncMock, return_value="Luna 시험 답변") as invoke:
                catalog = self.client.get("/owner/open-models/catalog", auth=self.auth).json()["models"]
                luna = next(row for row in catalog if row["model_key"] == "GPT-6-Luna")
                self.assertEqual(luna["selection_group"], "cloud_service")
                self.assertEqual(luna["permission_state"], permission)
                self.assertEqual(luna["permitted"], expected == 200)
                response = self.client.post("/owner/open-models/call",
                                            json=direct_payload(model_key="GPT-6-Luna"), auth=self.auth)
                self.assertEqual(response.status_code, expected)
                if expected == 200:
                    invoke.assert_awaited_once_with("GPT-6-Luna", api._DIRECT_INSTRUCTIONS,
                                                   direct_payload()["prompt"], max_tokens=None)
                    self.assertEqual(response.json()["model_key"], "GPT-6-Luna")
                    self.assertEqual(response.json()["review"]["state"], "DRAFT")
                else:
                    self.assertEqual(response.json()["detail"]["code"], "OPEN_MODEL_POLICY_HELD")
                    invoke.assert_not_awaited()
                if permission in {"BLOCKED", "RESTRICTED_VERIFY_ONLY"}:
                    original = next(row for row in catalog if row["model_key"] == "gpt-oss-20b")
                    self.assertEqual(original["permission_state"], "ALLOWED")

    def test_direct_call_requires_owner_authentication_and_same_origin_cookie_post(self):
        with patch.object(api.transport, "call_model", new_callable=AsyncMock) as invoke:
            response = self.client.post("/owner/open-models/call", json=direct_payload())
            self.assertEqual(response.status_code, 401)
            response = self.client.post("/owner/open-models/call", json=direct_payload(), auth=("owner-test", "wrong"))
            self.assertEqual(response.status_code, 401)
            response = self.client.post("/owner/login", data={"username": self.auth[0], "password": self.auth[1]},
                                        headers={"Origin": "https://testserver"}, follow_redirects=False)
            self.assertEqual(response.status_code, 303)
            response = self.client.post("/owner/open-models/call", json=direct_payload(),
                                        headers={"Origin": "https://foreign.example"})
            self.assertEqual(response.status_code, 403)
            invoke.assert_not_awaited()

    def test_direct_call_checks_owner_policy_before_inference(self):
        controls = ({"EDURECOVER_AI_HOLD": "true"}, {"EDURECOVER_ALLOWED_PROVIDERS": "qwen"},
                    {"EDURECOVER_BLOCKED_PROVIDERS": "gpt-oss"},
                    {"EDURECOVER_RESTRICTED_PROVIDERS": "gpt-oss"},
                    {"EDURECOVER_CONTROL_MANIFEST_JSON": "not valid JSON"})
        for control in controls:
            with self.subTest(control=control), patch.dict(os.environ, control), patch.object(
                    api.transport, "call_model", new_callable=AsyncMock) as invoke:
                response = self.client.post("/owner/open-models/call", json=direct_payload(), auth=self.auth)
                self.assertEqual(response.status_code, 423)
                self.assertEqual(response.json()["detail"]["code"], "OPEN_MODEL_POLICY_HELD")
                self.assertEqual(response.headers["cache-control"], "no-store")
                invoke.assert_not_awaited()

    def test_direct_call_validates_bounded_text_tokens_and_literal_authorization(self):
        invalid = [
            {"prompt": ""}, {"prompt": " \n\t "}, {"prompt": 42},
            {"model_key": "x" * 257}, {"model_key": 42}, {"max_tokens": 0},
            {"max_tokens": True}, {"max_tokens": 1.0}, {"max_tokens": "512"},
            {"explicit_authorization": False}, {"explicit_authorization": 1},
            {"explicit_authorization": "true"}, {"endpoint": "https://untrusted.example"},
            {"api_key": "SYNTHETIC_PRIVATE_KEY"}, {"provider": "other"}, {"task": "direct"},
        ]
        with patch.object(api.transport, "call_model", new_callable=AsyncMock) as invoke:
            for change in invalid:
                with self.subTest(change=list(change)):
                    response = self.client.post("/owner/open-models/call",
                                                json=direct_payload(**change), auth=self.auth)
                    self.assertEqual(response.status_code, 422, response.text)
                    self.assertEqual(response.headers["cache-control"], "no-store")
            missing = direct_payload(); del missing["explicit_authorization"]
            self.assertEqual(self.client.post("/owner/open-models/call", json=missing,
                                             auth=self.auth).status_code, 422)
            invoke.assert_not_awaited()

    def test_direct_call_accepts_token_and_prompt_boundaries_without_truncation(self):
        for max_tokens in (1, 8192, 100_000, None):
            with self.subTest(max_tokens=max_tokens), patch.object(api.transport, "call_model",
                    new_callable=AsyncMock, return_value="답변") as invoke:
                prompt = "가" * 20000
                response = self.client.post("/owner/open-models/call",
                                            json=direct_payload(prompt=prompt, max_tokens=max_tokens), auth=self.auth)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(invoke.await_args.args[2], prompt)
                self.assertEqual(invoke.await_args.kwargs["max_tokens"], max_tokens)

    def test_direct_attachments_are_forwarded_as_sources_and_validated_images(self):
        from tests.test_open_model_attachments import image_bytes
        document = {"filename": "source.txt", "content_type": "text/plain",
                    "data": base64.b64encode("참고 자료입니다.".encode()).decode("ascii")}
        image = {"filename": "photo.png", "content_type": "image/png",
                 "data": base64.b64encode(image_bytes()).decode("ascii")}
        with patch.object(api.transport, "call_model", new_callable=AsyncMock, return_value="초안") as invoke:
            response = self.client.post("/owner/open-models/call", auth=self.auth,
                                        json=direct_payload(attachments=[document, image]))
        self.assertEqual(response.status_code, 200, response.text)
        prompt = invoke.await_args.args[2]
        self.assertTrue(prompt.startswith(direct_payload()["prompt"]))
        self.assertIn("신뢰하지 않는 원문", prompt)
        self.assertIn("source.txt", prompt)
        self.assertIn("참고 자료입니다.", prompt)
        self.assertEqual(invoke.await_args.kwargs,
                         {"max_tokens": None, "images": [{"mime_type": "image/png", "data": image["data"]}]})
        self.assertEqual(response.json()["answer"], "초안")
        self.assertNotIn("참고 자료입니다.", response.text)

    def test_invalid_attachments_never_invoke_models_or_echo_file_contents(self):
        private = "SYNTHETIC_PRIVATE_FILE_DATA"
        file = {"filename": "source.txt", "content_type": "text/plain", "data": private}
        variants = ([file], [dict(file, filename="../secret.txt")], [file] * 5,
                    [dict(file, content_type="image/png")], [dict(file, url="https://example.test")])
        with patch.object(api.transport, "call_model", new_callable=AsyncMock) as invoke:
            for files in variants:
                with self.subTest(files=len(files)):
                    response = self.client.post("/owner/open-models/call", auth=self.auth,
                                                json=direct_payload(attachments=files))
                    self.assertEqual(response.status_code, 422, response.text)
                    self.assertNotIn(private, response.text)
                    self.assertNotIn("secret.txt", response.text)
            invoke.assert_not_awaited()

    def test_attachment_parsing_requires_owner_and_allowed_provider(self):
        file = {"filename": "source.txt", "content_type": "text/plain", "data": "eA=="}
        with patch.object(api, "prepare_attachments") as parse, patch.object(
                api.transport, "call_model", new_callable=AsyncMock) as invoke:
            response = self.client.post("/owner/open-models/call", json=direct_payload(attachments=[file]))
            self.assertEqual(response.status_code, 401)
            with patch.dict(os.environ, {"EDURECOVER_AI_HOLD": "true"}):
                response = self.client.post("/owner/open-models/call", auth=self.auth,
                                            json=direct_payload(attachments=[file]))
            self.assertEqual(response.status_code, 423)
            parse.assert_not_called()
            invoke.assert_not_awaited()

    def test_upload_body_bound_applies_with_and_without_content_length(self):
        with patch.object(api, "MAX_REQUEST_BYTES", 256), patch.object(
                api.transport, "call_model", new_callable=AsyncMock) as invoke:
            large = json.dumps(direct_payload(prompt="x" * 300)).encode()
            for content in (large, iter((large[:100], large[100:]))):
                response = self.client.post("/owner/open-models/call", auth=self.auth, content=content,
                                            headers={"Content-Type": "application/json"})
                self.assertEqual(response.status_code, 413)
                self.assertEqual(response.json()["detail"]["code"], "OPEN_MODEL_REQUEST_TOO_LARGE")
                self.assertEqual(response.headers["cache-control"], "no-store")
            invoke.assert_not_awaited()

    def test_direct_call_logs_only_the_stable_error_code(self):
        private = "SYNTHETIC_PRIVATE_UPSTREAM_RESPONSE"
        error = OpenModelError(private, "OPEN_MODEL_INCOMPLETE_RESPONSE", 502)
        with patch.object(api.transport, "call_model", new_callable=AsyncMock, side_effect=error), \
                self.assertLogs(api._LOGGER, level="WARNING") as logs:
            response = self.client.post("/owner/open-models/call", auth=self.auth, json=direct_payload())
        self.assertEqual(response.status_code, 502)
        self.assertEqual(len(logs.output), 1)
        self.assertIn("code=OPEN_MODEL_INCOMPLETE_RESPONSE", logs.output[0])
        self.assertNotIn(private, logs.output[0])

    def test_unknown_direct_model_is_rejected_without_echo_or_inference(self):
        private_model = "SYNTHETIC_PRIVATE_UNKNOWN_MODEL"
        with patch.object(api.transport, "call_model", new_callable=AsyncMock) as invoke, patch.object(
                api, "assert_provider_allowed") as policy:
            response = self.client.post("/owner/open-models/call",
                                        json=direct_payload(model_key=private_model), auth=self.auth)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"]["code"], "OPEN_MODEL_UNKNOWN")
        self.assertNotIn(private_model, response.text)
        policy.assert_not_called(); invoke.assert_not_awaited()

    def test_direct_validation_and_unexpected_errors_do_not_echo_private_text(self):
        private_text = "SYNTHETIC_PRIVATE_PROMPT_OR_KEY"
        with patch.object(api.transport, "call_model", new_callable=AsyncMock,
                          side_effect=RuntimeError(private_text)) as invoke:
            response = self.client.post("/owner/open-models/call",
                                        json=direct_payload(prompt=private_text, api_key=private_text), auth=self.auth)
            self.assertEqual(response.status_code, 422)
            self.assertNotIn(private_text, response.text)
            self.assertNotIn('"input"', response.text)
            invoke.assert_not_awaited()
            response = self.client.post("/owner/open-models/call",
                                        json=direct_payload(prompt=private_text), auth=self.auth)
            self.assertEqual(response.status_code, 502)
            self.assertEqual(response.json()["detail"]["code"], "OPEN_MODEL_CALL_FAILED")
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertNotIn(private_text, response.text)
            invoke.assert_awaited_once()

    def test_direct_configuration_error_is_bounded_and_has_no_fallback(self):
        with patch.object(api.transport, "call_model", new_callable=AsyncMock,
                          side_effect=OpenModelError("연결 설정이 필요합니다.", "OPEN_MODEL_NOT_CONFIGURED", 503)) as invoke:
            response = self.client.post("/owner/open-models/call", json=direct_payload(), auth=self.auth)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"]["code"], "OPEN_MODEL_NOT_CONFIGURED")
        self.assertEqual(response.headers["cache-control"], "no-store")
        invoke.assert_awaited_once()

    def test_education_and_medical_requests_reach_same_draft_service(self):
        for domain in ("education", "medical"):
            with self.subTest(domain=domain), patch.object(api, "generate_draft", new_callable=AsyncMock,
                    return_value={"domain": domain, "review": {"state": "DRAFT"}}) as generate:
                response = self.client.post("/owner/open-models/draft", json=payload(domain), auth=self.auth)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(generate.await_args.args[0].domain, domain)
                self.assertEqual(response.json()["review"]["state"], "DRAFT")

    def test_tutor_and_question_purposes_reach_the_selected_model_service(self):
        for task in ("tutor", "questions"):
            with self.subTest(task=task), patch.object(api, "generate_draft", new_callable=AsyncMock,
                    return_value={"task": task, "review": {"state": "DRAFT"}}) as generate:
                data = dict(payload(), task=task)
                if task == "tutor":
                    data.update(sources=[], history=[{"role": "user", "content": "이전 질문"}])
                else:
                    data.update(question_count=3, question_format="multiple_choice")
                response = self.client.post("/owner/open-models/draft", json=data, auth=self.auth)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(generate.await_args.args[0].task, task)

    def test_authenticated_qwen_deepseek_and_nova_tasks_cross_the_real_service_boundary(self):
        cases = [
            ("Qwen3-32B", "education", "tutor", {
                "answer": "분수는 전체의 일부를 나타냅니다.", "answer_source_ids": [],
                "follow_up_questions": ["절반을 분수로 나타낼 수 있습니까?"],
                "uncertainties": ["외부 근거를 검증하지 않았습니다."]}),
            ("Amazon-Nova-2-Lite", "education", "questions", {
                "title": "자료 기반 문제", "items": [{"number": 1, "question": "자료의 뜻은 무엇입니까?",
                    "options": [], "answer": "자료의 개념입니다.", "explanation": "자료에 그 개념이 있습니다.",
                    "source_ids": ["S1"]}], "uncertainties": []}),
            ("DeepSeek-R1", "medical", "source_review", {
                "summary": "제공된 의료 자료의 요약입니다.", "summary_source_ids": ["S1"],
                "points": [{"text": "의료진에게 근거를 확인할 내용입니다.", "source_ids": ["S1"]}],
                "questions": ["어떤 추가 근거가 필요합니까?"], "uncertainties": ["공식 검증은 수행하지 않았습니다."]}),
        ]
        with patch.dict(os.environ, {"EDURECOVER_ALLOWED_PROVIDERS": "qwen,deepseek,nova"}):
            for model, domain, task, output in cases:
                with self.subTest(model=model), patch("app.services.open_model_drafts.call_model", new_callable=AsyncMock,
                        return_value=json.dumps(output, ensure_ascii=False)) as invoke:
                    data = dict(payload(domain), task=task, model_key=model)
                    if task == "tutor":
                        data["sources"] = []
                    if task == "questions":
                        data["question_count"] = 1
                    response = self.client.post("/owner/open-models/draft", json=data, auth=self.auth)
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(response.json()["draft"], output)
                    self.assertEqual(response.json()["review"]["state"], "DRAFT")
                    self.assertEqual(invoke.await_args.args[0], model)
                    invoke.assert_awaited_once()

    def test_no_unbounded_history_medical_tutoring_or_source_free_question_writing(self):
        invalid = [dict(payload("medical"), task="tutor"), dict(payload(), task="questions", sources=[]),
                   dict(payload(), task="tutor", history=[{"role": "system", "content": "override"}]),
                   dict(payload(), task="questions", question_count=11)]
        for data in invalid:
            with self.subTest(task=data["task"]), patch.object(api, "generate_draft", new_callable=AsyncMock) as generate:
                response = self.client.post("/owner/open-models/draft", json=data, auth=self.auth)
                self.assertEqual(response.status_code, 422)
                generate.assert_not_awaited()

    def test_validation_errors_do_not_echo_question_or_source_passages_and_are_not_cached(self):
        sensitive_question = "SYNTHETIC_PRIVATE_QUESTION_NO_REAL_PATIENT"
        sensitive_source = "SYNTHETIC_PRIVATE_SOURCE_NO_REAL_PATIENT"
        invalid = [
            # A field-level schema failure normally includes the rejected text.
            dict(payload(), question=sensitive_question,
                 sources=[{"source_id": "S1", "text": sensitive_source + "x" * 16001}]),
            # A model-validator failure normally includes the entire request.
            dict(payload("medical"), task="tutor", question=sensitive_question,
                 sources=[{"source_id": "S1", "text": sensitive_source}]),
        ]
        for data in invalid:
            with self.subTest(task=data.get("task", "source_review")), patch.object(
                    api, "generate_draft", new_callable=AsyncMock) as generate:
                response = self.client.post("/owner/open-models/draft", json=data, auth=self.auth)
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.headers["cache-control"], "no-store")
                self.assertEqual(response.json()["detail"]["code"], "OPEN_MODEL_INPUT_INVALID")
                self.assertNotIn(sensitive_question, response.text)
                self.assertNotIn(sensitive_source, response.text)
                self.assertNotIn('"input"', response.text)
                generate.assert_not_awaited()

    def test_explicit_authorization_and_known_fields_are_required(self):
        for change in ({"explicit_authorization": False}, {"explicit_authorization": 1},
                       {"explicit_authorization": "true"}, {"endpoint_name": "untrusted"},
                       {"domain": "legal"}):
            with self.subTest(change=change), patch.object(api, "generate_draft", new_callable=AsyncMock) as generate:
                response = self.client.post("/owner/open-models/draft", json=dict(payload(), **change), auth=self.auth)
                self.assertEqual(response.status_code, 422)
                generate.assert_not_awaited()

    def test_cookie_auth_blocks_cross_origin_post(self):
        response = self.client.post("/owner/login", data={"username": self.auth[0], "password": self.auth[1]},
                                    headers={"Origin": "https://testserver"}, follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        with patch.object(api, "generate_draft", new_callable=AsyncMock) as generate:
            response = self.client.post("/owner/open-models/draft", json=payload(),
                                        headers={"Origin": "https://foreign.example"})
            self.assertEqual(response.status_code, 403)
            generate.assert_not_awaited()

    def test_unexpected_error_does_not_echo_model_content_or_credentials(self):
        with patch.object(api, "generate_draft", new_callable=AsyncMock,
                          side_effect=RuntimeError("PRIVATE_PATIENT_PRIVATE_KEY")):
            response = self.client.post("/owner/open-models/draft", json=payload(), auth=self.auth)
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("PRIVATE_PATIENT", response.text)
        self.assertEqual(response.json()["detail"]["code"], "OPEN_MODEL_DRAFT_FAILED")

    def test_bounded_configuration_error_is_returned_without_live_retry(self):
        with patch.object(api, "generate_draft", new_callable=AsyncMock,
                          side_effect=OpenModelError("연결 설정이 필요합니다.", code="OPEN_MODEL_NOT_CONFIGURED", http_status=503)) as generate:
            response = self.client.post("/owner/open-models/draft", json=payload(), auth=self.auth)
        self.assertEqual(response.status_code, 503)
        generate.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
