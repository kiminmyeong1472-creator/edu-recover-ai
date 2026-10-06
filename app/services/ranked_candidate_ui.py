"""Teacher-only workbench for ranked hypotheses and verified feedback.

This module renders a page. Session ownership and teacher authentication must
be checked by the route before calling ``candidate_workbench_html``.
"""

from __future__ import annotations

import json


def _script_json(value: object) -> str:
    """Keep user-controlled strings inside a JSON literal, never HTML markup."""
    return (
        json.dumps(value, ensure_ascii=False)
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def candidate_workbench_html(
    session_id: str, teacher_token: str, base_css: str, base_js: str
) -> str:
    """Render the ranked-candidate workbench using trusted application assets."""
    config = _script_json({"sessionId": session_id, "teacherToken": teacher_token})
    return (
        _HTML.replace("/* APPLICATION_CSS */", base_css, 1)
        .replace("/* APPLICATION_JS */", base_js, 1)
        .replace("/* WORKBENCH_CONFIG */", config, 1)
    )


_HTML = r"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>EduRecover 후보 탐색·검증</title>
<style>/* APPLICATION_CSS */
.candidate-main{max-width:1180px;margin:auto;padding:20px}
.candidate-main h1{font-size:clamp(24px,4vw,34px);margin-top:0}
.candidate-main h2{font-size:21px;margin-top:0}
.candidate-main p{line-height:1.65}
.candidate-main label{display:block;margin-top:12px;font-weight:600}
.candidate-main textarea{resize:vertical;min-height:84px}
.candidate-main .json-editor{font:14px/1.6 ui-monospace,monospace;min-height:290px}
.candidate-layout{display:grid;grid-template-columns:minmax(0,1fr) minmax(250px,.65fr);gap:16px}
.candidate-help{font-size:14px;line-height:1.6;color:var(--muted,#a5b4c7);margin:4px 0 10px}
.candidate-actions{display:flex;flex-wrap:wrap;gap:10px;margin-top:14px}
.candidate-status{white-space:pre-wrap;line-height:1.6;padding:12px;border-radius:10px;margin:12px 0;background:#0f172a;border:1px solid var(--line,#2b3955)}
.candidate-status:empty{display:none}
.candidate-status.error{border-color:var(--bad,#fb7185)}
.candidate-status.success{border-color:var(--ok,#34d399)}
.candidate-meta{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px}
.candidate-text{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.65}
.candidate-review{border-top:1px solid var(--line,#2b3955);padding-top:12px;margin-top:14px}
.candidate-check{display:flex!important;align-items:flex-start;gap:8px;font-weight:400!important;line-height:1.55}
.candidate-check input{width:auto;flex-shrink:0;margin-top:4px}
.candidate-main button:disabled{opacity:.5;cursor:not-allowed}
.candidate-main pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#0f172a;padding:12px;border-radius:10px;font-size:13px}
.candidate-small{font-size:13px;color:var(--muted,#a5b4c7);overflow-wrap:anywhere}
.candidate-batch{margin:20px 0 28px}
.candidate-policy-note{border-left:3px solid var(--warn,#fbbf24);padding-left:14px}
@media(max-width:850px){.candidate-layout{grid-template-columns:1fr}.candidate-main{padding:14px}}
</style></head><body>
<div class="top"><b>EduRecover 후보 탐색·검증</b><a id="backLink" class="btn" href="/classroom">교사 콘솔</a></div>
<main class="candidate-main">
<section class="card"><h1>순위 밖의 답도 근거로 확인합니다</h1>
<p><strong>모델 순위는 정답 순위가 아닙니다.</strong> 1위 후보가 틀리고 14위 후보가 맞을 수도 있습니다. 낮은 순위의 후보도 탐색 대상으로 남기고, 실제 근거를 확인한 뒤 결과를 기록합니다.</p>
<p class="candidate-help">탐색 허용범위는 살펴볼 후보를 정하는 값입니다. 정답 인정 기준을 낮추거나 환각을 정답으로 바꾸는 값이 아닙니다. 근거가 없거나 충돌하면 미확정으로 남깁니다.</p><p class="candidate-help">① 오개념 원인 추정, ② 답안·주장의 정확성, ③ 학생의 무도움 학습 효과는 서로 다른 판단입니다. 후보 내용이 맞다고 확인되어도 학생의 오개념 원인이나 학습 효과까지 확정된 것은 아닙니다.</p></section>
<section class="candidate-layout">
<div class="card"><h2>1. 후보 묶음 만들기</h2>
<label for="contextRef">문제·상황 참조</label><input id="contextRef" maxlength="240" placeholder="예: 영문법 수의 일치 · 문항 12">
<p class="candidate-help">같은 문제나 상황에서 나온 후보를 한 묶음으로 기록합니다. 학생 실명이나 개인정보를 입력하지 마세요.</p>
<details open style="margin:16px 0"><summary><strong>AI로 대안 후보 생성</strong></summary>
<label for="provider">호출할 AI</label><select id="provider"><option value="openai">GPT · OpenAI</option><option value="claude">Claude · Anthropic</option><option value="gemini">Gemini · Google</option></select>
<label for="taskText">검토할 문제와 요청</label><textarea id="taskText" rows="3" maxlength="5000" placeholder="예: 문장 구조를 분석하고 오답 원인 가설과 대안 풀이를 14개 제시"></textarea>
<label for="sourceText">사용할 원자료</label><textarea id="sourceText" rows="4" maxlength="20000" placeholder="검토할 문장·문항·근거 자료를 적어 주세요."></textarea>
<label for="studentResponse">학생 응답 (선택)</label><textarea id="studentResponse" rows="2" maxlength="5000" placeholder="실명이나 개인정보 없이 답안만 적어 주세요."></textarea>
<label for="generationCount">생성할 후보 수</label><input id="generationCount" type="number" min="1" max="50" step="1" value="14">
<label for="generationSubject">과목</label><input id="generationSubject" value="영어" maxlength="100"><label for="generationGrade">학년</label><input id="generationGrade" value="1학년" maxlength="100">
<label class="candidate-check"><input id="reasonContextAllowed" type="checkbox">검수하고 학습 사용을 허용한 이유를 다음 AI 후보 생성에 사용</label>
<label class="candidate-check"><input id="generationAuthorized" type="checkbox">선택한 AI에 위 문제·원자료·응답을 전달하여 후보를 생성하도록 승인합니다.</label>
<p class="candidate-help">AI 호출에는 설정된 이용요금이 발생할 수 있습니다. 생성 순서와 AI가 스스로 정한 순위는 정답 확률이 아닙니다. AI가 측정 점수를 제공하지 않으면 점수를 만들어 붙이지 않습니다. 서버 설정이나 운영 통제로 호출이 보류되면 아래의 수동 입력과 교사 직관을 사용할 수 있습니다.</p>
<button id="generateButton" class="btn primary" type="button">선택한 AI로 후보 생성·기록</button><div id="generateStatus" class="candidate-status" role="status" aria-live="polite"></div></details>
<label for="candidateJson">후보 목록 (직접 입력·가져오기)</label><textarea id="candidateJson" class="json-editor" spellcheck="false" aria-describedby="jsonHelp"></textarea>
<p class="candidate-help">외부에서 받은 후보를 직접 옮기거나 교사의 직관을 함께 기록할 수 있습니다. 자동 생성된 후보도 검증 전에는 학생에게 공개되지 않습니다.</p><p id="jsonHelp" class="candidate-help">JSON은 목록을 저장하는 글 형식입니다. 모델 후보마다 candidate_id(식별자), rank(모델 순위), model_score(측정 점수가 있으면 0~1, 없으면 null), text(내용), evidence_refs(근거 참조 목록)를 적습니다. concept_refs는 연결된 개념 식별자 목록이며 hypothesis_type은 후보 유형입니다. 교사 직관은 origin을 HUMAN_INSIGHT로, rank와 model_score를 null로 적습니다. 점수는 검증된 정답 확률이 아닙니다.</p>
<label for="insightText">교사의 직관·통찰 후보</label><textarea id="insightText" rows="3" placeholder="모델이 떠올리지 못한 원인이나 풀이를 적어 주세요."></textarea><p class="candidate-help">교사 직관에는 임의의 모델 순위나 확률을 붙이지 않습니다. 직관 후보도 실제 근거로 검증해야 합니다.</p><button id="insightButton" class="btn" type="button">직관 후보를 목록에 추가</button><div class="candidate-actions"><button id="exampleButton" class="btn" type="button">14개 가설 예시 넣기</button><button id="createButton" class="btn primary" type="button">후보 묶음 기록</button></div>
<div id="createStatus" class="candidate-status" role="status" aria-live="polite"></div></div>
<div class="card"><h2>탐색 허용범위</h2>
<label for="maxRank">살펴볼 최대 순위</label><input id="maxRank" type="number" min="1" max="100" step="1" value="14">
<p class="candidate-help">예: 14를 선택하면 순위 기준으로 14위까지 탐색할 수 있습니다.</p>
<label for="scoreMargin">최고 점수와의 허용 차이</label><input id="scoreMargin" type="number" min="0" max="1" step="0.01" value="1.0">
<p class="candidate-help">0~1 사이의 값입니다. 넓힐수록 점수가 낮은 후보도 함께 살펴봅니다. 실제 점수가 없는 후보에 가짜 점수 조건을 붙이지 않으며, 기록된 순위·포함 지정에 따라 탐색합니다.</p>
<label for="includeIds">추가로 포함할 후보 식별자</label><textarea id="includeIds" rows="3" placeholder="예: grammar-14"></textarea>
<p class="candidate-help">순위·점수 범위 밖이어도 확인하려는 후보를 한 줄에 하나씩 적습니다. 포함되었다는 사실만으로 정답이나 공개 자료가 되지 않습니다.</p>
<label for="verificationScope">이번 후보에서 확인해야 할 것</label><select id="verificationScope"><option value="ANSWER,EXPLANATION">답과 설명을 각각 확인</option><option value="ANSWER,EXPLANATION,ACTUAL_CAUSE">답·설명·학생의 실제 오류 원인을 각각 확인</option><option value="ANSWER">답만 확인 (원인·설명 확정 아님)</option></select><p class="candidate-help">답이 맞는지, 설명이 성립하는지, 실제 학생 오류의 원인인지는 서로 다른 판단입니다. 실제 원인은 학생 관찰과 대조 또는 도움 없는 별도 재검사로 확인합니다.</p><label for="verificationKind">탐색 전에 고정할 검증 기준</label><select id="verificationKind"><option value="EVIDENCE">실제 근거와 이유로 검증</option><option value="NUMERIC">수치 기준값과 절대오차로 검증</option></select><label for="criteriaRef">검증 기준 참조</label><input id="criteriaRef" maxlength="500" value="교사가 확인한 실제 문제·상황 근거"><div id="numericCriteria" hidden><label for="expectedValue">기준 수치</label><input id="expectedValue" type="number" step="any"><label for="absoluteTolerance">허용 절대오차</label><input id="absoluteTolerance" type="number" min="0" step="any" value="0"></div><p class="candidate-help">수치 오차는 실제 측정값과 기준값의 차이를 허용하는 값입니다. 후보를 볼 범위를 넓히는 탐색 허용범위와는 별개입니다. 검증 기준은 결과를 보기 전에 정하며, 결과에 맞춰 바꾸지 않습니다.</p><p class="candidate-help">후보를 만든 뒤의 평가 결과는 원래 순위·점수와 함께 남습니다. 결과에 맞춰 원래 예측을 바꾸지 않습니다.</p></div>
</section>
<section class="card"><h2>관계와 공동조건을 함께 살펴보기</h2><p class="candidate-help">교사가 공개한 고정 학습경로의 선수 관계, 같은 조건을 쓰는 연결, 검토한 유사 관계를 함께 읽습니다. 유사하다는 이유나 오답 하나로 원인을 확정하지 않으며, 관찰이 없으면 미확정으로 남깁니다.</p><label class="candidate-check"><input id="useCauseMap" type="checkbox">검토·공개된 학습경로의 관계와 공동조건을 이 후보 탐색에 사용</label><label for="focusNodes">집중해서 볼 개념 식별자 (선택)</label><textarea id="focusNodes" rows="2" placeholder="한 줄에 하나씩, 비워 두면 현재 경로의 목표 개념"></textarea><label for="conditionObservations">조건별 관찰 기록</label><textarea id="conditionObservations" rows="3" spellcheck="false">[]</textarea><p class="candidate-help">관찰 목록 형식: [{"condition_key":"조건 이름","status":"UNKNOWN","evidence_refs":[],"teacher_checked":false}]. MET(충족)·UNMET(미충족)은 교사가 실제 근거를 확인한 경우에만 쓰고, UNKNOWN(미확정)·CONFLICTING(근거 충돌)을 미충족으로 바꾸지 않습니다.</p><button id="causeMapButton" class="btn" type="button">승인된 관계·공동조건 확인</button><div id="causeMapStatus" class="candidate-status" role="status" aria-live="polite"></div><pre id="causeMapDetail"></pre></section>
<section class="card"><h2>2. 실제 근거로 검증하고 공개하기</h2>
<p class="candidate-help">각 후보에 대해 실제 결과, 이유와 근거를 기록합니다. 정답 확인이 끝나고 교사가 내용을 확인한 후보만 별도로 공개할 수 있습니다. 공개는 현재 수업 내용을 바꿀 수 있으므로 확인 후 눌러 주세요.</p>
<div class="candidate-actions"><button id="refreshButton" class="btn" type="button">후보 묶음 새로고침</button></div>
<div id="batchStatus" class="candidate-status" role="status" aria-live="polite"></div>
<div id="batches"></div></section>
<section class="card"><h2>3. 검증 결과를 다음 후보 선택에 반영</h2>
<p class="candidate-policy-note">운영 적응은 검증 영수증별 사례 통계를 다시 계산하는 방식이며 신경망 가중치 학습이 아닙니다. 기존 별도 학습 연구 모듈은 운영 화면에서 실행하지 않습니다. 현재 이 기능은 검증된 피드백으로 <strong>이 서비스 안의 후보 탐색 범위와 성과 기록</strong>을 갱신합니다. 검수하고 학습 사용을 허용한 이유는 선택하여 다음 생성의 참고 자료로 사용할 수 있습니다. GPT·Claude 등의 원본 모델 자체를 재훈련하거나 모델 가중치를 직접 바꾸는 기능은 아닙니다.</p>
<p class="candidate-help">실제 확인이 없는 결과와 근거가 충돌하는 결과는 학습 근거로 확정하지 않습니다. 낮은 순위의 후보가 맞았다는 검증 기록도 다음 선택에 반영할 수 있습니다. 이유 검수·학습 사용 승인·자료 사용 권한이 갖춰진 학습용 기록만 반영하며, 평가용 기록은 정책 학습에 섞지 않습니다.</p>
<div id="policyStatus" class="candidate-status" role="status" aria-live="polite"></div>
<label class="candidate-check"><input id="learningConfirmed" type="checkbox">검증된 결과만 다음 후보 선택에 반영하며, 정답 검증 기준은 유지한다는 점을 확인했습니다.</label>
<div class="candidate-actions"><button id="policyRefreshButton" class="btn" type="button">학습 상태 새로고침</button><button id="learnButton" class="btn primary" type="button" disabled>검증 결과 반영</button></div>
<details style="margin-top:14px"><summary>후보 선택 기록 자세히 보기</summary><pre id="policyDetail">불러오는 중...</pre></details>
</section><section class="card"><h2>4. 근거를 정정하고 실제 사용만 철회</h2><p class="candidate-help">잘못된 근거의 정확한 참조·버전을 지정하면 이 근거를 사용한 후보 묶음과 검증 기록을 찾아 공개와 사례 적응에서 제외합니다. 관련 없는 검토된 공개 자료와 다른 문제의 선택 규칙은 유지합니다. 외부 AI의 가중치를 되돌리는 기능은 아닙니다.</p><label for="revokedEvidence">철회할 근거 참조·버전</label><textarea id="revokedEvidence" rows="2"></textarea><label for="correctionEvidence">정정을 확인한 새 근거 참조·버전</label><textarea id="correctionEvidence" rows="2"></textarea><label for="correctionReason">정정 이유</label><textarea id="correctionReason" rows="2"></textarea><label class="candidate-check"><input id="correctionChecked" type="checkbox">교사가 정정 근거를 직접 확인했습니다.</label><button id="revokeEvidenceButton" class="btn danger" type="button">근거 사용 철회·관련 사례 다시 계산</button><div id="revokeEvidenceStatus" class="candidate-status" role="status" aria-live="polite"></div></section></main>
<script>/* APPLICATION_JS */
(() => {
'use strict';
const cfg=/* WORKBENCH_CONFIG */;
const sessionPath=encodeURIComponent(cfg.sessionId);
const endpoint='/classroom/api/sessions/'+sessionPath;
const teacherQuery='teacher_token='+encodeURIComponent(cfg.teacherToken);
let currentPolicy=null,currentPolicyContext='';
const $=id=>document.getElementById(id);
const back=new URL('/classroom/teacher/'+sessionPath,location.origin);
back.searchParams.set('teacher_token',cfg.teacherToken);$('backLink').href=back.pathname+back.search;
function el(tag,text,className){const node=document.createElement(tag);if(text!==undefined&&text!==null)node.textContent=String(text);if(className)node.className=className;return node;}
function status(id,text,error=false){const target=typeof id==='string'?$(id):id;target.textContent=String(text||'');target.className='candidate-status '+(error?'error':'success');}
function refs(value){return String(value||'').split(/\r?\n/).map(x=>x.trim()).filter(Boolean);}
function humanError(error){if(error.status===409)return '기록 또는 수업 내용이 변경되었거나, 현재 수업 형식에서는 이 공개 방식을 사용할 수 없습니다. 새로고침한 뒤 상태를 확인해 주세요.\n'+error.message;if(error.status===503||error.status===423)return '요청이 현재 설정 또는 검증·운영 통제로 보류되었습니다. 후보는 자동 공개되지 않았습니다. 수동 입력이나 교사 직관을 사용할 수 있습니다.\n'+error.message;if(error.status===401||error.status===403)return '교사 로그인 또는 수업 접근 권한을 확인해 주세요.';return error.message||'요청을 처리하지 못했습니다.';}
async function request(path,body){const options={credentials:'same-origin',headers:{'accept':'application/json'}};if(body!==undefined){options.method='POST';options.headers['content-type']='application/json';options.body=JSON.stringify(body);}const separator=path.includes('?')?'&':'?';const response=await fetch(path+separator+teacherQuery,options);let data;try{data=await response.json();}catch(_){data={detail:'응답 내용을 읽을 수 없습니다.'};}if(!response.ok){const detail=data.detail||data.message||'요청 실패';const error=new Error(typeof detail==='string'?detail:detail.message||JSON.stringify(detail));error.status=response.status;throw error;}return data;}
async function task(button,statusTarget,fn){button.disabled=true;try{await fn();}catch(error){status(statusTarget,humanError(error),true);}finally{button.disabled=button===$('learnButton')?(!currentPolicy||currentPolicy.revision===undefined||!$('learningConfirmed').checked):false;}}
function example(){const texts=[
'동사의 시제를 잘못 선택했을 가능성',
'능동태와 수동태를 혼동했을 가능성',
'현재완료의 시간 조건을 잘못 이해했을 가능성',
'주어와 보어를 혼동했을 가능성',
'관계대명사가 가리키는 선행사를 잘못 찾았을 가능성',
'등위접속사의 연결 범위를 잘못 읽었을 가능성',
'부정 표현의 적용 범위를 놓쳤을 가능성',
'가정법의 조건과 결과를 혼동했을 가능성',
'동명사와 분사의 역할을 혼동했을 가능성',
'수식어가 붙는 대상을 잘못 찾았을 가능성',
'분사구문의 생략된 주어를 잘못 복원했을 가능성',
'문장 전체 의미만 보고 동사 형태를 선택했을 가능성',
'병렬 구조의 두 항목을 다른 문장 성분으로 읽었을 가능성',
'The report, along with the notes, is ready.에서 삽입구의 notes를 주어로 오인했을 가능성. 실제 주어의 핵은 단수 report이며 along with the notes는 삽입구라는 근거를 별도로 검증해야 함'
];$('contextRef').value='영문법 수의 일치 · 진단 가설 예시';$('candidateJson').value=JSON.stringify(texts.map((text,i)=>({candidate_id:'grammar-'+(i+1),origin:'MODEL',rank:i+1,model_score:Number((0.95-i*0.05).toFixed(2)),score_basis:'EXAMPLE_SYNTHETIC_SCORE',concept_refs:[],hypothesis_type:'MISCONCEPTION_HYPOTHESIS',text,evidence_refs:[]})),null,2);$('maxRank').value='14';$('scoreMargin').value='1';$('includeIds').value='';status('createStatus','예시는 미검증 가설이며 점수도 가상 예시입니다. 14위 후보를 미리 정답으로 지정하지 않았습니다.');}
function latestReview(batch,id){const raw=batch.reviews||[];const items=Array.isArray(raw)?raw:Object.values(raw);return items.filter(x=>x&&x.candidate_id===id).slice(-1)[0]||null;}
function option(select,value,label){const node=el('option',label);node.value=value;select.append(node);}
function labeled(parent,label,node){const wrap=el('label',label);wrap.append(node);parent.append(wrap);return node;}
function makeCandidate(batch,candidate){const card=el('article',null,'card');const meta=el('div',null,'candidate-meta');if(candidate.origin==='HUMAN_INSIGHT'){meta.append(el('span','교사 직관 · 순위·점수 없음','badge'));}else{meta.append(el('span',(candidate.rank_basis==='GENERATION_ORDER'?'생성 순서 ':candidate.rank_basis==='SELF_REPORTED_RANK'?'AI 자체 순위 ':'모델 순위 ')+candidate.rank,'badge'),el('span',candidate.model_score===null||candidate.model_score===undefined?'측정 점수 없음':'모델 점수 '+candidate.model_score,'badge'));}meta.append(el('span',candidate.candidate_id,'badge'));card.append(meta,el('div',candidate.text,'candidate-text'));
if(candidate.concept_refs?.length)card.append(el('p','연결 개념: '+candidate.concept_refs.join(' / '),'candidate-small'));if(candidate.hypothesis_type)card.append(el('p','후보 유형: '+candidate.hypothesis_type+' · 학생 오개념의 확정 진단이 아님','candidate-small'));const withdrawn=(batch.invalidated_candidate_ids||[]).includes(candidate.candidate_id);if(withdrawn)card.append(el('p','근거 정정으로 철회된 후보 · 공개와 학습 사용 중단','candidate-status error'));const review=latestReview(batch,candidate.candidate_id);const reviewOutcome=review?.status||review?.outcome;const outcomes={ACCEPTED:'채택',REJECTED:'기각',UNRESOLVED:'미확정'};const truths={VERIFIED:'실제 근거 확인',UNKNOWN:'실제 정답 미확정',CONFLICTING:'근거 충돌'};
if(review){card.append(el('p','최근 판정: '+(outcomes[reviewOutcome]||reviewOutcome)+' · '+(truths[review.ground_truth_status]||review.ground_truth_status),'candidate-small'));card.append(el('p','학습 사용: '+(review.learning_use_approved?'승인':'미승인')+' · 이유 검수: '+(review.reason_status==='TEACHER_CHECKED'?'교사 확인':'미확정')+' · 자료 사용 권한: '+(review.rights_status==='APPROVED'?'확인':'미확정')+' · 구분: '+(review.split==='EVAL'?'평가용':'학습용'),'candidate-small'));if(review.reason)card.append(el('p','판정 이유: '+review.reason,'candidate-text'));if(review.evidence_refs?.length)card.append(el('p','판정 근거: '+review.evidence_refs.join(' / '),'candidate-small'));}else card.append(el('p','아직 검증하지 않은 후보','candidate-small'));
if(candidate.evidence_refs?.length)card.append(el('p','후보의 근거 참조: '+candidate.evidence_refs.join(' / '),'candidate-small'));
const form=el('div',null,'candidate-review');const outcome=el('select');option(outcome,'UNRESOLVED','미확정 · 판단 보류');option(outcome,'ACCEPTED','채택 · 실제 근거로 맞음 확인');option(outcome,'REJECTED','기각 · 실제 근거로 틀림 확인');labeled(form,'결과 판정',outcome);
const truth=el('select');option(truth,'UNKNOWN','실제 정답이 아직 확인되지 않음');option(truth,'VERIFIED','실제 근거를 확인했음');option(truth,'CONFLICTING','근거끼리 충돌함');labeled(form,'실제 정답 확인 상태',truth);
const reason=el('textarea');reason.rows=3;reason.placeholder='예: 14위 가설이 맞은 이유와 확인 과정';labeled(form,'판정 이유',reason);
const evidence=el('textarea');evidence.rows=2;evidence.placeholder='한 줄에 하나씩: 검증한 문항, 풀이, 관찰 또는 자료의 참조';labeled(form,'검증한 근거 참조',evidence);
const criteria=batch.manifest?.verification_criteria||{kind:'EVIDENCE'};const dimensionInputs={};const dimensions=criteria.required_dimensions||['ANSWER'];const dimensionNames={ANSWER:'답의 정확성',EXPLANATION:'설명의 성립',ACTUAL_CAUSE:'학생 오류의 실제 원인'};for(const dimension of dimensions){const wrap=el('fieldset');wrap.append(el('legend',dimensionNames[dimension]));const state=el('select');option(state,'UNKNOWN','미확정');option(state,'PASSED','근거로 통과 확인');option(state,'FAILED','근거로 실패 확인');option(state,'CONFLICTING','근거 충돌');labeled(wrap,'이 판단의 상태',state);const refsInput=el('textarea');refsInput.rows=2;labeled(wrap,'이 판단을 확인한 별도 근거 참조',refsInput);const label=el('label',null,'candidate-check');const checked=el('input');checked.type='checkbox';label.append(checked,el('span','교사가 이 판단과 근거를 별도로 확인했습니다.'));wrap.append(label);let method=null,observation=null,contrast=null;if(dimension==='ACTUAL_CAUSE'){method=el('select');option(method,'CONTROLLED_COMPARISON','조건을 달리한 대조 관찰');option(method,'INDEPENDENT_RETEST','도움 없는 별도 문항 재검사');option(method,'DIRECT_OBSERVATION','원인 기제를 직접 관찰');labeled(wrap,'원인 확인 방법',method);observation=el('input');labeled(wrap,'학생의 실제 관찰 기록 참조',observation);contrast=el('input');labeled(wrap,'대조·별도 재검사 기록 참조',contrast);}dimensionInputs[dimension]={state,refsInput,checked,method,observation,contrast};form.append(wrap);}let measured=null;if(criteria.kind==='NUMERIC'){measured=el('input');measured.type='number';measured.step='any';labeled(form,'실제 측정값 (기준 '+criteria.expected_numeric_value+' · 허용 절대오차 '+criteria.numeric_absolute_tolerance+')',measured);}const verifiedLabel=el('label',null,'candidate-check');const verified=el('input');verified.type='checkbox';verifiedLabel.append(verified,el('span','교사가 이 후보의 내용과 실제 근거를 직접 확인했습니다. 확정 판정에는 필수입니다.'));form.append(verifiedLabel);const check=(label)=>{const wrap=el('label',null,'candidate-check');const input=el('input');input.type='checkbox';wrap.append(input,el('span',label));form.append(wrap);return input;};const reasonChecked=check('판정 이유가 실제 근거와 맞는지 교사가 별도로 검수했습니다.');const learningApproved=check('이 후보·결과·이유를 학습 근거로 사용하도록 승인합니다.');const rightsApproved=check('위 자료를 학습에 사용할 권한을 확인했습니다.');const split=el('select');option(split,'TRAIN','학습용 · 후보 선택 개선에 사용');option(split,'EVAL','평가용 · 학습에 섞지 않음');labeled(form,'기록 사용 구분',split);const splitGroup=el('input');splitGroup.maxLength=500;splitGroup.placeholder='같은 원자료 묶음의 구분명 (선택)';labeled(form,'원자료 그룹',splitGroup);form.append(el('p','이유 검수·학습 사용 승인·자료 권한을 모두 확인하지 않아도 정답 검증과 공개는 가능합니다. 다만 학습 보상은 만들지 않습니다. 같은 원자료 그룹을 학습용과 평가용에 중복 배정하지 않습니다.','candidate-help'));
const actions=el('div',null,'candidate-actions');const save=el('button','검증 결과 기록','btn primary');save.type='button';if(withdrawn||(reviewOutcome&&reviewOutcome!=='UNRESOLVED')){save.disabled=true;form.append(el('p','확정된 검증 기록은 덮어쓰지 않습니다. 다른 근거로 재검증하려면 새 후보 묶음을 만드세요.','candidate-help'));}const publish=el('button','검증한 후보를 수업에 공개','btn');publish.type='button';publish.disabled=withdrawn||!(reviewOutcome==='ACCEPTED'&&review?.ground_truth_status==='VERIFIED'&&review?.teacher_verified===true);const invalidate=el('button','근거 정정 · 후보 철회','btn danger');invalidate.type='button';invalidate.disabled=withdrawn;actions.append(save,publish,invalidate);const result=el('div',null,'candidate-status');result.setAttribute('role','status');result.setAttribute('aria-live','polite');form.append(actions,result);card.append(form);
save.addEventListener('click',()=>task(save,result,async()=>{if(outcome.value!=='UNRESOLVED'&&!verified.checked)throw new Error('확정 판정은 내용과 근거를 확인한 뒤 교사 확인에 체크해 주세요.');if(!reason.value.trim())throw new Error('판정 이유를 적어 주세요.');if(outcome.value!=='UNRESOLVED'&&truth.value!=='VERIFIED')throw new Error('실제 정답이 미확정이거나 근거가 충돌하면 결과도 미확정으로 기록해 주세요.');if(outcome.value!=='UNRESOLVED'&&!refs(evidence.value).length)throw new Error('확정 판정에는 검증한 근거 참조가 필요합니다.');let numeric=null;if(measured&&measured.value!==''){numeric=Number(measured.value);if(!Number.isFinite(numeric))throw new Error('실제 측정값은 유한한 숫자여야 합니다.');}if(measured&&outcome.value!=='UNRESOLVED'&&numeric===null)throw new Error('사전에 정한 수치 기준으로 실제 측정값을 입력해 주세요.');const checks={};for(const [dimension,input] of Object.entries(dimensionInputs)){checks[dimension==='ANSWER'?'answer_check':dimension==='EXPLANATION'?'explanation_check':'actual_cause_check']={status:input.state.value,evidence_refs:refs(input.refsInput.value),teacher_checked:input.checked.checked,method:input.method?.value||'SOURCE_CHECK',observation_ref:input.observation?.value.trim()||'',contrast_ref:input.contrast?.value.trim()||''};}await request(endpoint+'/candidate-batches/'+encodeURIComponent(batch.batch_id)+'/review',{...checks,reason_status:reasonChecked.checked?'TEACHER_CHECKED':'UNVERIFIED',learning_use_approved:learningApproved.checked,rights_status:rightsApproved.checked?'APPROVED':'UNKNOWN',split:split.value,split_group:splitGroup.value.trim(),measured_numeric_value:numeric,expected_digest:batch.digest,candidate_id:candidate.candidate_id,outcome:outcome.value,reason:reason.value.trim(),evidence_refs:refs(evidence.value),teacher_verified:verified.checked,ground_truth_status:truth.value});status(result,'검증 결과를 기록했습니다. 학습 사용 승인은 정답 검증과 별도로 적용됩니다.');await loadBatches();await loadPolicy();}));
publish.addEventListener('click',()=>task(publish,result,async()=>{if(!confirm('실제 근거로 확인한 이 후보를 현재 수업에 공개할까요? 공개하면 수업 내용이 변경될 수 있습니다.'))return;const lesson=await request('/tutor/api/sessions/'+sessionPath);if(!lesson.lesson_digest)throw new Error('현재 수업의 검토 상태를 읽지 못했습니다. 교사 콘솔에서 수업을 확인해 주세요.');await request(endpoint+'/candidate-batches/'+encodeURIComponent(batch.batch_id)+'/publish',{expected_digest:batch.digest,candidate_id:candidate.candidate_id,expected_lesson_digest:lesson.lesson_digest,teacher_verified_content:true});status(result,'검증한 후보를 수업에 공개했습니다.');await loadBatches();}));invalidate.addEventListener('click',()=>task(invalidate,result,async()=>{const why=prompt('기존 근거가 잘못되었음을 확인한 정정 이유를 적어 주세요.');if(why===null)return;if(!why.trim())throw new Error('확인한 근거 정정 이유가 필요합니다.');const proof=prompt('정정을 확인한 근거 참조를 한 줄에 하나씩 적어 주세요.');if(proof===null)return;if(!refs(proof).length)throw new Error('확인한 정정 근거 참조가 필요합니다.');if(!confirm('교사가 정정 근거를 확인했습니까? 이 후보의 학생 공개와 학습 사용을 철회하고 후보 선택 정책을 다시 구성합니다.'))return;const resultData=await request(endpoint+'/candidate-batches/'+encodeURIComponent(batch.batch_id)+'/invalidate',{expected_digest:batch.digest,candidate_id:candidate.candidate_id,reason:why.trim(),evidence_refs:refs(proof),teacher_verified:true});status('batchStatus','후보를 철회했습니다. 학생 공개 자료 '+(resultData.withdrawn_segment_count??0)+'개를 거두고 후보 선택 기록을 다시 구성했습니다.');await loadBatches();await loadPolicy();}));return card;}
function renderBatches(batches){const root=$('batches');root.replaceChildren();if(!batches.length){root.append(el('p','아직 기록한 후보 묶음이 없습니다.','candidate-help'));return;}for(const batch of batches){const wrap=el('section',null,'candidate-batch');wrap.append(el('h3',batch.context_ref||batch.manifest?.context_ref||'후보 묶음'));wrap.append(el('p','묶음 식별자: '+batch.batch_id,'candidate-small'));const search=batch.manifest?.search;if(search)wrap.append(el('p','요청한 최대 순위 '+search.requested_max_rank+'위 · 학습 기록을 반영한 실제 최대 순위 '+search.effective_max_rank+'위 · 요청 점수 차이 '+search.score_margin+' · 학습 반영 점수 차이 '+(search.effective_score_margin??search.score_margin),'candidate-help'));const selected=new Set(batch.manifest?.selected_candidate_ids||[]);const candidates=batch.candidates||[];const shown=candidates.filter(x=>selected.has(x.candidate_id));wrap.append(el('p','기록된 후보 '+candidates.length+'개 · 이번 탐색 대상 '+shown.length+'개','candidate-help'));for(const candidate of shown)wrap.append(makeCandidate(batch,candidate));const hidden=candidates.filter(x=>!selected.has(x.candidate_id));if(hidden.length){const details=el('details');details.append(el('summary','이번 탐색 범위 밖의 후보 '+hidden.length+'개'));for(const candidate of hidden)details.append(el('p',(candidate.origin==='HUMAN_INSIGHT'?'교사 직관':candidate.rank+'위')+' · '+candidate.candidate_id+' · '+candidate.text,'candidate-text'));wrap.append(details);}if(!shown.length)wrap.append(el('p','선택된 후보가 없습니다. 탐색 범위와 추가 식별자를 확인해 주세요.','candidate-help'));root.append(wrap);}}
async function loadBatches(){const result=await request(endpoint+'/candidate-batches');renderBatches(Array.isArray(result.batches)?result.batches:[]);status('batchStatus','후보 묶음을 불러왔습니다.');}
async function loadPolicy(){const context=$('contextRef').value.trim();if(!context){currentPolicy=null;currentPolicyContext='';$('learnButton').disabled=true;status('policyStatus','문제·상황 참조를 먼저 입력해 주세요.');return;}const result=await request(endpoint+'/candidate-policy?context_ref='+encodeURIComponent(context));if($('contextRef').value.trim()!==context)return;currentPolicy=result.policy||result;currentPolicyContext=context;$('policyDetail').textContent=JSON.stringify(result,null,2);status('policyStatus','후보 선택 기록을 불러왔습니다. 현재 개정: '+(currentPolicy.revision??'확인 필요')+' · 학습 기록에 따른 탐색 범위: '+(currentPolicy.effective_max_rank??'확인 필요')+'위까지 · 학습 기록의 점수 차이 '+(currentPolicy.effective_score_margin??0)+' · 검증 피드백 '+(currentPolicy.learned_reward_count??0)+'건');$('learnButton').disabled=!$('learningConfirmed').checked||currentPolicy.revision===undefined;}
$('exampleButton').addEventListener('click',()=>{example();currentPolicy=null;$('learnButton').disabled=true;loadPolicy().catch(e=>status('policyStatus',humanError(e),true));});
$('contextRef').addEventListener('change',()=>{currentPolicy=null;$('learnButton').disabled=true;loadPolicy().catch(e=>status('policyStatus',humanError(e),true));});
$('verificationKind').addEventListener('change',()=>{$('numericCriteria').hidden=$('verificationKind').value!=='NUMERIC';});
$('insightButton').addEventListener('click',()=>{try{const text=$('insightText').value.trim();if(!text)throw new Error('직관·통찰의 내용을 먼저 적어 주세요.');const list=JSON.parse($('candidateJson').value||'[]');if(!Array.isArray(list))throw new Error('후보 목록은 JSON 배열이어야 합니다.');const id='insight-'+Date.now().toString(36);list.push({candidate_id:id,origin:'HUMAN_INSIGHT',rank:null,model_score:null,text,evidence_refs:[]});$('candidateJson').value=JSON.stringify(list,null,2);$('insightText').value='';status('createStatus','직관 후보를 목록에 추가했습니다. 모델 점수를 붙이지 않으며 자동 채택하지 않습니다.');}catch(e){status('createStatus',humanError(e),true);}});
function readSearch(){const maxRank=Number($('maxRank').value),margin=Number($('scoreMargin').value);if(!Number.isInteger(maxRank)||maxRank<1||maxRank>100)throw new Error('최대 순위는 1~100 사이의 정수여야 합니다.');if(!Number.isFinite(margin)||margin<0||margin>1)throw new Error('허용 차이는 0~1 사이여야 합니다.');return {max_rank:maxRank,score_margin:margin,include_candidate_ids:refs($('includeIds').value)};}
function readVerification(){const verification={kind:$('verificationKind').value,criteria_ref:$('criteriaRef').value.trim(),required_dimensions:$('verificationScope').value.split(',')};if(!verification.criteria_ref)throw new Error('탐색 전에 적용할 검증 기준 참조를 적어 주세요.');if(verification.kind==='NUMERIC'){if($('expectedValue').value===''||$('absoluteTolerance').value==='')throw new Error('수치 검증의 기준값과 허용 절대오차를 먼저 적어 주세요.');verification.expected_numeric_value=Number($('expectedValue').value);verification.numeric_absolute_tolerance=Number($('absoluteTolerance').value);if(!Number.isFinite(verification.expected_numeric_value)||!Number.isFinite(verification.numeric_absolute_tolerance)||verification.numeric_absolute_tolerance<0)throw new Error('기준값은 유한한 수이며 허용 절대오차는 0 이상이어야 합니다.');}return verification;}
function readCauseMap(){if(!$('useCauseMap').checked)return null;let observations;try{observations=JSON.parse($('conditionObservations').value||'[]');}catch(_){throw new Error('조건별 관찰 기록의 JSON 목록 형식을 확인해 주세요.');}if(!Array.isArray(observations))throw new Error('조건별 관찰은 목록이어야 합니다.');return {focus_node_ids:refs($('focusNodes').value),observations};}
$('causeMapButton').addEventListener('click',()=>task($('causeMapButton'),'causeMapStatus',async()=>{const map=await request(endpoint+'/candidate-cause-map',readCauseMap()||{focus_node_ids:refs($('focusNodes').value),observations:[]});$('causeMapDetail').textContent=JSON.stringify(map,null,2);status('causeMapStatus','검토한 선수·공동조건·유사 관계를 함께 읽었습니다. 미확정 관찰을 미충족으로 전파하지 않았고 학습경로를 바꾸지 않았습니다.');}));
$('revokeEvidenceButton').addEventListener('click',()=>task($('revokeEvidenceButton'),'revokeEvidenceStatus',async()=>{if(!$('correctionChecked').checked)throw new Error('교사가 정정 근거를 확인한 뒤 체크해 주세요.');const revoked=refs($('revokedEvidence').value),corrected=refs($('correctionEvidence').value),reason=$('correctionReason').value.trim();if(!revoked.length||!corrected.length||!reason)throw new Error('철회할 근거, 새 정정 근거와 이유를 모두 적어 주세요.');const result=await request(endpoint+'/candidate-evidence/revoke',{revoked_evidence_refs:revoked,correction_evidence_refs:corrected,reason,teacher_verified:true});status('revokeEvidenceStatus','관련 후보 '+result.revocation.affected_candidate_uses.length+'개와 학생 공개 '+result.withdrawn_segment_count+'개를 철회하고 관련 사례만 다시 계산했습니다.');await loadBatches();await loadPolicy();}));
$('createButton').addEventListener('click',()=>task($('createButton'),'createStatus',async()=>{const context=$('contextRef').value.trim();if(!context)throw new Error('문제·상황 참조를 적어 주세요.');let candidates;try{candidates=JSON.parse($('candidateJson').value);}catch(_){throw new Error('후보 목록의 JSON 형식을 확인해 주세요.');}if(!Array.isArray(candidates)||!candidates.length)throw new Error('하나 이상의 후보 목록을 입력해 주세요.');const search=readSearch(),verification=readVerification();const result=await request(endpoint+'/candidate-batches',{context_ref:context,candidates,verification,search,cause_map_request:readCauseMap()});status('createStatus','후보 묶음을 기록했습니다. 탐색 대상 '+(result.manifest?.selected_candidate_ids?.length??0)+'개');await loadBatches();await loadPolicy();}));
$('generateButton').addEventListener('click',()=>task($('generateButton'),'generateStatus',async()=>{if(!$('generationAuthorized').checked)throw new Error('선택한 AI 호출과 입력 자료 전달을 먼저 승인해 주세요.');const context=$('contextRef').value.trim(),taskText=$('taskText').value.trim(),source=$('sourceText').value.trim(),count=Number($('generationCount').value);if(!context||!taskText||!source)throw new Error('문제·상황 참조, 검토 요청과 원자료를 모두 적어 주세요.');if(!Number.isInteger(count)||count<1||count>50)throw new Error('후보 수는 1~50 사이의 정수여야 합니다.');status('generateStatus','선택한 AI에서 검토용 후보를 생성하고 있습니다. 학생에게 자동 공개하지 않습니다.');const result=await request(endpoint+'/candidate-batches/generate',{provider:$('provider').value,explicit_authorization:true,context_ref:context,source_text:source,task_text:taskText,student_response:$('studentResponse').value,count,subject:$('generationSubject').value.trim(),grade:$('generationGrade').value.trim(),search:readSearch(),verification:readVerification(),use_verified_reason_context:$('reasonContextAllowed').checked,cause_map_request:readCauseMap()});status('generateStatus','AI 후보 '+(result.candidates?.length??0)+'개를 기록했습니다. 실제 근거 확인 전에는 공개되지 않습니다.');await loadBatches();await loadPolicy();}));
$('refreshButton').addEventListener('click',()=>task($('refreshButton'),'batchStatus',loadBatches));
$('policyRefreshButton').addEventListener('click',()=>task($('policyRefreshButton'),'policyStatus',loadPolicy));
$('learningConfirmed').addEventListener('change',()=>{$('learnButton').disabled=!$('learningConfirmed').checked||!currentPolicy||currentPolicy.revision===undefined;});
$('learnButton').addEventListener('click',()=>task($('learnButton'),'policyStatus',async()=>{if(!$('learningConfirmed').checked)throw new Error('검증 결과 반영 범위를 먼저 확인해 주세요.');if(!currentPolicy||currentPolicy.revision===undefined||$('contextRef').value.trim()!==currentPolicyContext)throw new Error('현재 문제·상황의 최신 후보 선택 상태를 먼저 불러와 주세요.');const result=await request(endpoint+'/candidate-policy/learn',{expected_revision:currentPolicy.revision,context_ref:currentPolicyContext});await loadPolicy();status('policyStatus','검증 결과를 서비스 안의 후보 선택에 반영했습니다. 원본 모델 재훈련은 수행하지 않았습니다.');$('policyDetail').textContent=JSON.stringify(result,null,2);}));
example();Promise.allSettled([loadBatches(),loadPolicy()]).then(results=>{if(results[0].status==='rejected')status('batchStatus',humanError(results[0].reason),true);if(results[1].status==='rejected')status('policyStatus',humanError(results[1].reason),true);});
})();
</script></body></html>"""
