# Socratic Tutoring FTO Deep Research — 2026-09-26

상태: WORKING FTO RECORD / NOT A LEGAL OPINION  
배포: 내부 검토용  
훈련 상태: FIRST-PARTY SOCRATIC MODEL TRAINING = HOLD

## 1. 사용자 철학의 고정 정의

본 프로젝트의 산파식 교수축은 다음 목적을 가진다.

1. 전달하려는 목표개념(target concept)을 수업 전에 먼저 고정한다.
2. 목표개념에 대해 예상되는 오개념/경계오류를 사전에 정의한다.
3. 각 오개념 또는 경계오류를 드러내기 위한 함정질문/경계질문을 미리 설계한다.
4. 정답을 선행 제공하지 않는다.
5. 학생 응답에 따라 질문의 표현·조건·사례·맥락을 바꾸되 목표개념은 유지한다.
6. 학생이 스스로 개념을 설명·적용할 수 있을 때까지 질문을 계속 바꾼다.
7. 마지막 확인은 새로운 개념 추천이 아니라 동일 목표개념의 미노출 전이문항이다.
8. 목표는 오답 매력도 증가나 시험 변별도 향상이 아니라 개념의 자발적 이해와 장기 파지다.

이를 평가문항 자동생성/오답 최적화 축과 혼합하지 않는다.

## 2. Claude 기록 전체 대조 결과

Google Drive의 "클로드 채팅정리" 폴더에서 다음 저장문서 전체 본문을 직접 대조했다.

- Claude 작업정리 — 영어 문항 생성 시스템 및 특허 (마스터)
- Claude 작업정리 제2부 — 특허 조사 이후
- Claude 작업정리 제3부 — 선행기술 구현 완료
- 선행기술 총정리 및 조사 전략
- 출제 패턴 매뉴얼 — 6대 기술·빈칸 ADG·선행기술 원리
- 통합 운영기준 단일본 v2.0 — 매뉴얼·코드·특허 대응

중요: 저장 폴더 자체가 Claude 전체 raw conversation export는 아니며, 작업정리/조사/매뉴얼 문서의 보존본이다.

### 2.1 Claude가 "학생 응답 미사용"을 정한 직접 맥락

그 금지선은 문항합성/오답설계 축에서 설정되었다.

- 학생 선택률을 수집하여 오답 매력도를 사후 최적화하는 P4 기능을 제외.
- 학생 선택률을 이용해 오답 생성기를 DPO 재학습하는 P5-S4 기능을 제외.
- 문항합성 명세서의 독립항에서 "학습자 응답·선택 데이터를 입력으로 하지 않는다"는 한정을 사용.
- DB에서 기존 문제를 정렬/추천하는 구조와 분리.

따라서 "어떤 학생 응답도 저장하거나 분석하면 안 된다"는 전 프로젝트 공통 원칙으로 확대해석하지 않는다.

### 2.2 Claude가 별도로 기록한 산파식 축

Claude 마스터에는 산파식 축이 별도로 다음처럼 기록돼 있다.

- 학습목표 설정
- 개념 도달까지 질문 연쇄
- 함정질문으로 오개념 노출
- 알고 맞췄는지/찍어서 맞췄는지 판별
- 일주일 후 전이문제로 개념 지속성 확인
- 목표는 변별도 향상이 아니라 장기 파지

따라서 문항합성용 선택률 피드백 루프와 산파식 수업응답의 진단용 이용을 별도로 취급해야 한다.

## 3. 기존 자체 특허축 직접 대조

### 3.1 2026-08-25 계열

내부 기준파일:
- 20260824_특허출원용_명세서_최종본_v0.16.4.docx
- 관련 출원번호 기록: 10-2026-0159430

확인한 핵심:
- 복수 잠재지식상태 유지
- 기능적 오답/오류규칙/목표개념 결속
- 선택답 + 이유응답을 포함한 후속응답 수신
- 후속응답에 따라 사후확률 갱신
- 후속 검증 또는 교정문항 선택
- 반례/조건역전/예외경계/전이 질문정책
- 동일 단원에서 교정 후 미노출 전이/지연 확인

따라서 산파식 수업에서 학생 응답을 "현재 목표개념의 진단과 교정"에 사용하는 것 자체는 자체 특허기재와 정면 모순되지 않는다.

### 3.2 2026-09-04 계열

내부 HLZ 직접대조 기록:
- 출원3최종1.hlz
- SHA-256: 209b40ffbbdeb3823c408ed65aa42e03e6f1a1b470e58c944910cbfefb085644
- 출원번호: 10-2026-0168145

직접 확인 기록상:
- 실제 노출된 교육 구성요소
- 답/이유/확신도 등 응답증거
- 복수 원인가설
- 진단목적에 따른 전문 AI교사의 후속행동
- 즉시/지연/전이 목적코드
- 노출확인
- 증거편입 전 적격성 확인
- 사후 결함 선택회복

이 축과 현재 concept-bound trajectory는 강하게 연결되므로 신규 특허를 만들 때 기존 권리축을 중복해서 독립발명의 중심으로 주장하지 않는다.

## 4. 외부 선행기술 심층조사 — 핵심 관련군

### 4.1 Riiid / Socra 계열

대표:
- KR102096301B1 — 액티브 러닝 기법을 적용한 머신 러닝 프레임워크
- KR102213479B1 / 480 / 481 / 482
- KR101996249B1 계열

KR102096301B1의 핵심 결합은:
- 복수 문제 DB
- 사용자 풀이결과 수집
- 사용자/문제 데이터 분석 모델
- 분석모델에 필요한 데이터를 제안하는 전문가 모델
- 전문가모델 추천으로 DB에서 문제 추출
- 풀이결과로 분석모델 업데이트
- 분석모델 정확도 향상 방향의 reward로 전문가모델 업데이트

따라서 EduRecover에서는 다음을 분리한다.
- student choice rate -> distractor ranking
- student response -> problem recommendation from item DB
- student response -> assessment item generator retraining

위 3개는 Socratic tutor training과 같은 데이터파이프에 두지 않는다.

### 4.2 Turing 계열

대표:
- KR102716531B1
- KR102716530B1
- KR102716532B1
- KR102716528B1 등

관련 중심:
- 문제 제공
- 풀이데이터 수신
- 단원별 skill range 갱신
- 진단 정확도 산출/종료
- 진단용 문제 제공

우리 산파식 축을 "취약 단원 추정 후 DB에서 다음 문제 추천" 구조로 설계하지 않는다.

### 4.3 수학 튜터 대화 특허

KR102655228B1:
- 수학 문제 집합/풀이과정/풀이히스토리 DB
- 맞춤형 문제 선별/제시
- 풀이 질문 분석
- 풀이과정 정오 평가
- 개념 질문에 설명 제공
- 언어모델 추가학습에 의한 tutoring dialogue/evaluation

차별설계:
- 단순 풀이과정 평가 + 설명제공이 중심이 되지 않도록 한다.
- precommitted target concept 및 misconception-specific predesigned probes를 별도 객체로 유지한다.
- 정답설명 제공을 기본 동작으로 두지 않는다.
- 동일개념 전이 성공을 별도 종료/검증조건으로 둔다.

KR20250063563A:
- 학습자의 step-by-step 풀이
- AI가 다음 풀이 step 예측
- 실제 step과 예측 step 비교
- 오류 또는 해결방법을 대화메시지로 제공

차별설계:
- "정답 풀이경로와 학생 경로의 next-step 비교"가 핵심 판정기가 되지 않도록 한다.
- 목표개념/오개념가설/경계질문에 기초한 진단을 중심으로 유지한다.

### 4.4 Socratic/Maieutic LLM tutoring

KR102967727B1 / KR20260018584A:
- LLM 질문생성
- 복수회 질의응답
- Socratic Maieutics를 통한 자기발견

따라서 "LLM이 소크라테스식으로 여러 번 질문한다" 자체는 신규성 중심으로 잡지 않는다.

### 4.5 사전설계 trap-state adaptive tutorial

WO2014127415A1 / US20150339942A1 계열:
- 저자가 예상 오류/오개념을 미리 고려해 trap state를 사전 작성
- trap state를 learning objective에 결속
- trap 발생을 mastery evidence로 사용
- knowledge model 갱신
- feedback/hint/further task 제공
- 실제 student data로 trap/mastery algorithm을 나중에 개선할 수도 있음

이 선행기술은 사용자의 "목표개념 + 미리 설계한 함정"과 가장 가까운 강한 선행기술이다.

따라서 신규 청구를 단순히:
"목표개념을 미리 정하고 함정문항을 만든다"
로 잡으면 안 된다.

### 4.6 2025 HCLT / 2026 Journal of KIISE 연구

"소크라테스식 질문 생성을 위한 학생 이해상태 기반 수학 튜터링 데이터셋"
- 문제 이해 상태와 개념 이해 상태를 분리
- 상태에 기반한 맞춤형 Socratic question
- tutor-validator-student 3-agent
- 3,084 Korean math tutoring dialogues
- open-source model 학습/비교

따라서:
"학생 이해상태를 진단한 후 소크라테스식 질문을 생성"
자체도 신규성 중심으로 잡지 않는다.

## 5. 현재 보호해야 할 차별 결속 후보

현재 조사에서 단일 문헌의 독립적 핵심으로 동일하게 확인되지 않은 조합을 중심으로 추가검색한다.

1. 하나의 target concept를 수업 시작 전에 고정.
2. target concept의 misconception/boundary hypothesis 집합을 사전 고정.
3. 각 hypothesis에 trap/boundary/counterexample probe를 사전 결속.
4. answer key 또는 직접 정답설명을 concept recovery 전까지 비노출.
5. 응답에 따라 "새 개념/추천문제"로 이동하지 않고 같은 target concept 안에서 question surface/condition/context만 변형.
6. 학생이 자기 언어로 설명/적용하는 concept recovery evidence를 요구.
7. 동일 concept를 새로운 표면·조건·맥락에 적용하는 unseen transfer를 필수 확인.
8. 필요 시 delayed transfer를 별도 확인.
9. tutoring trajectory는 assessment-item attractiveness/choice-rate/DPO feedback loop와 기술적으로 분리.
10. tutoring trajectory의 tutor-model training 이용은 별도 IP/FTO clearance gate 통과 후에만 허용.

주의: 위 "조합 전체가 신규/진보"라고 아직 결론내리지 않는다. 추가 국가/패밀리/인용망 검색이 필요하다.

## 6. 코드상 현재 강제경계

현재 feature branch에서:
- ConceptTargetPlan 추가
- 모든 tutoring turn을 same target concept에 결속
- concept recovery 전에 answer reveal 금지
- transfer/delayed review는 same target concept만 허용
- used_for_item_generation 차단
- used_for_distractor_optimization 차단
- used_for_choice_rate_ranking 차단
- Tutor-model training은 explicit IP/FTO clearance 전 HOLD

## 7. 현재 결정

### 허용
- 실제 수업자료 증거 보존
- 비식별화
- 동일 목표개념의 runtime dialogue 연구
- 동일 목표개념 transfer evaluation 설계
- 공개 라이선스 데이터로 GPU pipeline smoke test

### HOLD
- 실제 학생 trajectory를 gpt-oss tutor training dataset으로 승격
- 산파식 응답으로 모델 지속학습 실행
- 산파식 응답 기반 reward 최종 설계

### 계속 금지
- 산파식 응답 -> 평가문항 생성기
- 산파식 응답 -> distractor attractiveness optimization
- 산파식 선택률 -> DPO item generator retraining

## 8. 미확정 조사목록

- Turing 전체 family의 독립항/종속항 전수대조
- Socra/Riiid 해외 family 및 심사경과
- WO2014127415 family의 현재 국가별 권리상태/claims
- KR102967727B1의 전체 독립항 및 출원경과
- HCLT 2025 conference original proceedings publication date/content
- Weverse Mind memory-based dialogue patent exact number/claims
- 국내 G09B7/00 + G06N/G06F NLP 교차분류 추가검색
- US/EP/JP/CN/PCT의 concept mastery + trap + transfer + Socratic tutoring 결합 검색

실제 proprietary tutor training은 이 목록의 핵심 위험군 대조가 끝날 때까지 HOLD한다.
