"""Teacher pilot landing page for EduRecover AI."""

PILOT_HTML = r"""
<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>EduRecover AI · 교사 파일럿</title>
  <style>
    :root { font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color:#111827; background:#f3f4f6; }
    * { box-sizing:border-box; }
    body { margin:0; }
    header { background:#111827; color:white; padding:22px 18px; }
    header h1 { margin:0 0 6px; font-size:22px; }
    header p { margin:0; color:#d1d5db; font-size:14px; }
    main { max-width:980px; margin:0 auto; padding:18px; }
    .grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(260px,1fr)); gap:14px; }
    .card { background:white; border-radius:14px; padding:16px; box-shadow:0 1px 5px rgba(0,0,0,.08); }
    .card h2 { margin:0 0 10px; font-size:17px; }
    .badge { display:inline-block; border-radius:999px; padding:4px 8px; background:#e5e7eb; font-size:12px; margin-right:6px; }
    textarea { width:100%; min-height:260px; border:1px solid #d1d5db; border-radius:10px; padding:10px; font-family:ui-monospace, monospace; font-size:12px; }
    button { border:0; border-radius:10px; padding:10px 12px; font-weight:700; cursor:pointer; margin:6px 6px 0 0; }
    .primary { background:#111827; color:#fff; }
    .secondary { background:#e5e7eb; color:#111827; }
    pre { white-space:pre-wrap; overflow-wrap:anywhere; background:#0f172a; color:#e2e8f0; border-radius:10px; padding:12px; min-height:120px; font-size:12px; }
    .status { font-weight:700; }
    .ok { color:#047857; }
    .warn { color:#b45309; }
    .muted { color:#6b7280; font-size:13px; line-height:1.5; }
    footer { color:#6b7280; font-size:12px; padding:0 18px 24px; max-width:980px; margin:0 auto; }
    a { color:#2563eb; text-decoration:none; }
  </style>
</head>
<body>
<header>
  <h1>EduRecover AI · 교사 파일럿</h1>
  <p>교사용 문항 출제·검증 파일럿 · Trust Chain · Socratic Trap · 상태 확인</p>
</header>

<main>
  <section class="grid">
    <div class="card">
      <h2>시스템 상태</h2>
      <p id="health" class="status warn">확인 중...</p>
      <p class="muted">백엔드와 헬스체크 상태를 표시합니다.</p>
      <a href="/docs" target="_blank">API 문서 열기</a>
    </div>
    <div class="card">
      <h2>검증 원칙</h2>
      <span class="badge">다수결 배척</span>
      <span class="badge">Fail-Closed</span>
      <p class="muted">필수 검증이 충족되지 않으면 승인하지 않고 재검증 대기 상태로 보냅니다.</p>
    </div>
    <div class="card">
      <h2>교사 파일럿</h2>
      <p class="muted">문항 수는 교사가 자유롭게 정합니다. 현재 화면은 출제된 문항 데이터의 검증 흐름을 확인하는 파일럿입니다.</p>
    </div>
  </section>

  <section class="card" style="margin-top:14px">
    <h2>문항 데이터 검증</h2>
    <p class="muted">형식 예: {"problems":[{"question":"...","answer":"...","explanation":"...","passage":"..."}]} · 문항 수 제한 없음</p>
    <textarea id="payload" placeholder='{"problems":[...]}'></textarea>
    <div>
      <button class="secondary" onclick="fillDemo()">샘플 문항 채우기</button>
      <button class="primary" onclick="callApi('/verify-problem')">Trust Chain 검증</button>
      <button class="primary" onclick="callApi('/apply-trap')">Socratic Trap 적용</button>
    </div>
  </section>

  <section class="card" style="margin-top:14px">
    <h2>결과</h2>
    <pre id="result">아직 실행하지 않았습니다.</pre>
  </section>
</main>

<footer>
  EduRecover AI Teacher Pilot · 교사가 출제·검토하는 문항 제작 지원용 파일럿입니다.
</footer>

<script>
async function refreshHealth() {
  const el = document.getElementById("health");
  try {
    const res = await fetch("/health");
    const data = await res.json();
    el.textContent = res.ok ? "정상 · " + data.service + " · " + data.environment : "오류";
    el.className = "status " + (res.ok ? "ok" : "warn");
  } catch (e) {
    el.textContent = "연결 실패";
    el.className = "status warn";
  }
}

function fillDemo() {
  const problems = Array.from({length:3}, (_,i)=>({
    problem_id:i+1,
    question:"Demo question " + (i+1),
    answer:"A",
    explanation:"The explanation is grounded in the supplied passage.",
    passage:"This is demo passage " + (i+1) + "."
  }));
  document.getElementById("payload").value = JSON.stringify({problems}, null, 2);
}

async function callApi(path) {
  const out = document.getElementById("result");
  let body;
  try {
    body = JSON.parse(document.getElementById("payload").value || "{}");
  } catch (e) {
    out.textContent = "JSON 형식 오류: " + e.message;
    return;
  }
  out.textContent = "실행 중...";
  try {
    const res = await fetch(path, {
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify(body)
    });
    const text = await res.text();
    let parsed;
    try { parsed = JSON.parse(text); } catch { parsed = text; }
    out.textContent = JSON.stringify({http_status:res.status, response:parsed}, null, 2);
  } catch (e) {
    out.textContent = "요청 실패: " + e.message;
  }
}
refreshHealth();
</script>
</body>
</html>
"""
