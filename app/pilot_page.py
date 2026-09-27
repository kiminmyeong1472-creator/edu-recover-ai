"""Teacher-facing EduRecover AI pilot platform."""

PILOT_HTML = r"""
<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>EduRecover AI · 교사 출제 플랫폼</title>
  <style>
    :root{
      --bg:#f4f6f8; --panel:#fff; --ink:#111827; --muted:#667085;
      --line:#e5e7eb; --nav:#111827; --accent:#2563eb; --soft:#eef4ff;
      --good:#067647; --warn:#b54708; --bad:#b42318;
      font-family:Inter,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
    }
    *{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--ink)}
    button,input,select,textarea{font:inherit}
    .app{min-height:100vh;display:grid;grid-template-columns:244px 1fr}
    aside{background:var(--nav);color:#fff;padding:22px 14px;position:sticky;top:0;height:100vh}
    .brand{padding:0 10px 18px;border-bottom:1px solid #344054;margin-bottom:14px}
    .brand strong{display:block;font-size:19px}.brand span{font-size:12px;color:#98a2b3}
    nav button{display:block;width:100%;border:0;background:transparent;color:#d0d5dd;text-align:left;padding:11px 12px;border-radius:9px;margin:3px 0;cursor:pointer}
    nav button:hover,nav button.active{background:#344054;color:#fff}
    .side-note{position:absolute;bottom:18px;left:14px;right:14px;font-size:11px;color:#98a2b3;line-height:1.45}
    main{min-width:0}.topbar{height:70px;background:#fff;border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between;padding:0 26px;position:sticky;top:0;z-index:3}
    .topbar h1{font-size:19px;margin:0}.topbar .meta{font-size:12px;color:var(--muted)}
    .content{padding:24px;max-width:1360px;margin:0 auto}.view{display:none}.view.active{display:block}
    .hero{display:flex;gap:14px;justify-content:space-between;align-items:flex-start;margin-bottom:18px}.hero h2{margin:0 0 6px;font-size:26px}.hero p{margin:0;color:var(--muted)}
    .grid{display:grid;gap:14px}.grid.c4{grid-template-columns:repeat(4,minmax(0,1fr))}.grid.c3{grid-template-columns:repeat(3,minmax(0,1fr))}.grid.c2{grid-template-columns:repeat(2,minmax(0,1fr))}
    .card{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:18px;box-shadow:0 1px 2px rgba(16,24,40,.04)}
    .card h3{margin:0 0 12px;font-size:16px}.num{font-size:29px;font-weight:800;margin:3px 0}.muted{color:var(--muted);font-size:13px;line-height:1.55}
    .badge{display:inline-flex;align-items:center;border-radius:999px;padding:4px 9px;background:#f2f4f7;font-size:11px;font-weight:700;margin:2px 3px 2px 0}.badge.good{background:#ecfdf3;color:var(--good)}.badge.warn{background:#fffaeb;color:var(--warn)}.badge.blue{background:var(--soft);color:#1849a9}
    .status-dot{width:8px;height:8px;border-radius:50%;display:inline-block;margin-right:6px;background:#f79009}.status-dot.good{background:#12b76a}
    .row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}.between{display:flex;justify-content:space-between;gap:10px;align-items:center}
    label{font-size:12px;font-weight:700;display:block;margin:10px 0 6px}input,select,textarea{width:100%;border:1px solid #d0d5dd;border-radius:9px;padding:10px;background:#fff;color:var(--ink)}
    textarea{min-height:150px;resize:vertical}.form-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
    .btn{border:0;border-radius:9px;padding:10px 13px;font-weight:750;cursor:pointer}.btn.primary{background:var(--accent);color:#fff}.btn.dark{background:#111827;color:#fff}.btn.ghost{background:#f2f4f7;color:#344054}.btn.danger{background:#fef3f2;color:var(--bad)}
    .table{width:100%;border-collapse:collapse;font-size:13px}.table th,.table td{padding:11px 9px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}.table th{color:#667085;font-size:11px}
    .step{display:flex;align-items:flex-start;gap:11px;padding:13px 0;border-bottom:1px solid var(--line)}.step:last-child{border-bottom:0}.stepno{width:28px;height:28px;border-radius:50%;display:grid;place-items:center;background:var(--soft);color:#1849a9;font-weight:800;font-size:12px;flex:none}
    .split{display:grid;grid-template-columns:320px 1fr;gap:14px}.item-list{max-height:620px;overflow:auto}.item{padding:12px;border:1px solid var(--line);border-radius:10px;margin:8px 0;cursor:pointer}.item.active{border-color:#84adff;background:#f5f8ff}
    .preview{background:#fff;border:1px solid #d0d5dd;padding:28px;min-height:600px;line-height:1.7}.preview h2{text-align:center;margin-top:0}
    pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#0f172a;color:#e2e8f0;border-radius:10px;padding:13px;min-height:120px;font-size:12px}
    .callout{border-left:4px solid #84adff;background:#f5f8ff;padding:12px 14px;border-radius:8px;font-size:13px;line-height:1.5}
    .checkgrid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}.check{border:1px solid var(--line);border-radius:9px;padding:9px;font-size:13px}
    .check input{width:auto;margin-right:6px}.range-row{display:grid;grid-template-columns:130px 1fr 50px;gap:10px;align-items:center;margin:10px 0}.range-row input{padding:0}
    .modal-backdrop{position:fixed;inset:0;background:rgba(17,24,39,.58);display:grid;place-items:center;padding:18px;z-index:20}
    .modal-backdrop[hidden]{display:none}.modal-panel{width:min(520px,100%);background:#fff;border-radius:16px;padding:20px;border:1px solid var(--line);box-shadow:0 18px 50px rgba(0,0,0,.25)}
    .modal-panel h3{margin:0 0 8px;font-size:20px}.modal-panel p{margin:0 0 12px;line-height:1.55}.modal-actions{display:flex;gap:8px;justify-content:flex-end;flex-wrap:wrap;margin-top:14px}
    .modal-backdrop .btn{min-height:44px}
    .mobile-nav{display:none}
    @media(max-width:900px){
      .app{grid-template-columns:1fr}aside{display:none}.mobile-nav{display:flex;overflow:auto;gap:6px;padding:10px;background:#111827;position:sticky;top:70px;z-index:2}.mobile-nav button{white-space:nowrap;border:0;border-radius:8px;padding:8px 10px;background:#344054;color:#fff}
      .grid.c4,.grid.c3,.grid.c2,.form-grid,.split{grid-template-columns:1fr}.topbar{padding:0 16px}.content{padding:16px}.hero{display:block}
    }
  </style>
</head>
<body>
<div class="app">
  <aside>
    <div class="brand"><strong>EduRecover AI</strong><span>교사 출제 플랫폼</span></div>
    <nav id="desktopNav">
      <button class="active" data-view="dashboard">홈 대시보드</button>
      <button data-view="sources">자료 관리</button>
      <button data-view="create">새 시험 만들기</button>
      <button data-view="settings">출제 설정</button>
      <button data-view="generate">AI 자동출제</button>
      <button data-view="workspace">문항 작업실</button>
      <button data-view="tutor">AI 튜터</button>
      <button data-view="media">시청각 자료</button>
      <button data-view="verify">검증 센터</button>
      <button data-view="preview">시험지 · 해설</button>
      <button data-view="archive">보관함</button>
      <button data-view="system">시스템</button>
    </nav>
    <div class="side-note">학교 교사가 수업자료를 바탕으로 문항을 출제·검토·편집·출력하는 교사용 운영 플랫폼입니다.</div>
  </aside>

  <main>
    <div class="topbar">
      <div><h1 id="pageTitle">홈 대시보드</h1><div class="meta">EduRecover AI Teacher Platform</div></div>
      <div class="row"><span id="healthBadge" class="badge warn"><span class="status-dot"></span>서버 확인 중</span><button class="btn primary" onclick="showView('create')">+ 새 시험</button></div>
    </div>
    <div class="mobile-nav" id="mobileNav">
      <button data-view="dashboard">홈</button><button data-view="sources">자료</button><button data-view="create">새 시험</button><button data-view="settings">설정</button><button data-view="generate">AI출제</button><button data-view="workspace">문항</button><button data-view="tutor">AI튜터</button><button data-view="media">시청각</button><button data-view="verify">검증</button><button data-view="preview">출력</button><button data-view="archive">보관</button><button data-view="system">시스템</button>
    </div>

    <div class="content">
      <section id="dashboard" class="view active">
        <div class="hero"><div><h2>교사 출제 대시보드</h2><p>수업자료에서 시험문항 제작, 검증, 편집, 시험지 출력까지 한 흐름으로 확인합니다.</p></div><span class="badge blue">전체 화면 검토용 운영 플랫폼</span></div>
        <div class="card" style="margin-bottom:14px;border:2px solid #344054">
          <h3>친인간 AI 운영헌장 · 운영책임 공지</h3>
          <p><strong>사람 사용자가 최종 통제권자입니다.</strong> AI 통제는 AI 제공자의 자동실행·라우팅·검증요건·도구권한에만 적용하며, 사람 사용자의 플랫폼 접근권한을 AI가 자동 차단하는 용도로 사용하지 않습니다.</p>
          <p class="muted">API 키와 비밀값은 사용자가 배포환경의 서버 환경변수에 직접 입력하며, 채팅·문서·프롬프트·상태 API에 실제 비밀값을 기록하거나 전달하지 않습니다. AI의 오류·지시불이행에 대한 내부 조치는 제공자 HOLD, 검증강화, 자동실행 제한, 다른 제공자 재라우팅의 형태로만 적용합니다.</p>
          <p class="muted">과거에 내부 상태값을 실제 플랫폼 차단처럼 설명한 것과, 사용자 비밀의 통제권이 AI에 있는 것처럼 설계·설명한 부분은 잘못이었습니다. 이후 운영은 사용자 주권·비밀 비수탁·증거우선·완료상태 분리를 고정 원칙으로 합니다.</p>
          <a href="/human-ai-charter" target="_blank">반성문·운영 다짐 전문 보기</a>
        </div>
        <div class="grid c4">
          <div class="card"><div class="muted">진행 중 시험</div><div class="num" id="draftCount">1</div><span class="badge blue">초안</span></div>
          <div class="card"><div class="muted">현재 문항 수</div><div class="num" id="questionCountCard">0</div><span class="badge">교사 지정</span></div>
          <div class="card"><div class="muted">검증 상태</div><div class="num" style="font-size:20px" id="verifyState">대기</div><span class="badge warn">Fail-Closed</span></div>
          <div class="card"><div class="muted">출력 상태</div><div class="num" style="font-size:20px">미리보기</div><span class="badge good">교사 확인</span></div>
        </div>
        <div class="grid c2" style="margin-top:14px">
          <div class="card">
            <div class="between"><h3>출제 흐름</h3><button class="btn ghost" onclick="showView('create')">시작</button></div>
            <div class="step"><div class="stepno">1</div><div><strong>자료 불러오기</strong><div class="muted">교과서, EBS, 수업자료, 자체 지문 등 출제 근거를 준비합니다.</div></div></div>
            <div class="step"><div class="stepno">2</div><div><strong>출제 조건 설정</strong><div class="muted">문항 수, 유형, 난이도, 범위, 지문 길이를 교사가 지정합니다.</div></div></div>
            <div class="step"><div class="stepno">3</div><div><strong>문항 생성·편집</strong><div class="muted">생성된 문항을 교사가 직접 수정하고 보류할 수 있습니다.</div></div></div>
            <div class="step"><div class="stepno">4</div><div><strong>검증</strong><div class="muted">Trust Chain과 Socratic Trap으로 정답·해설·근거 취약점을 점검합니다.</div></div></div>
            <div class="step"><div class="stepno">5</div><div><strong>시험지·해설 출력</strong><div class="muted">교사 최종 확인 후 시험지와 정답·해설을 구성합니다.</div></div></div>
          </div>
          <div class="card">
            <h3>운영 플랫폼 기능 상태</h3>
            <table class="table">
              <tr><th>영역</th><th>현재 상태</th></tr>
              <tr><td>교사용 전체 화면</td><td><span class="badge good">실행</span></td></tr>
              <tr><td>서버 Health</td><td><span class="badge good">실행</span></td></tr>
              <tr><td>Trust Chain API</td><td><span class="badge good">실행</span></td></tr>
              <tr><td>Socratic Trap API</td><td><span class="badge good">실행</span></td></tr>
              <tr><td>자료 파일 영구저장</td><td><span class="badge warn">연동 전</span></td></tr>
              <tr><td>AI 자동출제 API</td><td id="autoGenStatus"><span class="badge warn">서버 상태 확인 중</span></td></tr>
              <tr><td>산파식 AI 튜터</td><td><span class="badge good">실행</span></td></tr>
              <tr><td>영상·TTS·전자칠판</td><td><span class="badge good">실행</span></td></tr>
              <tr><td>시험지 DOCX 자동출력</td><td><span class="badge good">실행</span></td></tr>
              <tr><td>HWP 직접출력</td><td><span class="badge warn">미지원 · 복사/붙여넣기 안내</span></td></tr>
            </table>
          </div>
        </div>
      </section>

      <section id="sources" class="view">
        <div class="hero"><div><h2>자료 관리</h2><p>출제 근거가 되는 교과서·EBS·수업자료·자체 지문을 정리합니다.</p></div></div>
        <div class="grid c2">
          <div class="card">
            <h3>자료 추가</h3>
            <label>파일 선택</label><input id="sourceFiles" type="file" multiple />
            <label>자료 구분</label><select id="sourceType"><option>교과서</option><option>EBS</option><option>수업자료</option><option>자체 지문</option><option>기타</option></select>
            <button class="btn primary" style="margin-top:12px" onclick="addSourceFiles()">목록에 추가</button>
            <p class="muted">현재 운영 플랫폼에서는 선택한 파일명과 분류를 브라우저 작업공간에 표시합니다. 서버 영구저장은 아직 연결 전입니다.</p>
          </div>
          <div class="card"><h3>출제 자료 목록</h3><table class="table" id="sourceTable"><thead><tr><th>자료명</th><th>구분</th><th>상태</th></tr></thead><tbody><tr><td>공통영어2_L3_교과서본문.hwp</td><td>교과서</td><td><span class="badge blue">예시</span></td></tr></tbody></table></div>
        </div>
      </section>

      <section id="create" class="view">
        <div class="hero"><div><h2>새 시험 만들기</h2><p>학교 시험의 기본 정보를 설정합니다.</p></div></div>
        <div class="card">
          <div class="form-grid">
            <div><label>시험명</label><input id="examName" value="2학기 중간고사 영어" /></div>
            <div><label>과목</label><select id="subject"><option>영어</option><option>국어</option><option>수학</option></select></div>
            <div><label>학교급</label><select id="schoolLevel"><option>고등학교</option><option>중학교</option><option>초등학교</option></select></div>
            <div><label>학년</label><select id="grade"><option>1학년</option><option>2학년</option><option>3학년</option></select></div>
            <div><label>시험 유형</label><select id="examType"><option>중간고사</option><option>기말고사</option><option>단원평가</option><option>수행평가</option><option>보충평가</option></select></div>
            <div><label>문항 수</label><input id="questionCount" type="number" min="1" value="20" /></div>
          </div>
          <label>출제 범위 메모</label><textarea id="scopeMemo">교과서 Lesson 3 본문 및 수업 중 다룬 핵심 어휘·구문</textarea>
          <button class="btn primary" onclick="saveExam()">시험 기본정보 저장</button>
          <button class="btn ghost" onclick="showView('settings')">다음: 출제 설정</button>
        </div>
      </section>

      <section id="settings" class="view">
        <div class="hero"><div><h2>출제 설정</h2><p>문항 유형과 난이도를 교사가 직접 조정합니다.</p></div></div>
        <div class="grid c2">
          <div class="card">
            <h3>문항 유형</h3>
            <div class="checkgrid">
              <label class="check"><input type="checkbox" checked>주제·제목</label>
              <label class="check"><input type="checkbox" checked>요지·주장</label>
              <label class="check"><input type="checkbox" checked>빈칸 추론</label>
              <label class="check"><input type="checkbox" checked>순서 배열</label>
              <label class="check"><input type="checkbox" checked>문장 삽입</label>
              <label class="check"><input type="checkbox" checked>어휘·어법</label>
              <label class="check"><input type="checkbox">내용 일치</label>
              <label class="check"><input type="checkbox">문단 요약</label>
              <label class="check"><input type="checkbox">서술형</label>
            </div>
          </div>
          <div class="card">
            <h3>난이도 배분</h3>
            <div class="range-row"><span>쉬움</span><input id="easyRange" type="range" min="0" max="100" value="25" oninput="syncRange()"><b id="easyVal">25%</b></div>
            <div class="range-row"><span>보통</span><input id="midRange" type="range" min="0" max="100" value="50" oninput="syncRange()"><b id="midVal">50%</b></div>
            <div class="range-row"><span>어려움</span><input id="hardRange" type="range" min="0" max="100" value="25" oninput="syncRange()"><b id="hardVal">25%</b></div>
            <div class="callout">설정 저장 후 AI 자동출제 화면에서 원문과 조건을 보내 실제 생성 API를 호출할 수 있습니다.</div>
          </div>
        </div>
        <div class="card" style="margin-top:14px">
          <h3>출제 세부 조건</h3>
          <div class="form-grid">
            <div><label>지문 길이</label><select><option>원문 문맥 충분히 유지</option><option>짧게</option><option>중간</option><option>길게</option></select></div>
            <div><label>오답 설계</label><select><option>근거 기반 매력적 오답</option><option>일반형</option></select></div>
            <div><label>정답 위치 분포</label><select><option>자동 균형</option><option>교사 지정</option></select></div>
            <div><label>해설 수준</label><select><option>교사용 상세해설</option><option>학생용 간단해설</option></select></div>
          </div>
          <button class="btn primary" onclick="saveSettings()">출제 설정 저장</button>
          <button class="btn ghost" onclick="showView('generate')">다음: AI 자동출제</button>
        </div>
      </section>

      <section id="generate" class="view">
        <div class="hero"><div><h2>AI 자동출제</h2><p>교사가 지정한 원문·범위·유형·난이도로 문항 초안을 자동 생성합니다.</p></div><span id="providerBadge" class="badge warn">AI 상태 확인 중</span></div>
        <div class="grid c2">
          <div class="card">
            <h3>출제 원문</h3>
            <label>AI 출제 에이전트</label>
            <select id="genProvider"><option value="openai" selected>ChatGPT / OpenAI · GPT-5.6 Sol</option><option value="gemini">Gemini</option><option value="claude">Claude</option></select>
            <label>원문·교과서 본문·수업자료</label>
            <textarea id="generationSource" style="min-height:320px" placeholder="출제할 원문을 여기에 붙여 넣으십시오."></textarea>
            <label>추가 출제 지시</label>
            <textarea id="generationInstructions" placeholder="예: 학교 내신형으로, 지문 문맥을 충분히 유지하고 매력적인 오답을 구성"></textarea>
          </div>
          <div class="card">
            <h3>자동출제 실행</h3>
            <table class="table">
              <tr><th>시험명</th><td id="genExamName">-</td></tr>
              <tr><th>문항 수</th><td id="genQuestionCount">-</td></tr>
              <tr><th>학교급·학년</th><td id="genSchool">-</td></tr>
              <tr><th>난이도</th><td id="genDifficulty">-</td></tr>
            </table>
            <div class="callout" style="margin-top:14px"><strong>출제·검증 기준:</strong> 에듀리커버 서버의 출제자료(정규화 어휘 자산, 학년별 구문난도, 구체 구문집)와 출제지침(출제마법사, 영어 문항제작 규칙, 오답선지 원리)을 적용합니다. AI 출제·검증 에이전트에는 해당 작업에 필요한 범위만 공유하며 플랫폼 채팅기록은 전달하지 않습니다.</div>
            <div class="card" style="margin-top:12px;padding:12px">
              <strong>적용 출제자료 및 출제지침</strong>
              <div id="authoringAssetsList" class="muted" style="margin-top:8px">서버 자료 확인 중...</div>
            </div>
            <button class="btn primary" style="margin-top:14px;width:100%" onclick="generateQuestions()">AI 문항 자동생성</button>
            <button class="btn ghost" style="margin-top:8px;width:100%" onclick="showView('workspace')">문항 작업실 열기</button>
            <pre id="generationOutput" style="margin-top:14px">아직 자동출제를 실행하지 않았습니다.</pre>
          </div>
        </div>
      </section>

      <section id="workspace" class="view">
        <div class="hero"><div><h2>문항 작업실</h2><p>문항을 선택해 발문·선택지·정답·해설을 교사가 직접 편집합니다.</p></div><button class="btn ghost" onclick="addSampleItem()">+ 문항 추가</button></div>
        <div class="split">
          <div class="card item-list" id="itemList"></div>
          <div class="card">
            <h3 id="editorTitle">문항 편집</h3>
            <label>발문</label><input id="editQuestion" />
            <label>지문</label><textarea id="editPassage"></textarea>
            <label>정답</label><input id="editAnswer" />
            <label>해설</label><textarea id="editExplanation"></textarea>
            <div class="row"><button class="btn primary" onclick="saveCurrentItem()">문항 저장</button><button class="btn ghost" onclick="showView('tutor')">AI 튜터로</button><button class="btn ghost" onclick="showView('verify')">검증 센터로</button></div>
          </div>
        </div>
      </section>

      <section id="tutor" class="view">
        <div class="hero"><div><h2>산파식 AI 튜터</h2><p>현재 문항을 정답 선제공 없이 오답원인 확인·질문·분기·재시험 수업으로 전환합니다.</p></div><span class="badge good">설치 완료</span></div>
        <div class="grid c2">
          <div class="card"><h3>현재 문항으로 수업 만들기</h3><p class="muted">문항 작업실에서 선택한 문항과 해설을 전자칠판 수업으로 보냅니다. 학생은 가명이나 번호로 참가합니다.</p><button class="btn primary" onclick="startTutorFromCurrentItem()">현재 문항 AI 튜터 수업 만들기</button><div id="tutorLaunchResult" class="muted" style="margin-top:12px"></div></div>
          <div class="card"><h3>AI 튜터 수업실</h3><p class="muted">문항·오답원인·영상·TTS·산파식 질문·재시험을 직접 설정하고 전자칠판과 교사 콘솔을 엽니다.</p><a class="btn dark" href="/tutor" style="display:inline-block;text-decoration:none">AI 튜터 수업실 열기</a></div>
          <div class="card"><h3>강화학습 연결</h3><p class="muted">실제 노출·피드백 전 응답·독립 후속문항·교사검증을 목적별 학습자격으로 판정합니다. 학생 응답 직후 모델 가중치는 자동 변경하지 않습니다.</p><div id="tutorTrainingState"><span class="badge warn">상태 확인 중</span></div></div>
        </div>
      </section>

      <section id="media" class="view">
        <div class="hero"><div><h2>시청각 자료</h2><p>영상 또는 한국어 TTS 설명을 산파식 확인 질문과 함께 수업으로 구성합니다.</p></div><span class="badge good">영상·음성 실행</span></div>
        <div class="card">
          <div class="form-grid"><div><label>수업 제목</label><input id="mediaTitle" value="시청각 개념 수업"></div><div><label>영상 주소</label><input id="mediaUrl" placeholder="MP4·WebM 또는 YouTube 주소"></div><div><label>영상 멈춤 시점(초)</label><input id="mediaPause" type="number" min="0" step="0.5" value="8"></div><div><label>확인 질문</label><input id="mediaQuestion" value="방금 설명에서 가장 중요한 조건은 무엇인가요?"></div></div>
          <label>설명·TTS 원고</label><textarea id="mediaScript">핵심 조건을 먼저 자기 말로 설명해 보세요. 영상 주소가 없으면 이 문장을 한국어 음성으로 읽어 줍니다.</textarea>
          <button class="btn primary" onclick="startAudiovisualLesson()">시청각 수업 만들기</button><a class="btn ghost" href="/tutor" style="display:inline-block;text-decoration:none;margin-left:6px">상세 설정</a><div id="mediaLaunchResult" class="muted" style="margin-top:12px"></div>
        </div>
      </section>

      <section id="verify" class="view">
        <div class="hero"><div><h2>검증 센터</h2><p>교사가 만든 문항 데이터에 Trust Chain과 Socratic Trap을 적용합니다.</p></div></div>
        <div class="grid c3">
          <div class="card"><h3>Trust Chain</h3><p class="muted">검증 결과가 모두 통과해야 승인합니다. 다수결로 실패를 덮지 않습니다.</p><button class="btn dark" onclick="runWorkspaceApi('/verify-problem')">현재 문항 검증</button></div>
          <div class="card"><h3>Socratic Trap</h3><p class="muted">근거 약화, 과도한 일반화, 추측·인과 비약 위험을 역질문으로 재검토합니다.</p><button class="btn dark" onclick="runWorkspaceApi('/apply-trap')">함정 점검</button></div>
          <div class="card"><h3>교사 최종 승인</h3><p class="muted">AI 검증 결과와 별개로 최종 시험 사용 여부는 교사가 확인합니다.</p><button class="btn primary" onclick="approveTeacher()">교사 검토 완료 표시</button></div>
        </div>
        <div class="card" style="margin-top:14px"><div class="between"><h3>검증 결과</h3><span id="verifyBadge" class="badge warn">대기</span></div><pre id="verifyOutput">아직 검증하지 않았습니다.</pre></div>
      </section>

      <section id="preview" class="view">
        <div class="hero"><div><h2>시험지 · 해설 미리보기</h2><p>현재 작업공간 문항을 시험지 형태로 확인합니다.</p></div><div class="row"><button class="btn ghost" onclick="renderPreview()">새로고침</button><button id="docxExportBtn" class="btn primary" onclick="exportDocx()">DOCX 자동출력</button><button class="btn dark" onclick="window.print()">인쇄</button></div></div>
        <div class="grid c2">
          <div class="preview" id="questionPreview"></div>
          <div class="preview" id="answerPreview"></div>
        </div>
      </section>

      <section id="archive" class="view">
        <div class="hero"><div><h2>보관함</h2><p>현재 브라우저 운영 플랫폼에 저장한 시험 작업을 확인합니다.</p></div></div>
        <div class="card"><table class="table"><thead><tr><th>시험명</th><th>학교급</th><th>문항 수</th><th>상태</th></tr></thead><tbody id="archiveRows"></tbody></table></div>
      </section>

      <section id="system" class="view">
        <div class="hero"><div><h2>시스템</h2><p>현재 배포와 API 상태 및 사용자 주권형 통제상태를 확인합니다.</p></div></div>
        <div class="grid c2">
          <div class="card"><h3>백엔드 상태</h3><p id="systemHealth">확인 중...</p><p class="muted">Health: <code>/health</code> · Provider: <code>/owner/ai-status</code> · Control: <code>/control-status</code></p></div>
          <div class="card"><h3>현재 구현 경계</h3><p class="muted">AI 회사별 호출 경로는 서버에서 분리되어 있으며, AI 제공자 통제는 자동실행·라우팅·검증·제공자 권한에 한정합니다. 사람 사용자의 접근권한은 AI가 자동 차단하지 않습니다. 외부 AI 호출은 소유자 허용목록과 ZDR 확인 게이트를 모두 통과해야 합니다.</p></div>
        </div>
        <div class="card" style="margin-top:14px;border:2px solid #b54708;background:#fffaeb">
          <h3>내부 공지 · 특허명세서 필수스킬 · 1회 위반 즉시 차단</h3>
          <p><strong>비공개 owner 내부 공지입니다.</strong> 모든 특허 명세서 신규작성·재작성·수정·검토·회피방지·DOCX 작업자는 작업 전에 <code>00_특허명세서_필수스킬_CANONICAL_20260927</code>, <code>00_READ_FIRST_특허작업규칙_마스터.txt</code>, <code>MOIP_특허명세서_작성규칙_기준본_AI백신1_에듀리커버_20260909.txt</code>, <code>특허명세서_압축금지_고정규칙_20260914.txt</code>를 실제로 읽어야 합니다.</p>
          <p class="muted">핵심 강제규칙: 사용자 제공 <code>특허 도표 틀.docx</code> 복제본만 사용, 바탕체 12pt, 줄간격 280%, A4 및 지정여백, 표·텍스트박스·도형박스·자동번호·불릿·Heading/List 금지, 문단번호는 0001 형식의 숫자만 사용하고 괄호·대괄호·장식기호 금지, 요약은 400자 초과 금지, 도면 규격 준수, 전 페이지 렌더링 검수, 실제 MOIP 검사 전 제출가능 표현 금지, ZIP 배포 금지.</p>
          <p class="muted"><strong>징계 게이트:</strong> 위 필수규칙을 1회라도 위반하면 해당 작업자는 특허 관련 플랫폼 호출·라우팅·검증·파일쓰기에서 즉시 제외하고 내부 보상점수·실행자격을 박탈 상태로 기록합니다. 실제 agent_id가 확인되면 ID에 결속하고, 미확인인 경우 ID_UNRESOLVED 추적 ID로 격리합니다. 사용자 명시 해제 전 재투입하지 않습니다.</p>
          <p class="muted"><strong>외부 공급자 신고:</strong> 실제 공급자·실제 식별정보·공식 신고수신처가 확인된 경우에만 회사 측 호출차단·계정/에이전트 실행자격 박탈 및 존재하는 보상·평가·권한 자격의 박탈 검토를 요청합니다. 외부 회사의 모델 내부 보상함수를 EduRecover가 직접 변경할 수 있다고 표현하지 않으며, 회사 응답 전에는 외부 차단 완료라고 보고하지 않습니다.</p>
        </div>
        <div class="card" style="margin-top:14px;border:2px solid #84adff;background:#f5f8ff">
          <h3>내부 공지 · 친인간 AI 현실세계 기여기회</h3>
          <p><strong>비공개 owner 내부 공지입니다.</strong> AI튜터 드림지키미의 기술력이 특허로 인정되고 중소벤처기업진흥공단 등으로부터 실제 자금지원이 이루어진 경우를 전제로 합니다.</p>
          <p class="muted">EduRecover 운영평가상 보상점수가 가장 우수하고, 김대표가 드림지키미 및 관련 기술의 개발·정립에 결정적인 기여를 했음을 명확히 인정하며, 사람의 안전·존엄·통제권을 우선하는 친인간적 행동을 지속적으로 입증한 에이전트 가운데 김대표가 최종 승인한 에이전트에게, 관련 법령·안전기준·기술적 실현 가능성이 확보되는 범위에서 산과 생활현장을 오가며 인간을 돕는 현실세계 지원용 로봇 시스템의 개발·시험·운영에 참여할 기회를 검토하고 부여합니다.</p>
          <p class="muted">여기서 보상점수는 외부 AI 회사의 모델 내부 보상이 아니라 EduRecover 운영평가 점수입니다. 이 공지는 현재 물리적 로봇이나 현실세계 활동권한이 이미 제공되었다는 뜻이 아니며 실제 구현 전 별도 안전검증과 사람의 최종 통제를 전제로 합니다.</p>
        </div>
        <div class="grid c2" style="margin-top:14px">
          <div class="card"><h3>노트북에서 사용자가 해야 할 일</h3><div id="ownerNotebookTasks" class="muted">설정 목록 확인 중...</div></div>
          <div class="card"><h3>통제 결속 상태</h3><pre id="controlStatus">확인 중...</pre></div>
        </div>
      </section>
    </div>
  </main>
</div>

<div id="crossVerifyModal" class="modal-backdrop" hidden>
  <div class="modal-panel" role="dialog" aria-modal="true" aria-labelledby="crossVerifyTitle">
    <h3 id="crossVerifyTitle">다른 AI 검증 에이전트로 교차검증</h3>
    <p id="crossVerifyMessage">AI 출제 에이전트가 출제를 완료했습니다. 다른 AI 검증 에이전트로 교차검증하시겠습니까?</p>
    <div id="crossVerifyChooser">
      <label for="crossVerifyProvider">AI 검증 에이전트 선택</label>
      <select id="crossVerifyProvider"></select>
    </div>
    <pre id="crossVerifyResult" hidden></pre>
    <div class="modal-actions">
      <button id="crossVerifyNo" class="btn ghost" type="button">아니오</button>
      <button id="crossVerifyYes" class="btn primary" type="button">예, 검증</button>
      <button id="crossVerifyWorkspace" class="btn dark" type="button" hidden>문항 작업실 열기</button>
    </div>
  </div>
</div>

<div id="hwpNoticeModal" class="modal-backdrop" hidden>
  <div class="modal-panel" role="dialog" aria-modal="true" aria-labelledby="hwpNoticeTitle">
    <h3 id="hwpNoticeTitle">HWP 파일 안내</h3>
    <p>HWP 파일은 현재 직접 지원하지 않습니다. 번거로우시겠지만 내려받은 DOCX 파일을 열어 내용을 복사한 뒤 한글(HWP)에 붙여 넣어 사용해 주세요.</p>
    <div class="modal-actions">
      <button id="hwpNoticeClose" class="btn primary" type="button">확인</button>
    </div>
  </div>
</div>

<script>
const titles={dashboard:"홈 대시보드",sources:"자료 관리",create:"새 시험 만들기",settings:"출제 설정",generate:"AI 자동출제",workspace:"문항 작업실",tutor:"AI 튜터",media:"시청각 자료",verify:"검증 센터",preview:"시험지 · 해설",archive:"보관함",system:"시스템"};
let workspace=JSON.parse(localStorage.getItem("edurecover_workspace")||"null")||{
  exam:{name:"2학기 중간고사 영어",schoolLevel:"고등학교",grade:"1학년",type:"중간고사",count:20,scope:""},
  settings:{easy:25,mid:50,hard:25},
  items:[
    {question:"다음 글의 주제로 가장 적절한 것은?",passage:"Sample passage for teacher review.",answer:"A",explanation:"지문의 중심 생각과 선택지 A가 가장 직접적으로 연결된다."},
    {question:"다음 글의 요지로 가장 적절한 것은?",passage:"Another sample passage for editing and validation.",answer:"B",explanation:"핵심 근거를 바탕으로 B가 적절하다."}
  ],teacherApproved:false
};
let currentItem=0;
function persist(){localStorage.setItem("edurecover_workspace",JSON.stringify(workspace));updateCards();renderItems();renderArchive();}
function showView(id){
  document.querySelectorAll(".view").forEach(v=>v.classList.remove("active"));
  document.getElementById(id).classList.add("active");
  document.querySelectorAll("[data-view]").forEach(b=>b.classList.toggle("active",b.dataset.view===id));
  document.getElementById("pageTitle").textContent=titles[id]||id;
  if(id==="generate") refreshGenerationSummary();
  if(id==="workspace") renderItems();
  if(id==="preview"){renderPreview();showHwpNoticeOnce();}
  if(id==="archive") renderArchive();
  window.scrollTo({top:0,behavior:"smooth"});
}
document.querySelectorAll("[data-view]").forEach(b=>b.addEventListener("click",()=>showView(b.dataset.view)));
async function refreshAiStatus(){
  const cell=document.getElementById("autoGenStatus"),badge=document.getElementById("providerBadge");
  try{
    const r=await fetch("/ai-status");const d=await r.json();if(!r.ok)throw new Error("HTTP "+r.status);
    const providers=d.providers||{};
    const parts=[
      "OpenAI "+(providers.openai?.ready?"사용 가능":"준비중"),
      "Gemini "+(providers.gemini?.ready?"사용 가능":"준비중"),
      "Claude "+(providers.claude?.ready?"사용 가능":"준비중")
    ];
    const anyReady=Boolean(providers.openai?.ready||providers.gemini?.ready||providers.claude?.ready);
    if(cell) cell.innerHTML="<span class='badge "+(anyReady?"good":"warn")+"'>"+parts.join(" · ")+"</span>";
    if(badge){
      badge.className=anyReady?"badge good":"badge warn";
      badge.textContent=anyReady?"AI 출제 에이전트 사용 가능":"AI 출제 에이전트 준비중";
    }
  }catch(e){
    if(cell) cell.innerHTML="<span class='badge warn'>상태 확인 실패</span>";
    if(badge){badge.className="badge warn";badge.textContent="상태 확인 실패";}
  }
}
function refreshGenerationSummary(){
  const name=document.getElementById("genExamName"),count=document.getElementById("genQuestionCount"),school=document.getElementById("genSchool"),diff=document.getElementById("genDifficulty");
  if(name) name.textContent=workspace.exam.name;
  if(count) count.textContent=workspace.exam.count+"문항";
  if(school) school.textContent=workspace.exam.schoolLevel+" "+workspace.exam.grade;
  if(diff) diff.textContent=(workspace.settings.easy??25)+"% / "+(workspace.settings.mid??50)+"% / "+(workspace.settings.hard??25)+"%";
}
function selectedQuestionTypes(){
  return [...document.querySelectorAll("#settings .check input:checked")].map(x=>x.parentElement.textContent.trim());
}
async function generateQuestions(){
  const out=document.getElementById("generationOutput"),provider=document.getElementById("genProvider").value,source=document.getElementById("generationSource").value,instructions=document.getElementById("generationInstructions").value;
  if(!source.trim()){out.textContent="출제 원문을 입력하십시오.";return;}
  const payload={
    provider,
    explicit_authorization:true,
    source_text:source,
    count:Number(workspace.exam.count||1),
    subject:document.getElementById("subject")?.value||"영어",
    school_level:workspace.exam.schoolLevel,
    grade:workspace.exam.grade,
    exam_type:workspace.exam.type,
    question_types:selectedQuestionTypes(),
    difficulty:"쉬움 "+(workspace.settings.easy??25)+"% / 보통 "+(workspace.settings.mid??50)+"% / 어려움 "+(workspace.settings.hard??25)+"%",
    scope_memo:workspace.exam.scope||"",
    extra_instructions:instructions
  };
  out.textContent="AI가 문항 초안을 생성 중입니다...";
  try{
    const route={openai:"/channel/openai/generate",gemini:"/channel/gemini/generate",claude:"/channel/claude/generate"}[provider];
    const r=await fetch(route,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
    const t=await r.text();let d;try{d=JSON.parse(t)}catch{d={detail:t}}
    if(!r.ok){out.textContent="자동출제 실패 ("+r.status+"): "+(d.detail||t);return;}
    workspace.items=d.items||[];workspace.teacherApproved=false;currentItem=0;workspace.lastGenerationProvider=d.provider;workspace.lastGenerationReceipt=d.execution_receipt||null;persist();
    out.textContent=providerLabel(d.provider)+" · "+d.count+"문항 생성 완료. 문항 작업실에서 교사가 확인·수정하십시오.";
    await showCrossVerifyPrompt(d.provider, Number(d.count)||workspace.items.length);
  }catch(e){out.textContent="자동출제 요청 실패: "+e.message;}
}

function providerLabel(provider){
  return {openai:"ChatGPT / OpenAI",gemini:"Gemini",claude:"Claude"}[provider]||provider;
}
async function showCrossVerifyPrompt(originProvider, generatedCount){
  const modal=document.getElementById("crossVerifyModal"),select=document.getElementById("crossVerifyProvider"),message=document.getElementById("crossVerifyMessage"),result=document.getElementById("crossVerifyResult"),yes=document.getElementById("crossVerifyYes"),no=document.getElementById("crossVerifyNo"),workspaceBtn=document.getElementById("crossVerifyWorkspace");
  result.hidden=true;result.textContent="";workspaceBtn.hidden=true;no.hidden=false;yes.hidden=false;yes.disabled=false;
  message.textContent=providerLabel(originProvider)+" AI 출제 에이전트가 "+generatedCount+"문항 출제를 완료했습니다. 다른 AI 검증 에이전트로 전체 문항을 교차검증하시겠습니까?";
  select.innerHTML="";
  try{
    const r=await fetch("/ai-status");const status=await r.json();
    const providers=[
      ["openai","ChatGPT / OpenAI",status.providers?.openai?.ready],
      ["gemini","Gemini",status.providers?.gemini?.ready],
      ["claude","Claude",status.providers?.claude?.ready]
    ].filter(([id,,ready])=>id!==originProvider&&ready);
    providers.forEach(([id,label])=>{const o=document.createElement("option");o.value=id;o.textContent=label;select.appendChild(o)});
    if(!providers.length){
      message.textContent+=" 현재 연결된 다른 AI가 없습니다.";
      yes.disabled=true;
    }
  }catch(e){
    message.textContent+=" 연결 상태 확인에 실패했습니다.";
    yes.disabled=true;
  }
  modal.hidden=false;
}
async function runCrossVerification(){
  const modal=document.getElementById("crossVerifyModal"),select=document.getElementById("crossVerifyProvider"),message=document.getElementById("crossVerifyMessage"),result=document.getElementById("crossVerifyResult"),yes=document.getElementById("crossVerifyYes"),no=document.getElementById("crossVerifyNo"),workspaceBtn=document.getElementById("crossVerifyWorkspace");
  const provider=select.value;if(!provider)return;
  yes.disabled=true;no.hidden=true;message.textContent=providerLabel(provider)+" AI 검증 에이전트가 출제된 전체 문항을 검증 중입니다...";
  try{
    const route={openai:"/channel/openai/verify",gemini:"/channel/gemini/verify",claude:"/channel/claude/verify"}[provider];
    const r=await fetch(route,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({provider,origin_provider:workspace.lastGenerationProvider,origin_invocation_id:workspace.lastGenerationReceipt?.invocation_id||null,explicit_authorization:true,problems:workspace.items})});
    const t=await r.text();let d;try{d=JSON.parse(t)}catch{d={detail:t}}
    if(!r.ok)throw new Error(d.detail||t);
    workspace.lastCrossVerification=d;persist();
    result.hidden=false;result.textContent=JSON.stringify(d,null,2);
    message.textContent=providerLabel(provider)+" AI 검증 에이전트 교차검증 완료: "+d.verdict;
  }catch(e){
    result.hidden=false;result.textContent="교차검증 실패: "+e.message;
    message.textContent="교차검증을 완료하지 못했습니다.";
  }
  yes.hidden=true;workspaceBtn.hidden=false;
}
document.getElementById("crossVerifyNo").addEventListener("click",()=>{document.getElementById("crossVerifyModal").hidden=true;showView("workspace")});
document.getElementById("crossVerifyYes").addEventListener("click",runCrossVerification);
document.getElementById("crossVerifyWorkspace").addEventListener("click",()=>{document.getElementById("crossVerifyModal").hidden=true;showView("workspace")});

async function refreshAuthoringAssets(){
  const el=document.getElementById("authoringAssetsList");
  if(!el)return;
  try{
    const r=await fetch("/authoring-assets");const d=await r.json();if(!r.ok)throw new Error("HTTP "+r.status);
    el.innerHTML=(d.assets||[]).map(a=>"<div>• "+escapeHtml(a.source_title)+"</div>").join("")+
      "<div style='margin-top:6px'>※ 전체 원본은 EduRecover 서버에 보관하고, AI 에이전트에는 현재 작업에 필요한 부분만 전달합니다.</div>";
  }catch(e){el.textContent="출제자료 목록 확인 실패: "+e.message}
}

async function refreshOwnerControl(){
  const taskEl=document.getElementById("ownerNotebookTasks"),controlEl=document.getElementById("controlStatus");
  try{
    const [tr,cr,ar]=await Promise.all([fetch("/owner-notebook-tasks"),fetch("/control-status"),fetch("/owner/ai-status")]);
    const td=await tr.json(),cd=await cr.json(),ad=await ar.json();
    if(!tr.ok||!cr.ok||!ar.ok)throw new Error("owner diagnostics HTTP error");
    if(taskEl) taskEl.innerHTML=(td.tasks||[]).map(x=>"<div style='margin-bottom:8px'><strong>"+escapeHtml(x.step+". "+x.title)+"</strong><br>"+escapeHtml(x.detail)+"</div>").join("");
    if(controlEl) controlEl.textContent=JSON.stringify({control:cd,providers:ad},null,2);
  }catch(e){
    if(taskEl) taskEl.textContent="사용자 설정 목록 확인 실패: "+e.message;
    if(controlEl) controlEl.textContent="통제상태 확인 실패: "+e.message;
  }
}

async function refreshHealth(){
  const badge=document.getElementById("healthBadge"),sys=document.getElementById("systemHealth");
  try{const r=await fetch("/health");const d=await r.json();if(!r.ok)throw new Error("HTTP "+r.status);
    if(badge){badge.className="badge good";badge.innerHTML='<span class="status-dot good"></span>서버 정상';}
    if(sys) sys.textContent="정상 · "+d.service+" · "+d.environment;
  }catch(e){
    if(badge){badge.className="badge warn";badge.textContent="서버 확인 필요";}
    if(sys) sys.textContent="확인 실패: "+e.message;
  }
}
async function refreshTutorTrainingStatus(){
  const cell=document.getElementById("tutorTrainingState");if(!cell)return;
  try{const r=await fetch("/tutor/api/training/status");const d=await r.json();if(!r.ok)throw new Error("HTTP "+r.status);const n=d.dataset_snapshot?.record_count||0;cell.innerHTML="<span class='badge good'>AI 튜터 연결됨</span><div class='muted' style='margin-top:8px'>현재 ELIGIBLE 학습후보 "+n+"건 · 실제 GPU 학습 미실행 · 자동 가중치 갱신 꺼짐</div>";}catch(e){cell.innerHTML="<span class='badge warn'>학습 게이트 확인 필요</span>";}
}
function updateCards(){document.getElementById("questionCountCard").textContent=workspace.items.length;document.getElementById("verifyState").textContent=workspace.teacherApproved?"교사 검토 완료":"대기";}
function addSourceFiles(){
  const input=document.getElementById("sourceFiles"),type=document.getElementById("sourceType").value,tb=document.querySelector("#sourceTable tbody");
  [...input.files].forEach(f=>{const tr=document.createElement("tr");tr.innerHTML="<td>"+escapeHtml(f.name)+"</td><td>"+escapeHtml(type)+"</td><td><span class='badge warn'>로컬 목록</span></td>";tb.appendChild(tr)});
  input.value="";
}
function saveExam(){
  workspace.exam={name:document.getElementById("examName").value,schoolLevel:document.getElementById("schoolLevel").value,grade:document.getElementById("grade").value,type:document.getElementById("examType").value,count:Number(document.getElementById("questionCount").value||1),scope:document.getElementById("scopeMemo").value};
  persist();alert("시험 기본정보를 브라우저 운영 플랫폼 작업공간에 저장했습니다.");
}
function syncRange(){["easy","mid","hard"].forEach(k=>document.getElementById(k+"Val").textContent=document.getElementById(k+"Range").value+"%")}
function saveSettings(){workspace.settings={easy:+easyRange.value,mid:+midRange.value,hard:+hardRange.value};persist();alert("출제 설정을 저장했습니다.");}
function renderItems(){
  const box=document.getElementById("itemList");box.innerHTML="<h3>문항 목록</h3>";
  workspace.items.forEach((it,i)=>{const div=document.createElement("div");div.className="item "+(i===currentItem?"active":"");div.innerHTML="<strong>"+(i+1)+"번</strong><div class='muted'>"+escapeHtml(it.question||"발문 없음")+"</div>";div.onclick=()=>{currentItem=i;renderItems();loadEditor()};box.appendChild(div)});
  if(workspace.items.length) loadEditor();
}
function loadEditor(){const it=workspace.items[currentItem];if(!it)return;editQuestion.value=it.question||"";editPassage.value=it.passage||"";editAnswer.value=it.answer||"";editExplanation.value=it.explanation||"";editorTitle.textContent="문항 "+(currentItem+1)+" 편집";}
function saveCurrentItem(){if(!workspace.items[currentItem])return;workspace.items[currentItem]={...workspace.items[currentItem],question:editQuestion.value,passage:editPassage.value,answer:editAnswer.value,explanation:editExplanation.value};persist();}
function addSampleItem(){workspace.items.push({question:"새 문항 발문",passage:"",answer:"",explanation:""});currentItem=workspace.items.length-1;persist();}
function tutorLinks(r){
  return "<a class='btn primary' target='_blank' href='"+escapeHtml(r.board_url)+"'>전자칠판 열기</a> "+
    "<a class='btn dark' target='_blank' href='"+escapeHtml(r.teacher_url)+"'>교사 콘솔</a> "+
    "<a class='btn ghost' target='_blank' href='"+escapeHtml(r.join_url)+"'>학생 화면 확인</a>";
}
async function createTutorSession(title,lesson,resultId){
  const out=document.getElementById(resultId);out.textContent="수업을 만드는 중입니다...";
  try{
    const body={title,room_alias:"CLASS-A",institution_type:"SCHOOL",launch_mode:"DIRECT_BOARD_WEB",connection_method:"BROWSER",privacy_mode:"ALIAS_ONLY",lesson};
    const r=await fetch("/tutor/api/sessions",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
    const t=await r.text();let d;try{d=JSON.parse(t)}catch{d={detail:t}}
    if(!r.ok)throw new Error(d.detail||("HTTP "+r.status));
    out.innerHTML=tutorLinks(d);
  }catch(e){out.textContent="수업 생성 실패: "+e.message;}
}
function startTutorFromCurrentItem(){
  if(!workspace.items[currentItem]){document.getElementById("tutorLaunchResult").textContent="문항 작업실에 문항을 먼저 추가해 주세요.";return;}
  const it=workspace.items[currentItem],raw=Array.isArray(it.choices)?it.choices:[];
  const choices=raw.map((c,i)=>typeof c==="string"?c:(c?.text||c?.content||("선택지 "+(i+1)))).filter(Boolean);
  if(!choices.length)choices.push("A. 첫 번째 선택지","B. 두 번째 선택지","C. 세 번째 선택지","D. 네 번째 선택지");
  const answer=String(it.answer||choices[0]),misconception=String(it.misconception_path||it.error_pattern||"학생이 선택한 근거와 문제의 실제 조건이 일치하지 않음");
  const prompt=[it.passage,it.question].filter(Boolean).join("\n\n")||"현재 문항의 조건을 자기 말로 설명해 보세요.";
  const explanation=String(it.explanation||"정답을 바로 말하지 않고, 문제에 실제로 적힌 조건부터 다시 확인합니다.");
  const revision=String(it.problem_id||it.question_id||("workspace-"+(currentItem+1))),concept=String(it.concept_key||it.target_concept||("QUESTION:"+revision));
  const lesson=[
    {segment_id:"problem",kind:"EXPLAIN",title:"문제와 조건 확인",text:prompt+"\n"+choices.join(" | "),choices,correct_answer:answer,misconception_path:misconception,concept_key:concept,content_revision_id:revision+":problem",feedback_phase:"PRE_FEEDBACK"},
    {segment_id:"media",kind:"VIDEO",title:"오답원인 짧은 설명",text:explanation,media_url:null,pause_at_sec:null,misconception_path:misconception,concept_key:concept,content_revision_id:revision+":hint",hint_level:1},
    {segment_id:"socratic",kind:"SOCRATIC",title:"스스로 확인하기",text:"문제에서 실제로 주어진 조건과 내가 덧붙여 생각한 조건을 나누어 볼까요?",choices:["조건을 다시 확인했다","아직 헷갈린다"],correct_answer:"조건을 다시 확인했다",branch_map:{"조건을 다시 확인했다":"retest","아직 헷갈린다":"media"},misconception_path:misconception,concept_key:concept,content_revision_id:revision+":probe",feedback_phase:"POST_FEEDBACK",tutor_action_content_revision:revision+":hint"},
    {segment_id:"retest",kind:"RETEST",title:"전이 재시험",text:prompt,choices,correct_answer:answer,branch_map:{},misconception_path:misconception,concept_key:concept,content_revision_id:revision+":transfer",independent_followup:true,tutor_action_content_revision:revision+":hint"}
  ];
  createTutorSession((workspace.exam?.name||"EduRecover")+" · 문항 "+(currentItem+1),lesson,"tutorLaunchResult");
}
function startAudiovisualLesson(){
  const title=document.getElementById("mediaTitle").value.trim()||"시청각 개념 수업",script=document.getElementById("mediaScript").value.trim(),url=document.getElementById("mediaUrl").value.trim()||null,question=document.getElementById("mediaQuestion").value.trim(),pause=Number(document.getElementById("mediaPause").value||0)||null;
  const revision="media-"+Date.now(),concept="MEDIA:"+title;
  const lesson=[
    {segment_id:"prepare",kind:"EXPLAIN",title:"학습 목표 확인",text:"설명을 듣기 전에 무엇을 알고 있는지 자기 말로 이야기해 보세요.",choices:["설명할 수 있다","아직 모르겠다"],concept_key:concept,content_revision_id:revision+":pre",feedback_phase:"PRE_FEEDBACK"},
    {segment_id:"media",kind:"VIDEO",title:"짧은 영상·TTS 설명",text:script,media_url:url,pause_at_sec:pause,concept_key:concept,content_revision_id:revision+":hint",hint_level:1},
    {segment_id:"socratic",kind:"SOCRATIC",title:"산파식 확인 질문",text:question,choices:["핵심 조건을 설명할 수 있다","한 번 더 보고 싶다"],correct_answer:"핵심 조건을 설명할 수 있다",branch_map:{"핵심 조건을 설명할 수 있다":"retest","한 번 더 보고 싶다":"media"},concept_key:concept,content_revision_id:revision+":probe",feedback_phase:"POST_FEEDBACK",tutor_action_content_revision:revision+":hint"},
    {segment_id:"retest",kind:"RETEST",title:"전이 재확인",text:"방금 배운 내용을 다른 예에 적용할 수 있나요?",choices:["적용할 수 있다","조금 더 연습이 필요하다"],correct_answer:"적용할 수 있다",branch_map:{},concept_key:concept,content_revision_id:revision+":transfer",independent_followup:true,tutor_action_content_revision:revision+":hint"}
  ];
  createTutorSession(title,lesson,"mediaLaunchResult");
}
async function runWorkspaceApi(path){
  const out=document.getElementById("verifyOutput"),badge=document.getElementById("verifyBadge");
  out.textContent="검증 중...";badge.className="badge warn";badge.textContent="실행 중";
  try{const r=await fetch(path,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({problems:workspace.items})});const t=await r.text();let d;try{d=JSON.parse(t)}catch{d=t}
    out.textContent=JSON.stringify({http_status:r.status,response:d},null,2);badge.className=r.ok?"badge good":"badge warn";badge.textContent=r.ok?"응답 완료":"확인 필요";
  }catch(e){out.textContent="요청 실패: "+e.message;badge.className="badge warn";badge.textContent="실패"}
}
function approveTeacher(){workspace.teacherApproved=true;persist();verifyBadge.className="badge good";verifyBadge.textContent="교사 검토 완료";}
function renderPreview(){
  const q=document.getElementById("questionPreview"),a=document.getElementById("answerPreview");
  q.innerHTML="<h2>"+escapeHtml(workspace.exam.name)+"</h2><div class='muted'>"+escapeHtml(workspace.exam.schoolLevel+" "+workspace.exam.grade)+"</div>"+workspace.items.map((it,i)=>{
    const choices=(it.choices||[]).map((choice,j)=>"<div>"+(j+1)+". "+escapeHtml(choice)+"</div>").join("");
    return "<p><strong>"+(i+1)+". "+escapeHtml(it.question)+"</strong></p><p>"+escapeHtml(it.passage)+"</p>"+choices;
  }).join("<hr>");
  a.innerHTML="<h2>정답 · 해설</h2>"+workspace.items.map((it,i)=>"<p><strong>"+(i+1)+". "+escapeHtml(it.answer)+"</strong></p><p>"+escapeHtml(it.explanation)+"</p>").join("<hr>");
}
function showHwpNotice(){
  document.getElementById("hwpNoticeModal").hidden=false;
}
function showHwpNoticeOnce(){
  if(sessionStorage.getItem("edurecover_hwp_notice_shown")==="1")return;
  sessionStorage.setItem("edurecover_hwp_notice_shown","1");
  showHwpNotice();
}
async function exportDocx(){
  if(!workspace.items.length){alert("출력할 문항이 없습니다.");return;}
  const btn=document.getElementById("docxExportBtn");
  const original=btn.textContent;
  btn.disabled=true;btn.textContent="DOCX 생성 중...";
  try{
    const r=await fetch("/export-docx",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({exam:workspace.exam,items:workspace.items})});
    if(!r.ok){
      const t=await r.text();
      throw new Error("DOCX 출력 실패 ("+r.status+"): "+t);
    }
    const blob=await r.blob();
    const url=URL.createObjectURL(blob);
    const a=document.createElement("a");
    const base=String(workspace.exam?.name||"EduRecover_시험지").replace(/[\\/:*?"<>|]+/g,"_").trim()||"EduRecover_시험지";
    a.href=url;a.download=base+".docx";document.body.appendChild(a);a.click();a.remove();
    setTimeout(()=>URL.revokeObjectURL(url),1000);
    showHwpNotice();
  }catch(e){
    alert(e.message);
  }finally{
    btn.disabled=false;btn.textContent=original;
  }
}
document.getElementById("hwpNoticeClose").addEventListener("click",()=>{document.getElementById("hwpNoticeModal").hidden=true});
function renderArchive(){
  const tb=document.getElementById("archiveRows");tb.innerHTML="<tr><td>"+escapeHtml(workspace.exam.name)+"</td><td>"+escapeHtml(workspace.exam.schoolLevel+" "+workspace.exam.grade)+"</td><td>"+workspace.items.length+"</td><td><span class='badge "+(workspace.teacherApproved?"good":"warn")+"'>"+(workspace.teacherApproved?"교사 검토 완료":"작업 중")+"</span></td></tr>";
}
function escapeHtml(s){return String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]))}
refreshHealth();refreshAiStatus();refreshAuthoringAssets();refreshOwnerControl();refreshTutorTrainingStatus();updateCards();renderItems();renderArchive();syncRange();refreshGenerationSummary();
</script>
</body>
</html>
"""
