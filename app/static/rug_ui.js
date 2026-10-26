'use strict';
(() => {
  const el = id => document.getElementById(id);
  const status = text => { el('status').textContent = text; };
  let previewRaw = null;
  let lastInput = null;
  async function api(path, data) {
    const options = {credentials:'same-origin',cache:'no-store'};
    if (data !== undefined) Object.assign(options, {method:'POST',headers:{'Content-Type':'application/json','X-Rug-Consent':'1'},body:JSON.stringify(data)});
    const response = await fetch('/life/api/rug/'+path,options);
    if (response.status === 401) { location.assign('/life/auth/login'); throw Error('로그인이 필요합니다.'); }
    const result = await response.json();
    if (!response.ok) throw Error(typeof result.detail === 'string' ? result.detail : '입력 내용과 저장 설정을 확인하십시오.');
    return result;
  }
  function button(id, action) {
    el(id).addEventListener('click', async () => {
      el(id).disabled = true;
      try { await action(); } catch (error) { status(error.message); }
      finally { el(id).disabled = false; }
    });
  }
  function list(id, values) {
    el(id).replaceChildren(...values.map(value => { const item=document.createElement('li'); item.textContent=value; return item; }));
  }
  async function loadStatus() {
    const data = await api('rag/status');
    el('retrieval').textContent = data.retrieval === 'BM25' ? '현재 검색 방식: 낱말 검색(BM25). 의미 검색용 모델이 준비되지 않았습니다.' : '현재 검색 방식: 낱말 검색과 의미 검색의 순위를 합칩니다.';
    list('sourceStatus', Object.entries(data.sources).map(([id,source]) => `${id}: 원문 ${source.verified_fulltext_docs}건, 조각 ${source.chunks}개, 확인일 ${source.latest_checked_on || '없음'}${source.last_sync?.state === 'failed' ? ' · 동기화 실패: '+source.last_sync.reason : ''}`));
  }
  async function loadHistory() {
    const data=await api('history');
    list('history',data.history.map(r=>`${r.product_name} · ${r.checked_at} · ${r.status} · 확인한 성분 ${r.confirmed_count}개`));
  }
  function showReview(data){
    const need = data.requires_user_review;
    el('reviewArea').hidden = !need;
    el('reviewNotice').textContent = need ? data.review_notice : '';
    list('unresolvedList', need ? data.unresolved_ingredients : []);
    if (need) el('reviewCorrected').value = data.ingredients.join('\n');
  }
  async function resend(reviewed){
    if(!lastInput) throw Error('먼저 분석을 실행하십시오.');
    const data = await api('analyze', Object.assign({}, lastInput, {
      corrected_ingredients: el('reviewCorrected').value.split(/\n/).filter(v=>v.trim()),
      reviewed_names: reviewed}));
    showOutcome(data);
    status(data.requires_user_review ? '아직 확인되지 않은 이름이 남아 있습니다.' : '확인하신 내용으로 대조했습니다.');
    await loadHistory();
  }
  function showOutcome(data){
    el('result').textContent=data.message+(data.status==='HOLD'?' · 최종 판정 보류':'');
    el('matchNames').textContent=data.matches.length?'일치한 등록 성분: '+data.matches.join(', '):'';
    el('scopeNotice').textContent=data.incomplete_notice;list('issues',data.issues);
    el('changes').textContent=data.changes?`이전 결과와 재검토했습니다. 추가된 성분: ${data.changes.ingredients_added.join(', ')||'없음'} / 제외된 성분: ${data.changes.ingredients_removed.join(', ')||'없음'} / 새로 일치한 등록 성분: ${data.changes.matches_added.join(', ')||'없음'}`:'처음 저장한 분석입니다.';
    el('resultArea').hidden=false; showReview(data);
  }
  async function sendPhoto(input){
    const file=input.files[0]; if(!file)return;
    try{
      if(!el('photoConsent').checked)throw Error('암호화 저장 동의를 확인하십시오.');
      if(!el('photoName').value.trim())throw Error('제품명을 입력하십시오.');
      if(file.size>8*1024*1024)throw Error('8MB 이하 사진을 선택하십시오.');
      status('사진을 읽고 있습니다.');
      const form=new FormData();
      form.append('photo',file);form.append('product_name',el('photoName').value);
      form.append('oliveyoung_url',el('photoUrl').value);form.append('explicit_consent','true');
      const response=await fetch('/life/api/rug/photo',{method:'POST',credentials:'same-origin',cache:'no-store',headers:{'X-Rug-Consent':'1'},body:form});
      if(response.status===401){location.assign('/life/auth/login');return;}
      const data=await response.json();
      if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:'사진을 읽지 못했습니다. 다시 촬영해 주십시오.');
      lastInput={product_name:el('photoName').value,oliveyoung_url:el('photoUrl').value,
        raw_text:data.read_ingredients.join('\n'),complete:false,expected_count:null,
        reading_source:'photo_ocr',explicit_consent:true};
      el('photoRead').textContent=`사진에서 읽은 성분 ${data.read_ingredients.length}개: `+data.read_ingredients.join(', ');
      showOutcome(data);
      status('사진을 읽어 바로 대조했습니다. 읽은 범위 안에서만 판정했습니다.');
      await loadHistory();
    }catch(error){status(error.message);}
    finally{input.value='';}
  }
  el('photoShot').addEventListener('change',()=>sendPhoto(el('photoShot')));
  el('photoPick').addEventListener('change',()=>sendPhoto(el('photoPick')));
  button('saveAllergens', async()=>{
    const data=await api('allergens',{allergens:el('allergens').value.split(/\n/).filter(v=>v.trim())});
    el('registered').textContent='등록한 이름: '+data.allergens.join(', '); status('등록 성분을 저장했습니다.');
  });
  el('formulaFile').addEventListener('change',async()=>{
    try {
      const file=el('formulaFile').files[0]; if(!file)return;
      if(!/\.(txt|csv)$/i.test(file.name)||file.size>300000)throw Error('300KB 이하 텍스트 또는 CSV 파일을 선택하십시오.');
      const raw=await file.text(); if(raw.length>100000)throw Error('성분 원문은 10만 자 이하로 입력하십시오.');
      el('formula').value=raw; previewRaw=null; el('previewArea').hidden=true;
      status('파일 내용을 읽었습니다. 입력 내용을 확인하십시오.');
    } catch(error){status(error.message);}
  });
  el('formula').addEventListener('input',()=>{previewRaw=null;el('previewArea').hidden=true;});
  button('preview',async()=>{
    const data=await api('formula/preview',{raw_text:el('formula').value});
    el('normalization').replaceChildren(...data.normalization.map(row=>{
      const tr=document.createElement('tr'); for(const text of [row.original,row.normalized]){const td=document.createElement('td');td.textContent=text;tr.append(td);}return tr;
    }));
    el('corrected').value=data.ingredients.join('\n');previewRaw=el('formula').value;el('previewArea').hidden=false;
    status(`확인한 성분 ${data.confirmed_count}개입니다. 잘못 나뉜 부분을 고치십시오.`);
  });
  button('analyze',async()=>{
    if(!el('consent').checked)throw Error('암호화 저장 동의를 확인하십시오.');
    if(previewRaw!==el('formula').value)throw Error('입력 내용 확인을 먼저 실행하십시오.');
    const count=el('expectedCount').value;
    lastInput={product_name:el('productName').value,oliveyoung_url:el('productUrl').value,
      raw_text:el('formula').value,complete:el('complete').checked,
      expected_count:count===''?null:Number(count),reading_source:'typed',explicit_consent:true};
    const data=await api('analyze',Object.assign({},lastInput,
      {corrected_ingredients:el('corrected').value.split(/\n/).filter(v=>v.trim())}));
    showOutcome(data); status('대조 결과와 확인 범위를 표시했습니다.');await loadHistory();
  });
  button('applyCorrection',()=>resend(el('unresolvedList').textContent?Array.from(el('unresolvedList').children).map(li=>li.textContent):[]));
  button('keepAsIs',()=>resend(Array.from(el('unresolvedList').children).map(li=>li.textContent)));
  button('search',async()=>{
    const data=await api('rag/search',{q:el('question').value});el('answer').replaceChildren();
    if(data.state==='NO_EVIDENCE'){const p=document.createElement('p');p.textContent=data.answer;el('answer').append(p);}
    for(const sentence of data.sentences){
      const p=document.createElement('p');p.textContent=sentence.text+' ';
      const a=document.createElement('a');a.href=sentence.citation.url;a.target='_blank';a.rel='noopener noreferrer';
      a.textContent=`[${sentence.citation.source_name} · ${sentence.citation.title} · ${sentence.citation.checked_on} · 원문]`;p.append(a);el('answer').append(p);
    }
    if(data.multiple_sources){const p=document.createElement('p');p.textContent='여러 출처의 근거를 함께 표시했습니다. 내용이 다를 수 있어 판정을 보류했습니다.';el('answer').append(p);}
    status('공식 원문에서 대조한 근거를 표시했습니다. 검색 방식: '+data.search_mode);
  });
  button('loadHistory',loadHistory);
  (async()=>{try{const data=await api('allergens');el('allergens').value=data.allergens.join('\n');el('registered').textContent='등록한 이름: '+data.allergens.join(', ');await Promise.all([loadStatus(),loadHistory()]);status('전성분을 입력할 수 있습니다.');}catch(error){status(error.message);}})();
})();
