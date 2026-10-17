"""Owner model workspace with file uploads and browser voice dictation."""

_HTML = '''<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>교육·의료 AI 작업 | 에듀리커버</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#f3f6fa;color:#172b45;font-family:system-ui,sans-serif;line-height:1.6}
main{width:min(900px,calc(100% - 28px));margin:24px auto}header{display:flex;justify-content:space-between;align-items:center}
h1{font-size:27px;margin:18px 0 8px}h2{font-size:19px}p,small{color:#485d75}
section{background:white;border:1px solid #d5dfeb;border-radius:14px;padding:22px;margin:18px 0}
label{display:block;font-weight:700;margin-top:16px}input,select,textarea{display:block;width:100%;font:inherit;padding:11px;border:1px solid #8294ad;border-radius:7px;margin-top:6px}
textarea{min-height:110px;resize:vertical}button{font:inherit;padding:11px 18px;border:0;border-radius:7px;background:#185bc4;color:white;cursor:pointer;margin:14px 8px 0 0}
button.secondary{background:#e9eff8;color:#17437f}button:disabled{opacity:.5;cursor:default}
.attachment-list{padding-left:20px}.attachment-list button{padding:4px 10px;margin:0 0 0 10px}.voice-controls{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.voice-controls p{margin:8px 0}.attachment-controls{margin:14px 0;padding:14px;background:#f3f6fa;border-radius:8px}
.grid{display:grid;grid-template-columns:1fr 2fr;gap:16px}.notice{padding:10px;background:#edf3fc;border-radius:6px}
.source{padding:10px 0;border-bottom:1px solid #e6ebf3}.source label{margin-top:4px}
#status,#model-readiness{white-space:pre-wrap}#result{overflow-wrap:anywhere}#result p{white-space:pre-wrap}
.error{color:#a01919}li{margin:8px 0}a{color:#174d9e}@media(max-width:600px){.grid{grid-template-columns:1fr}section{padding:16px}h1{font-size:24px}}
</style></head><body><main>
<header><strong>EduRecover</strong><a href="/owner">관리 화면</a><a href="/owner/open-models/chutes">별도 직접 호출</a><a href="/owner/open-models/learning">모델 개발·학습 허용 모음</a></header>
<h1>교육·의료 AI 작업</h1>
<p>선택한 8개 계열·25개 모델에 직접 질문하거나 튜터 대화, 문제 출제, 자료 검토를 진행할 수 있습니다. Qwen·DeepSeek·gpt-oss·Gemma·GLM·Mistral·Phi·NVIDIA Nemotron을 포함하며, Amazon Nova 등 AWS 추가 후보는 따로 표시합니다.</p>
<p>GPT-6 Luna는 별도 Azure 서비스로 표시합니다. OpenRouter를 통해 Azure로만 요청하며, 제공자가 입력·응답을 저장하지 않는 조건(ZDR)을 충족하는 경로에만 요청하도록 제한합니다. 실제 호출에는 연결 설정과 소유자 사용 허용이 필요합니다.</p>
<p>Claude는 Haiku 5.5, Gemini는 2.5 Flash-Lite를 별도로 표시합니다. 이 모델과 추가 OpenRouter 연결은 ZDR 경로에만 요청합니다. ZDR은 보관 조건이며, 모델별 경쟁 모델 개발 제한을 없애지 않습니다. 모델의 공개 라이선스와 호출 서비스의 사용 조건은 별도로 적용됩니다.</p>
<p>딥시크 직접 연결을 켜면 V4.1 Flash와 V4 Pro는 딥시크 공식 서버에 요청합니다. 이 두 직접 경로에는 무보관 조건을 요구하지 않으며, 딥시크의 보관·이용 정책이 적용됩니다. Railway 서버의 DEEPSEEK_API_KEY 인증 키가 필요합니다. OpenRouter 키는 대신 사용할 수 없습니다.</p>
<section><form id="draft-form">
<label for="task">할 일</label><select id="task"><option value="direct">모델에 직접 질문</option><option value="tutor">튜터 대화·개념 설명</option><option value="questions">교재 기반 문제 출제</option><option value="source_review">교육·의료 자료 검토</option></select>
<div class="grid"><div id="domain-options"><label for="domain">분야</label><select id="domain"><option value="education">교육 설명자료</option><option value="medical">의료 근거 검토</option></select></div>
<div><label for="model">사용할 모델</label><select id="model" required><option value="">모델을 선택할 수 있도록 연결 정보를 불러오는 중입니다.</option></select></div></div>
<p class="notice" id="model-readiness" role="status" aria-live="polite">모델을 선택하면 연결 상태와 사용 허용 여부를 확인할 수 있습니다.</p>
<div id="direct-options"><p>앱에서 답변 토큰 수를 제한하지 않습니다. 모델 제공자의 처리 한도는 적용됩니다.</p></div>
<div id="question-options" hidden><div class="grid"><div><label for="question-count">문항 수</label><input id="question-count" type="number" value="5" min="1" max="10"></div><div><label for="question-format">문제 형식</label><select id="question-format"><option value="short_answer">서답형</option><option value="multiple_choice">4지선다형</option></select></div></div></div>
<label for="question" id="question-label">튜터에게 물어볼 내용</label>
<textarea id="question" maxlength="4000" required placeholder="질문, 배우고 싶은 개념이나 출제 목적을 입력할 수 있습니다."></textarea>
<div class="voice-controls"><button type="button" id="voice-input" class="secondary" aria-pressed="false">음성 입력 시작</button><p id="voice-status" role="status" aria-live="polite"></p></div>
<div id="attachment-options" class="attachment-controls">
<label for="attachments">사진·파일 첨부</label><input id="attachments" type="file" multiple accept=".png,.jpg,.jpeg,.webp,.pdf,.docx,.txt,.md,.csv,.json,.py,.js,.html,.css,.yaml,.yml" aria-describedby="attachment-help">
<p id="attachment-help">사진(PNG·JPG·WebP), PDF·워드(DOCX), 텍스트·코드 파일을 선택하십시오. 한 번에 최대 4개, 파일 하나당 8MiB(약 8.4MB)까지 첨부할 수 있습니다. 직접 질문을 보낼 때 선택한 모델로 함께 전송합니다.</p>
<p id="attachment-capability" role="status" aria-live="polite"></p>
<ul id="attachment-list" class="attachment-list" aria-label="첨부한 파일"></ul><p id="attachment-status" role="status" aria-live="polite"></p>
</div>
<p id="domain-help"></p><p id="history-info"></p><button type="button" id="new-conversation" class="secondary">새 튜터 대화</button>
<div id="sources"></div><button type="button" id="add-source" class="secondary">자료 추가</button>
<p class="notice" id="task-notice"></p>
<button type="submit" id="generate" disabled>튜터에게 질문하기</button>
<p id="status" role="status" aria-live="polite"></p>
</form></section><section id="result" hidden aria-live="polite"></section>
</main><script>
const byId=id=>document.getElementById(id);
let sourceCount=0, busy=false, tutorHistory=[], modelRows=new Map(), attachmentFiles=[], currentTask='direct';
let recognition=null, listening=false, voiceBase='';
const MAX_ATTACHMENTS=4,MAX_ATTACHMENT_BYTES=8*1024*1024;
const attachmentTypes={png:'image/png',jpg:'image/jpeg',jpeg:'image/jpeg',webp:'image/webp',pdf:'application/pdf',docx:'application/vnd.openxmlformats-officedocument.wordprocessingml.document',txt:'text/plain',md:'text/plain',csv:'text/plain',json:'text/plain',py:'text/plain',js:'text/plain',html:'text/plain',css:'text/plain',yaml:'text/plain',yml:'text/plain'};
function attachmentStatus(text,error=false){byId('attachment-status').textContent=text;byId('attachment-status').className=error?'error':'';}
function fileType(file){return attachmentTypes[file.name.split('.').pop().toLowerCase()]||'';}
function renderAttachments(){
 const list=byId('attachment-list');list.replaceChildren();
 attachmentFiles.forEach((file,index)=>{
  const item=document.createElement('li'),name=document.createElement('span'),remove=document.createElement('button');
  name.textContent=file.name+' ('+Math.ceil(file.size/1024)+'KB)';remove.type='button';remove.className='secondary';remove.textContent='제거';remove.disabled=busy;remove.setAttribute('aria-label',file.name+' 제거');
  remove.addEventListener('click',()=>{if(busy)return;attachmentFiles.splice(index,1);renderAttachments();attachmentStatus('선택한 파일을 제거했습니다.');});
  item.append(name,remove);list.append(item);
 });
}
function selectAttachments(event){
 if(busy)return;
 const incoming=Array.from(event.target.files||[]);event.target.value='';
 if(!incoming.length)return;
 if(attachmentFiles.length+incoming.length>MAX_ATTACHMENTS){attachmentStatus('파일은 최대 4개까지 첨부할 수 있습니다. 먼저 필요 없는 파일을 제거하십시오.',true);return;}
 for(const file of incoming){
  if(!fileType(file)){attachmentStatus('지원하지 않는 파일 형식입니다: '+file.name,true);return;}
  if(file.size===0||file.size>MAX_ATTACHMENT_BYTES){attachmentStatus('빈 파일은 첨부할 수 없으며, 파일 하나의 크기는 8MiB 이하여야 합니다: '+file.name,true);return;}
 }
 attachmentFiles.push(...incoming);renderAttachments();attachmentStatus('파일 '+attachmentFiles.length+'개를 첨부했습니다. 질문을 보내기 전까지 서버에 전송하지 않습니다.');
}
function readAttachment(file){
 return new Promise((resolve,reject)=>{
  const reader=new FileReader();
  reader.onload=()=>{const value=String(reader.result||''),comma=value.indexOf(',');if(comma<0){reject(new Error('첨부파일을 읽지 못했습니다: '+file.name));return;}resolve({filename:file.name,content_type:fileType(file),data:value.slice(comma+1)});};
  reader.onerror=()=>reject(new Error('첨부파일을 읽지 못했습니다: '+file.name));
  reader.onabort=()=>reject(new Error('첨부파일 읽기가 중단되었습니다: '+file.name));
  reader.readAsDataURL(file);
 });
}
function voiceStatus(text,error=false){byId('voice-status').textContent=text;byId('voice-status').className=error?'error':'';}
function updateVoiceButton(){
 const button=byId('voice-input');button.disabled=busy||!recognition;button.textContent=listening?'음성 입력 중지':'음성 입력 시작';button.setAttribute('aria-pressed',String(listening));
}
function stopVoice(){if(recognition&&listening){listening=false;try{recognition.stop();}catch(error){voiceStatus('음성 입력을 마쳤습니다.');}updateVoiceButton();}}
function setupVoice(){
 const Recognition=globalThis.SpeechRecognition||globalThis.webkitSpeechRecognition;
 if(!Recognition){voiceStatus('이 브라우저는 음성 입력을 지원하지 않습니다. 질문을 직접 입력하십시오.');updateVoiceButton();return;}
 recognition=new Recognition();recognition.lang='ko-KR';recognition.continuous=true;recognition.interimResults=true;
 recognition.onstart=()=>{if(busy){try{recognition.stop();}catch(error){}listening=false;updateVoiceButton();return;}listening=true;voiceStatus('듣고 있습니다. 음성 입력을 마치려면 중지하십시오. 자동으로 질문을 보내지 않습니다.');updateVoiceButton();};
 recognition.onresult=event=>{
  if(busy||!listening)return;
  // results is the current session snapshot, not new text to append. Rebuild
  // it so repeated final events and revised interim text cannot accumulate.
  const parts=[];for(let index=0;index<event.results.length;index++){const transcript=event.results[index]?.[0]?.transcript;if(typeof transcript==='string'&&transcript.trim())parts.push(transcript.trim());}
  const transcript=parts.join(' '),text=voiceBase+(voiceBase&&transcript?'\\n':'')+transcript,field=byId('question');field.value=field.maxLength>0?text.slice(0,field.maxLength):text;
 };
 recognition.onerror=event=>{listening=false;const messages={'not-allowed':'마이크 사용이 허용되지 않았습니다. 브라우저에서 마이크 권한을 확인하십시오.','service-not-allowed':'이 브라우저에서 음성 인식 서비스를 사용할 수 없습니다.','audio-capture':'사용 가능한 마이크를 찾지 못했습니다.','no-speech':'인식한 음성이 없습니다. 다시 시작하십시오.','network':'음성 인식 서비스에 연결하지 못했습니다.'};voiceStatus(messages[event.error]||'음성 입력이 중단되었습니다. 질문을 직접 입력하거나 다시 시작하십시오.',true);updateVoiceButton();};
 recognition.onend=()=>{listening=false;updateVoiceButton();if(!byId('voice-status').className.includes('error'))voiceStatus('음성 입력을 마쳤습니다. 내용을 확인한 뒤 질문을 보내십시오.');};
 voiceStatus('음성 인식은 브라우저가 제공하며, 인식을 위해 음성이 브라우저 제공업체로 전송될 수 있습니다. 마이크 사용을 허용한 뒤 시작하십시오. 녹음 파일 첨부는 지원하지 않습니다.');updateVoiceButton();
}
function toggleVoice(){
 if(busy||!recognition)return;
 if(listening){stopVoice();return;}
 voiceBase=byId('question').value.trimEnd();
 try{listening=true;updateVoiceButton();recognition.start();}catch(error){listening=false;voiceStatus('음성 입력을 시작하지 못했습니다. 마이크 권한을 확인한 뒤 다시 시작하십시오.',true);updateVoiceButton();}
}
function addSource(){
 if(sourceCount>=8)return;
 sourceCount++;
 const wrap=document.createElement('div');wrap.className='source';
 const label=document.createElement('label');label.htmlFor='source-'+sourceCount;label.textContent='자료 '+sourceCount+' (S'+sourceCount+')';
 const field=document.createElement('textarea');field.id='source-'+sourceCount;field.dataset.sourceId='S'+sourceCount;field.maxLength=16000;
 field.placeholder='원문과 출처 이름·주소 등을 함께 입력할 수 있습니다.';
 field.addEventListener('input',clearHistory);
 wrap.append(label,field);byId('sources').append(wrap);byId('add-source').disabled=sourceCount>=8;
}
function status(text,error=false){byId('status').textContent=text;byId('status').className=error?'error':'';}
function historyInfo(){
 const turns=tutorHistory.filter(x=>x.role==='user').length;
 byId('history-info').textContent=turns?'이 대화에서 최근 '+turns+'회 질문과 답변을 함께 전달합니다. 대화는 이 화면의 메모리에만 보관됩니다.':'아직 전달할 이전 튜터 대화가 없습니다.';
}
function clearHistory(){tutorHistory=[];historyInfo();}
function saveTurn(question,answer){
 // Explicitly labelled excerpts keep every message within the API bounds.
 const content=answer.length>4000?'[이전 답변의 앞부분 발췌] '+answer.slice(0,3950):answer;
 tutorHistory.push({role:'user',content:question},{role:'assistant',content});
 while(tutorHistory.length>12||tutorHistory.reduce((n,x)=>n+x.content.length,0)>16000)tutorHistory.splice(0,2);
 historyInfo();
}
function setBusy(value){
 if(value)stopVoice();
 busy=value;
 for(const field of byId('draft-form').querySelectorAll('input,select,textarea,button'))field.disabled=value;
 if(!value)byId('add-source').disabled=sourceCount>=8;
 refreshModelSelection();
 setTaskInputState();
 updateVoiceButton();
}
function setTaskInputState(){
 const task=byId('task').value;
 // Hidden numeric fields must not block a different task's form validation.
 byId('domain').disabled=busy||task!=='source_review';
 byId('attachments').disabled=busy||task!=='direct';
 byId('question-count').disabled=busy||task!=='questions';
 byId('question-format').disabled=busy||task!=='questions';
 for(const field of byId('sources').querySelectorAll('textarea'))field.disabled=busy||task==='direct';
}
function updateTask(){
 const task=byId('task').value,direct=task==='direct';
 if(!direct&&attachmentFiles.length){
  if(!globalThis.confirm('첨부파일은 직접 질문에서만 보낼 수 있습니다. 첨부파일을 모두 제거하고 작업을 바꾸시겠습니까?')){byId('task').value=currentTask;return;}
  attachmentFiles=[];byId('attachments').value='';renderAttachments();attachmentStatus('작업을 바꾸면서 첨부파일을 제거했습니다.');
 }
 currentTask=task;stopVoice();
 if(task!=='source_review')byId('domain').value='education';
 setTaskInputState();
 byId('domain-options').hidden=direct;byId('direct-options').hidden=!direct;
 byId('attachment-options').hidden=!direct;
 byId('sources').hidden=direct;byId('add-source').hidden=direct;
 if(direct)byId('question').removeAttribute('maxlength');else byId('question').maxLength=4000;
 byId('question-options').hidden=task!=='questions';
 byId('new-conversation').hidden=task!=='tutor';byId('history-info').hidden=task!=='tutor';
 byId('generate').textContent=direct?'선택한 모델에 질문하기':task==='tutor'?'튜터에게 질문하기':task==='questions'?'문제·정답·해설 만들기':'자료 검토 초안 만들기';
 byId('question-label').textContent=direct?'선택한 모델에 물어볼 내용':task==='tutor'?'튜터에게 물어볼 내용':task==='questions'?'출제할 과목·학년·범위·난이도':'설명하거나 검토할 내용';
 byId('domain-help').textContent=direct?'질문 한 개를 선택한 모델에 전달하고 답변을 그대로 표시합니다. 기본 학습 모델을 포함한 25개 모델에 사용할 수 있으며, 실제 호출에는 각 모델의 연결 설정과 인증 정보가 필요합니다.':task==='tutor'?'질문에 답하고 개념을 설명하며 후속 질문을 제안합니다. 이전 대화는 최대 12개 메시지·총 16,000자만 사용합니다.':task==='questions'?'교재 원문을 바탕으로 최대 10문항의 문제·정답·해설·근거 번호를 만듭니다. 교사의 검토 전에는 확정 답안이 아닙니다.':byId('domain').value==='medical'?'부작용·과잉진료 등 질문과 자료의 근거, 자료 사이의 차이, 의료진에게 확인할 질문을 정리합니다.':'교육 개념·설명·교수자료를 교사가 검토할 초안으로 정리합니다.';
 byId('task-notice').textContent=direct?'질문하기를 실행하면 입력한 질문과 첨부한 파일을 선택한 모델 하나에 전송합니다. 답변은 생성 초안이며 공식 출처 대조와 독립 AI 검증을 수행하지 않습니다.':'선택한 모델 하나에 입력한 내용만 전송합니다. 튜터는 자료 없이 질문할 수 있으며, 출처가 필요한 설명에는 자료를 함께 넣을 수 있습니다. 문제 출제와 자료 검토에는 원문이 필요합니다.';
 byId('result').hidden=true;clearHistory();
}
const familyNames={deepseek:'DeepSeek · 딥시크',qwen:'Qwen · 큐웬',nova:'Amazon Nova · 아마존 노바','gpt-oss':'gpt-oss',gemma:'젬마',glm:'GLM',mistral:'미스트랄',phi:'Phi',nemotron:'네모트론',openai:'OpenAI',anthropic:'Claude · 클로드',gemini:'Gemini · 제미나이'};
const readinessLabels={not_configured:'연결 설정 필요',missing_credentials:'인증 정보 필요',disabled:'호출 기능 꺼짐',configured_not_verified:'호출 설정됨 · 실제 응답 미확인'};
const permissionLabels={ALLOWED:'소유자 사용 허용',HOLD:'소유자가 전체 AI 호출을 중지함',OWNER_PERMISSION_REQUIRED:'소유자 사용 허용 필요',BLOCKED:'소유자가 이 모델 계열의 호출을 차단함',RESTRICTED_VERIFY_ONLY:'검증 전용 허용 · 질문 생성 보류',CONTROL_CONFIG_INVALID:'소유자 사용 제어 설정 확인 필요'};
function modelReady(row){return !!row&&row.configured===true&&row.enabled===true&&row.permitted===true&&(!row.readiness_state||row.readiness_state==='configured_not_verified');}
function connectionLabel(row){return readinessLabels[row.readiness_state]||(row.configured?(row.enabled?'호출 설정됨 · 실제 응답 미확인':'호출 기능 꺼짐'):'연결 설정 필요');}
function permissionLabel(row){return permissionLabels[row.permission_state]||(row.permitted===true?'소유자 사용 허용':'소유자 설정으로 사용 보류');}
function modelExplanation(row){
 if(!row)return '사용할 모델을 선택하십시오. 연결 준비가 끝나지 않은 모델도 선택하여 필요한 설정을 확인할 수 있습니다.';
 const lines=[row.display_name,'연결 상태: '+connectionLabel(row),'사용 권한: '+permissionLabel(row)];
 if(row.route_label)lines.push('호출 경로: '+row.route_label);
 if(row.policy_label)lines.push('개발·학습 허용 범위: '+row.policy_label);
 if(row.license)lines.push('공개 가중치 라이선스: '+row.license);
 if(row.policy_source)lines.push('확인 근거: '+row.policy_source);
 if(row.setup_note)lines.push(row.setup_note);
 if(row.required_api_key_env&&row.readiness_state==='missing_credentials')lines.push('서버에 필요한 인증 키 설정 이름: '+row.required_api_key_env);
 if(row.readiness_state==='missing_credentials')lines.push('해당 모델 서버의 인증 키가 필요합니다.');
 else if(!row.configured)lines.push('모델 서버 주소와 서버에서 사용하는 모델 이름의 연결 설정이 필요합니다.');
 else if(!row.enabled)lines.push('모델 호출 기능을 켜는 설정이 필요합니다.');
 if(row.permitted!==true)lines.push('소유자의 모델 사용 허용 설정을 확인하십시오.');
 if(modelReady(row))lines.push('질문하기를 실행하면 이 모델 하나에 요청합니다. 실제 응답 성공 여부는 아직 확인하지 않았습니다.');
 else lines.push('상태 확인을 위한 선택은 가능하며, 필요한 설정이 끝나기 전에는 질문을 전송하지 않습니다.');
 return lines.join('\\n');
}
function refreshModelSelection(){
 const row=modelRows.get(byId('model').value);
 byId('generate').disabled=busy||!modelReady(row);
 byId('model-readiness').textContent=modelExplanation(row);
 byId('attachment-capability').textContent=row?(row.supports_images===true?'선택한 모델은 사진 입력을 지원합니다.':'선택한 모델은 사진 입력이 확인되지 않아 사진을 전송하지 않습니다. 문서·텍스트 파일은 첨부할 수 있습니다.'):'사진을 첨부하려면 사진 입력을 지원하는 모델을 선택하십시오.';
}
async function loadModels(){
 try{
  const response=await fetch('/owner/open-models/catalog',{credentials:'same-origin',cache:'no-store'});
  if(!response.ok)throw new Error('모델 연결 정보를 불러오지 못했습니다. 관리자 로그인과 연결 설정을 확인해야 합니다.');
  const data=await response.json();const select=byId('model'),previous=select.value;select.replaceChildren();modelRows=new Map();
  const first=document.createElement('option');first.value='';first.textContent='사용할 모델을 선택할 수 있습니다.';select.append(first);
  const groups={};let usableModels=0;
  for(const row of data.models){
   const additional=row.selection_group==='aws_additional',cloud=row.selection_group==='cloud_service',groupKey=row.family+(additional?'-aws':cloud?'-cloud':'');
   let group=groups[groupKey];if(!group){group=document.createElement('optgroup');group.label=(familyNames[row.family]||row.family)+(additional?' · 추가 서비스':cloud?(row.family==='openai'?' · Azure 서비스':' · ZDR 서비스'):'');groups[groupKey]=group;select.append(group);}
   const option=document.createElement('option');option.value=row.model_key;
   modelRows.set(row.model_key,row);const ready=modelReady(row);
   option.textContent=row.display_name+' · '+connectionLabel(row)+(row.permitted===true?'':' · '+permissionLabel(row));group.append(option);if(ready)usableModels++;
  }
  select.value=modelRows.has(previous)?previous:'';refreshModelSelection();
  status(usableModels?'모든 등록 모델을 선택하여 상태를 확인할 수 있습니다. 질문 전송은 선택한 모델의 연결 설정과 사용 권한이 준비된 경우에만 가능합니다.':'모든 등록 모델을 선택하여 필요한 설정을 확인할 수 있습니다. 아직 질문을 전송할 수 있는 모델은 없습니다.');
 }catch(error){modelRows=new Map();refreshModelSelection();status(error.message||'모델 목록을 불러오지 못했습니다.',true);}
}
function addText(parent,tag,text){const node=document.createElement(tag);node.textContent=text;parent.append(node);return node;}
function references(ids){return ids.length?' ['+ids.join(', ')+']':'';}
function renderList(box,title,items){if(!items.length)return;addText(box,'h2',title);const list=document.createElement('ul');for(const item of items)addText(list,'li',item);box.append(list);}
function renderResult(result){
 const box=byId('result');box.replaceChildren();box.hidden=false;
 const task=result.task||'source_review',draft=result.draft;
 addText(box,'h2',task==='direct'?'모델 답변 초안':task==='tutor'?'튜터 답변':task==='questions'?'문제·정답·해설 초안':result.domain==='medical'?'의료 근거 검토 초안':'교육 설명자료 초안');
 addText(box,'p','사용 모델: '+result.model_key);
 if(task==='direct'){
  addText(box,'p',result.answer);
 }else if(task==='tutor'){
  addText(box,'p',draft.answer+references(draft.answer_source_ids));renderList(box,'이어서 생각해 볼 질문',draft.follow_up_questions);
 }else if(task==='questions'){
  addText(box,'h2',draft.title);
  for(const item of draft.items){
   addText(box,'h2',item.number+'. '+item.question);
   if(item.options.length){const options=document.createElement('ol');for(const option of item.options)addText(options,'li',option);box.append(options);}
   const details=document.createElement('details');addText(details,'summary','정답·해설 보기');
   addText(details,'p','정답: '+item.answer);addText(details,'p','해설: '+item.explanation+references(item.source_ids));box.append(details);
  }
 }else{
  addText(box,'p',draft.summary+references(draft.summary_source_ids));
  const points=document.createElement('ul');for(const point of draft.points)addText(points,'li',point.text+references(point.source_ids));box.append(points);
  renderList(box,'추가로 확인할 질문',draft.questions);
 }
 if(task!=='direct')renderList(box,'확인되지 않은 내용',draft.uncertainties);
 addText(box,'p','검토 상태: 생성 초안 · 공식 출처 대조 및 독립 AI 검증 미실시 · 사람의 검토 필요');
}
byId('add-source').addEventListener('click',addSource);
byId('attachments').addEventListener('change',selectAttachments);
byId('voice-input').addEventListener('click',toggleVoice);
byId('task').addEventListener('change',updateTask);byId('domain').addEventListener('change',updateTask);byId('model').addEventListener('change',()=>{clearHistory();refreshModelSelection();});
byId('new-conversation').addEventListener('click',()=>{clearHistory();byId('result').hidden=true;byId('question').value='';status('새 튜터 대화를 시작할 수 있습니다.');});
byId('draft-form').addEventListener('submit',async event=>{
 event.preventDefault();if(busy)return;
 const selected=modelRows.get(byId('model').value);
 if(!modelReady(selected)){refreshModelSelection();status(modelExplanation(selected),true);return;}
 setBusy(true);byId('result').hidden=true;
 try{
  const task=byId('task').value,question=byId('question').value.trim();
  const sources=Array.from(byId('sources').querySelectorAll('textarea')).filter(x=>x.value.trim()).map(x=>({source_id:x.dataset.sourceId,text:x.value.trim()}));
  if(!question)throw new Error('질문이나 작업 목적을 입력해야 합니다.');
  if(task==='direct'&&attachmentFiles.some(file=>fileType(file).startsWith('image/'))&&selected.supports_images!==true)throw new Error('선택한 모델은 사진 입력을 지원하지 않습니다. 사진을 제거하거나 사진 입력을 지원하는 모델을 선택하십시오.');
  if(task!=='direct'&&task!=='tutor'&&!sources.length)throw new Error('문제 출제·자료 검토에는 원문을 한 개 이상 입력해야 합니다.');
  if(task!=='direct'&&sources.reduce((total,x)=>total+x.text.length,0)>48000)throw new Error('자료 전체 길이는 48,000자 이내여야 합니다.');
  const body=task==='direct'?{model_key:byId('model').value,prompt:question,explicit_authorization:true}:{model_key:byId('model').value,task,domain:byId('domain').value,question,sources,explicit_authorization:true};
  if(task==='direct'&&attachmentFiles.length){status('첨부파일을 읽고 있습니다.');body.attachments=await Promise.all(attachmentFiles.map(readAttachment));}
  if(task==='tutor')body.history=tutorHistory;
  if(task==='questions'){body.question_count=Number(byId('question-count').value);body.question_format=byId('question-format').value;}
  status('선택한 모델에 요청하고 있습니다.');
  const response=await fetch(task==='direct'?'/owner/open-models/call':'/owner/open-models/draft',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const data=await response.json();if(!response.ok)throw new Error(data.detail?.message||'요청을 완료하지 못했습니다. 입력과 모델 연결을 확인해야 합니다.');
  renderResult(data);
  if(task==='tutor')saveTurn(question,data.draft.answer);
  status('선택한 모델의 응답을 받았습니다. 생성 결과와 근거는 별도로 검토해야 합니다.');
 }catch(error){status(error.message||'요청을 완료하지 못했습니다.',true);}finally{setBusy(false);}
});
addSource();setupVoice();updateTask();loadModels();
</script></body></html>'''


def chutes_workspace_html() -> str:
    """A separate direct-only workspace; no existing route is replaced."""
    html = _HTML.replace(
        '<a href="/owner/open-models/chutes">별도 직접 호출</a>',
        '<a href="/owner/open-models">기존 모델 작업</a>',
    )
    intro_start = html.index('<h1>')
    intro_end = html.index('<section><form')
    html = html[:intro_start] + '''<h1>공개 모델 별도 직접 호출</h1>
<p>Railway 서버에서 Chutes의 9개 제공판 중 선택한 하나에 직접 요청합니다. 제공판은 같은 계열이라도 버전·날짜·수치 정밀도가 다를 수 있는 모델입니다. 기존 27개 목록과 OpenRouter 연결은 그대로 유지합니다. 목록 확인일: 2026-10-17.</p>
<p>별도 인증 키 CHUTES_API_KEY가 서버에 필요합니다. OpenRouter 키나 DeepSeek 공식 API 키를 대신 사용하지 않습니다. 키 등록 여부와 실제 응답 성공 여부를 구분해 표시합니다.</p>
<p>모델 파일의 사용허가(라이선스)는 모델마다 다르며 MIT·Apache-2.0 등을 따릅니다. 확인한 Chutes 사용 조건에서는 경쟁 모델 개발·학습을 명시적으로 금지하는 조항을 찾지 못했습니다. 이는 모든 목적에 대한 허가나 법적 보증은 아닙니다. 제공자는 프로그램 호출(API)의 입력·응답을 저장하지 않는다고 설명합니다.</p>
<p><a href="https://chutes.ai/terms" target="_blank" rel="noopener noreferrer">Chutes 사용 조건</a> · <a href="https://chutes.ai/privacy" target="_blank" rel="noopener noreferrer">개인정보 처리 조건</a> · <a href="https://chutes.ai/app" target="_blank" rel="noopener noreferrer">Chutes 계정·키 관리</a></p>
<p>문서·텍스트 파일과 음성 받아쓰기를 사용할 수 있습니다. 사진 지원 여부는 선택한 모델의 설명에 표시합니다. 앱이 답변 길이를 지정하지 않으면 선택한 제공판의 기본값과 처리 한도가 적용됩니다.</p>
<section aria-labelledby="chutes-list-heading"><h2 id="chutes-list-heading">별도로 연결한 9개 제공판</h2><p>FP8·NVFP4는 모델 수치를 저장·계산하는 정밀도 이름입니다. 0731은 제공판 이름에 포함된 날짜 식별자입니다. 원래 모델과 다른 부분을 아래에 표시합니다.</p><p id="chutes-count" role="status">제공판 목록을 불러오는 중입니다.</p><ul id="chutes-model-list"></ul></section>
''' + html[intro_end:]
    html = html.replace(
        '질문 한 개를 선택한 모델에 전달하고 답변을 그대로 표시합니다. 기본 학습 모델을 포함한 25개 모델에 사용할 수 있으며, 실제 호출에는 각 모델의 연결 설정과 인증 정보가 필요합니다.',
        '선택한 Chutes 제공판 하나에만 질문을 보내고 답변을 그대로 표시합니다. 연결 설정을 마쳤더라도 실제로 응답을 받는지는 질문을 보내 확인해야 합니다.',
    )
    html = html.replace(
        " if(row.route_label)lines.push('호출 경로: '+row.route_label);",
        " if(row.original_model_key)lines.push('원래 목록의 모델: '+row.original_model_key);\n"
        " if(row.version)lines.push('제공판 버전: '+row.version);\n"
        " if(row.precision)lines.push('수치 정밀도: '+row.precision);\n"
        " if(row.difference)lines.push('원래 모델과의 차이: '+row.difference);\n"
        " if(row.catalog_verified_on)lines.push('공개 목록 확인일: '+row.catalog_verified_on);\n"
        " if(row.route_label)lines.push('호출 경로: '+row.route_label);",
    )
    html = html.replace(
        "  select.value=modelRows.has(previous)?previous:'';refreshModelSelection();",
        "  renderChutesCollection(data.models);\n  select.value=modelRows.has(previous)?previous:'';refreshModelSelection();",
    )
    html = html.replace(
        "async function loadModels(){",
        r'''function renderChutesCollection(rows){
 const list=byId('chutes-model-list');if(!list)return;list.replaceChildren();
 byId('chutes-count').textContent='총 '+rows.length+'개 · 목록 등록과 호출 설정을 표시하며, 실제 응답 성공을 뜻하지 않습니다.';
 for(const row of rows){
  const item=document.createElement('li'),name=document.createElement('strong'),detail=document.createElement('p');
  name.textContent=row.display_name;
  detail.textContent=[row.version?'버전 '+row.version:'',row.precision?'정밀도 '+row.precision:'',row.catalog_verified_on?'목록 확인일 '+row.catalog_verified_on:'',row.difference||'',connectionLabel(row)].filter(Boolean).join(' · ');
  item.append(name,detail);list.append(item);
 }
}
async function loadModels(){''',
    )
    for option in (
        '<option value="tutor">튜터 대화·개념 설명</option>',
        '<option value="questions">교재 기반 문제 출제</option>',
        '<option value="source_review">교육·의료 자료 검토</option>',
    ):
        html = html.replace(option, '')
    html = html.replace(
        '<div id="direct-options"><p>앱에서 답변 토큰 수를 제한하지 않습니다. 모델 제공자의 처리 한도는 적용됩니다.</p></div>',
        '<div id="direct-options"><label for="chutes-output-tokens">답변 길이 직접 지정 (선택)</label>'
        '<input id="chutes-output-tokens" type="number" min="1" step="1" inputmode="numeric" '
        'placeholder="비워두면 제공자 기본값 적용">'
        '<p>토큰은 AI가 글을 처리하는 단위입니다. 필요한 답변 길이를 양의 정수로 지정할 수 있습니다. '
        '앱에서 상한을 정하지 않으며, 제공자의 문맥·출력 한도는 적용됩니다.</p></div>',
    )
    html = html.replace(
        "  if(task==='direct'&&attachmentFiles.length)",
        r"""  const tokenOverride=byId('chutes-output-tokens').value.trim();
  if(tokenOverride){const amount=Number(tokenOverride);if(!/^[1-9]\d*$/.test(tokenOverride)||!Number.isSafeInteger(amount))throw new Error('답변 길이는 양의 정수로 입력하십시오.');body.max_tokens=amount;}
  if(task==='direct'&&attachmentFiles.length)""",
    )
    # All form requests stay on this named route. There is no fallback to the
    # existing call API, including for an injected unsupported task value.
    for suffix in ('catalog', 'call', 'draft'):
        html = html.replace(
            '/owner/open-models/' + suffix,
            '/owner/open-models/chutes/' + suffix,
        )
    return html


def learning_workspace_html() -> str:
    """Collect reviewed development permissions without imposing ZDR."""
    html = chutes_workspace_html().replace(
        '<a href="/owner/open-models/learning">모델 개발·학습 허용 모음</a>', '',
    )
    intro_start = html.index('<h1>')
    intro_end = html.index('<section><form')
    html = html[:intro_start] + '''<h1>자체 모델 개발·학습 허용 모델 모음</h1>
<p>다른 모델 개발·학습을 허용하는 공식 API와 공개 가중치를 먼저 모았습니다. 입력·응답을 보관하지 않는 조건(ZDR)은 이번 목록의 포함 조건으로 삼지 않습니다. 이 조건까지 확인된 경로가 있으면 우선 사용할 수 있으며, 아직 ZDR 충족을 보장하지 않습니다.</p>
<p>DeepSeek V4.1 Flash·V4 Pro는 공식 API 약관 제4.2조에서 출력으로 다른 모델을 학습·증류하는 것을 명시적으로 허용합니다. 기존 전용 키로 공식 서버에 직접 요청하며, OpenRouter를 거치지 않습니다. 실제 응답 성공 여부는 별도로 확인해야 합니다.</p>
<p>나머지는 공개 가중치를 직접 운영할 때 수정·파생 모델 개발을 허용하는 모델입니다. 가중치는 모델 실행에 필요한 파일입니다. 직접 실행할 서버가 아직 연결되지 않은 모델은 목록에 보관하며 질문을 보내지 않습니다. 공개 라이선스가 외부 API 제공자의 계약까지 바꾸지는 않습니다.</p>
<p>모든 모델에는 해당 라이선스·약관, 관련 법령과 제3자 권리가 적용됩니다. 기존 OpenRouter·Chutes 연결은 유지합니다. 검토일: 2026-10-17.</p>
<p><a href="https://cdn.deepseek.com/policies/en-US/deepseek-open-platform-terms-of-service.html" target="_blank" rel="noopener noreferrer">DeepSeek 공식 API 약관</a> · <a href="/owner/open-models/chutes">Chutes 별도 직접 호출</a></p>
<section aria-labelledby="learning-list-heading"><h2 id="learning-list-heading">허용 근거와 연결 상태</h2><p id="learning-count" role="status">모델 목록을 불러오는 중입니다.</p><ul id="learning-model-list"></ul></section>
''' + html[intro_end:]
    html = html.replace('chutes-output-tokens', 'learning-output-tokens')
    html = html.replace('/owner/open-models/chutes/' , '/owner/open-models/learning/')
    html = html.replace(
        '선택한 Chutes 제공판 하나에만 질문을 보내고 답변을 그대로 표시합니다. 연결 설정을 마쳤더라도 실제로 응답을 받는지는 질문을 보내 확인해야 합니다.',
        '이 모음에서 호출 준비가 된 모델에만 질문을 보냅니다. 자체 실행 서버가 없는 공개 가중치 모델은 목록과 허용 근거만 보관합니다.',
    )
    html = html.replace('  renderChutesCollection(data.models);\n', '')
    html = html.replace(
        "  select.value=modelRows.has(previous)?previous:'';refreshModelSelection();",
        "  renderLearningCollection(data.models);\n  select.value=modelRows.has(previous)?previous:'';refreshModelSelection();",
    )
    html = html.replace(
        "async function loadModels(){",
        r'''function renderLearningCollection(rows){
 const list=byId('learning-model-list');list.replaceChildren();
 const apiCount=rows.filter(row=>row.policy_scope==='official_api_outputs').length;
 byId('learning-count').textContent='총 '+rows.length+'개 · 공식 API 출력 학습 허용 '+apiCount+'개 · 공개 가중치 직접 운영 허용 '+(rows.length-apiCount)+'개';
 for(const row of rows){
  const item=document.createElement('li'),name=document.createElement('strong'),detail=document.createElement('p');
  name.textContent=row.display_name;
  detail.textContent=row.policy_label+' · '+(row.license||'공식 API 약관')+' · '+(row.policy_scope==='self_hosted_weights'?'자체 실행 서버 미연결':connectionLabel(row));
  item.append(name,detail);
  if(row.policy_source&&row.policy_source.startsWith('https://')){
   const link=document.createElement('a');link.textContent='공식 허용 근거';link.href=row.policy_source;link.target='_blank';link.rel='noopener noreferrer';item.append(link);
  }
  list.append(item);
 }
}
async function loadModels(){''',
    )
    return html

