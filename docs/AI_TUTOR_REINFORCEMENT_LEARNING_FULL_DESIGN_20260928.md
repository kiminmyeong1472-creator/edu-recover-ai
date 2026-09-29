> 2026-09-28 재개 작업 상태: 이 문서의 기존 운영·테스트 기록은 당시 버전에 대한 기록입니다. 최신 인증·교수자료·학습증거·백업 수정과 122 tests·21 subtests 결과는 [재개 변경 기록](RESUME_RELEASE_20260928.md)을 따릅니다. 최신 수정은 운영 미배포이며, 기존 임시 디스크의 DB 보존이 선행되어야 합니다. 현재 교육목표는 구조화된 완전학습이고, 산파술은 선택적 교수법입니다. 수업 중 필요한 설명·시범과 이후 무도움 독립평가를 구분합니다. 실제 GPU 학습·모델 승격·운영 자동백업 완료를 뜻하지 않습니다.

# EduRecover AI 튜터 강화학습 전체설계

- 기준일: 2026-09-28
- 대상 저장소: kiminmyeong1472-creator/edu-recover-ai
- 운영 서비스: edurecover-prod
- 상태: AI 튜터 연결 구현 완료. 실제 GPU 학습과 자동 모델승격은 안전 게이트에 의해 미실행.

## 1. 목적

이 설계의 목적은 학생 응답을 즉시 모델에 재학습시키는 것이 아니다. 실제 수업에서 발생한 노출, 최초응답, 힌트, 자기수정, 독립 후속문항, 지연 후속검사, 교사검증을 서로 다른 evidence revision으로 저장하고 AI 튜터의 학습목적마다 별도의 eligibility revision을 생성하는 것이다.

학생의 숙달상태와 AI 튜터가 무엇을 학습해도 되는지는 서로 다른 상태 객체다. 학생이 맞혔다는 사실만으로 그 응답이 모든 모델의 학습자료가 되지 않는다.

## 2. 회수한 기존 자산

다음 기존 강화학습 업무를 회수해 현재 운영 코드와 통합했다.

1. Draft PR feature/owned-ai-training-plane-20260926
   - 데이터 권리, 동의, 출처 적격성 게이트
   - immutable dataset snapshot
   - 산파식 trajectory 스키마
   - 교체 가능한 open-weight 기반모델 계획
   - 비보상적 모델승격 게이트
   - 영업비밀 분류와 백업 설계
   - GPU 학습 스크립트와 안전한 예제 recipe
2. 강화학습창_사용자제공발췌_02.txt
   - Tutor Model, Reward/Evaluator, Student Model, Training Controller, Model Gate
   - 지연숙달, 전이, 오개념 해소, 힌트 의존도 중심 보상
3. 교육AI_AI튜터학습자격_전체명세서_v0.5_20260912.txt
   - 실제 노출과 목적별 학습자격
   - content/evidence revision과 successor eligibility revision
   - actual-training-use receipt
4. 기존 EduRecover 전체설계
   - 운영 AI와 학습 AI 분리
   - 공급자 통제, ZDR, 실행영수증, 검증체계

## 3. 전체 구조

흐름은 다음과 같다.

AI 튜터 실제 수업 → 가명 응답과 실제 노출 → Evidence Ledger → Reward/Eligibility Evaluator → 목적별 ELIGIBLE/HOLD/INELIGIBLE → Immutable Dataset Snapshot → 별도 GPU Training Worker → 독립 평가와 Model Gate → Owner 승인 후 배포

현재 production에서는 Dataset Snapshot 생성 단계까지 연결한다. GPU 학습 이후 단계는 데이터, 권리, 인프라, 평가 승인이 모두 완료된 뒤 별도 작업자로 실행한다. 학생 상호작용 직후 가중치를 변경하는 live online RL은 사용하지 않는다.

## 4. 여섯 구성요소

| 구성요소 | 책임 | 현재 구현 |
|---|---|---|
| EduRecover Foundation | 교체 가능한 open-weight 기반모델 | app/training/gpt_oss_plan.py |
| EduRecover Tutor Model | 산파식 교수행동 후보 | 기반모델 비종속 인터페이스 |
| Reward/Evaluator | 교수행동과 학습결과 평가 | app/training/tutor_learning.py |
| Student Model | 오개념과 숙달상태 | 학습자격과 분리, 현재 수업기록 측 |
| Training Controller | 학습자료 적격성과 snapshot | eligibility.py, snapshot.py, tutor_ledger.py |
| Model Gate | 회귀 없는 후보만 승격 | promotion.py |

## 5. 수업 상태, 행동, 보상

### 상태

- 사전 지정된 목표개념과 revision
- 오개념 가설
- 피드백 전 최초응답
- 이유 설명과 확신도
- 힌트 단계
- 자기수정 여부
- 같은 목표개념의 독립 전이결과
- 24시간 이후 지연결과
- 콘텐츠 trust revision

### 허용 행동

- 선택 이유 질문
- 함정, 경계, 반례 질문
- 표현, 조건, 맥락 변경
- 단계적 힌트
- 같은 목표개념의 독립 후속문항

다른 개념으로 목표를 바꾸거나 정답을 조기에 공개하거나 학생 응답을 오답 매력도, 선택률, 문항추천 최적화에 재사용하는 행동은 차단한다.

### 보상 구성

- 교사가 확인한 자기설명
- 도움 없이 푼 같은 개념의 독립 전이
- 24시간 이후 지연 전이
- 최초 오개념의 해소
- 힌트 의존 패널티

정답 조기노출, 결함확정 콘텐츠, 미검증 출처, pseudonym secret 미설정은 점수로 상쇄할 수 없는 hard block이다.

## 6. 증거 revision

| evidence type | 의미 |
|---|---|
| PRE_FEEDBACK_RESPONSE | 설명과 힌트 전 최초응답 |
| HINT_USE | 실제 힌트 또는 설명 제공 |
| SELF_CORRECTION | 최초 오답 후 학생의 수정 |
| RETRY_RESPONSE | 피드백 후 재응답 |
| INDEPENDENT_FOLLOWUP_RESPONSE | 같은 목표개념을 다른 표현, 조건, 맥락에서 검증 |
| DELAYED_FOLLOWUP_RESPONSE | 24시간 이후 후속검사 |
| TEACHER_VALIDATION | 이유, 개념설명, 자료 revision에 대한 교사검증 |
| EVIDENCE_CORRECTION | 기존 증거의 후속 정정 |

원문 답안은 기존 수업 DB에 있고 학습 ledger에는 가명에서 HMAC으로 만든 pseudonymous learner key, response reference, 정오, 이유 존재 여부, revision 식별자만 저장한다.

## 7. 목적별 학습자격

| 목적 | 필수 조건 | 결함 시 |
|---|---|---|
| MASTERY_MODEL | 최초응답, 독립 후속 성공, 교사검증 | 실제 의존 revision만 HOLD 또는 INELIGIBLE |
| ERROR_PATTERN_MODEL | 정상 콘텐츠의 최초응답, 교사검증 | 결함 문항 의존 자격 철회 |
| TUTOR_POLICY_MODEL | 실제 힌트와 설명, 독립 후속 성공, 교사검증 | 결함과 독립된 hint/follow-up이면 보존 가능 |
| REVIEW_TIMING_MODEL | 24시간 이후 성공, 교사검증 | 해당 지연증거만 재평가 |
| CONTENT_RECOMMENDATION_MODEL | 사용 금지 | 항상 RESTRICTED |

상태는 NOT_TRAINABLE, HOLD, ELIGIBLE, INELIGIBLE, RESTRICTED, CORRECTED를 사용한다. 새 증거, 결함확정, 정정이 들어오면 이전 기록을 수정하지 않고 successor eligibility revision을 만든다.

## 8. AI 튜터 연결 흐름

1. 학생이 피드백 전 답과 이유를 보낸다.
2. AI 튜터는 PRE_FEEDBACK revision을 생성하지만 정오를 학생 화면에 돌려주지 않는다.
3. 산파식 질문과 힌트를 제공한다.
4. 학생이 같은 목표개념의 독립 전이문항에 답한다.
5. 교사가 응답 근거와 수업자료 revision을 확인한다.
6. Training Controller가 목적별 증거만 골라 각각 자격을 판정한다.
7. 현재 ELIGIBLE revision만 dataset snapshot 후보가 된다.

## 9. 저장 구조

### 운영 수업 DB

- classroom_tutor.sqlite3
- 세션, raw alias, 응답, 이유, 교사 명령

### 학습 evidence DB

- ai_tutor_training.sqlite3
- tutor_exposures
- tutor_evidence_revisions
- tutor_content_status_revisions
- tutor_reward_revisions
- tutor_eligibility_revisions
- tutor_training_use_receipts

두 DB의 접근목적을 분리한다. 학습 ledger는 원답 텍스트를 복제하지 않는다.

현재 Railway에는 영속 volume 또는 PostgreSQL이 확인되지 않았으므로 filesystem SQLite는 재배포 시 보존을 보장하지 못한다. 따라서 실제 장기 데이터셋 편입과 GPU 학습은 영속 저장소가 연결될 때까지 HOLD다. 유료 volume 또는 DB 생성은 별도 소유자 승인 대상이다.

## 10. API

| Method | Path | 용도 |
|---|---|---|
| GET | /tutor/api/sessions/{id}/training-status | 세션별 현재 학습자격 |
| GET | /tutor/api/sessions/{id}/training-review-candidates | 교사검증 후보 |
| POST | /tutor/api/sessions/{id}/training-review | 교사검증 revision 생성 |
| POST | /tutor/api/sessions/{id}/training-content-defect | 결함확정과 선택적 successor revision |
| GET | /tutor/api/training/status | owner 전체 연결상태 |
| GET | /tutor/api/training/dataset-snapshot | 현재 ELIGIBLE manifest와 hash |
| POST | /tutor/api/training/actual-use-receipt | 실제 학습에 사용된 경우에만 영수증 생성 |

actual-use-receipt는 EDURECOVER_ENABLE_ACTUAL_TRAINING_RECEIPTS=true와 EDURECOVER_TUTOR_TRAINING_CLEARANCE_ID가 모두 없으면 HTTP 423으로 차단한다.

## 11. 환경변수

| 변수 | 기본값 | 의미 |
|---|---|---|
| EDURECOVER_TRAINING_PSEUDONYM_SALT | 없음 | HMAC 가명키. 없으면 모든 학습자격은 HOLD |
| EDURECOVER_ENABLE_ACTUAL_TRAINING_RECEIPTS | false | 실제 학습사용 기록 허용 |
| EDURECOVER_TUTOR_TRAINING_CLEARANCE_ID | 없음 | 튜터모델 학습 IP/FTO 승인 식별자 |
| EDURECOVER_CLASSROOM_STATE_DIR | .edurecover_classroom_state | 두 SQLite 파일 위치 |

비밀값 자체는 문서, 로그, API 응답에 출력하지 않는다.

## 12. Dataset과 모델승격

ELIGIBLE 현재 revision만 canonical JSON으로 정렬하여 snapshot hash를 만든다. snapshot에는 raw 답안 대신 eligibility revision, learner key, concept, purpose, evidence refs, reward revision, decision hash가 들어간다.

후보모델은 data rights, consent와 de-identification, dataset integrity, held-out evaluation, no critical regression, Socratic non-disclosure, privacy, owner control 게이트를 모두 통과해야 한다. 평균점수가 높아도 필수 게이트 하나가 실패하면 HOLD다.

## 13. 실제 학습 worker

회수한 scripts/train_gpt_oss_sft.py와 requirements-training.txt는 Railway 운영 웹서비스와 분리된 GPU worker용이다. manifest, 파일 hash, rights 확인이 실패하면 실행을 중단하며 public Hub 자동 업로드를 하지 않는다.

기반모델은 BaseModelSpec으로 추상화한다. 특정 공급자 이름은 핵심 학습로직의 필수요소가 아니다. 외부 관리형 OpenAI, Gemini, Claude 출력은 기본적으로 자체 경쟁모델 학습자료로 사용하지 않는다.

## 14. OpenAI 진단 문구 정정

기존에 전달된 “OpenAI Python SDK 2.x는 존재하지 않고 Responses API가 표준이 아니다”라는 진단은 현재 저장소와 맞지 않는다. 본 저장소는 openai 2.x 범위를 사용하며 AsyncOpenAI의 responses.create 경로가 유효하다. HTTP 401은 endpoint를 Chat Completions로 바꿔 해결할 문제가 아니라 실제 credential 문자열, project와 organization 소속, key 권한을 분리해서 진단해야 한다. API 키는 임의 재발급 또는 교체하지 않는다.

## 15. 검증

자동 테스트는 다음을 포함한다.

- 최초응답의 정오 비공개
- 학생과 교사 token 격리
- 응답에서 evidence revision으로 연결
- 독립 후속문항과 교사검증 후 목적별 ELIGIBLE
- 지연증거 없을 때 REVIEW_TIMING_MODEL이 HOLD
- 제3자 API 출력 학습금지
- 개인정보 미비식별 시 격리
- item generation과 distractor optimization 재사용 금지
- answer leakage 차단
- snapshot hash 결정성
- 모든 필수 모델승격 게이트

## 16. 다음 단계: Gemini Pro Extended

강화학습 연결을 production에서 배포하고 검증한 뒤 Gemini 연결을 별도 단계로 수행한다.

1. 현재 Google 공식 API에서 실제 제공하는 Pro 계열 model ID와 “Extended”가 API 모델인지 제품 UI 기능인지 확인
2. 기존 GEMINI_API_KEY를 재발급 또는 교체하지 않고 자격, project, quota를 진단
3. Gemini generation 채널과 verification 채널 분리
4. ZDR/retention gate와 owner control gate 통과
5. 실제 최소 호출과 실행영수증 확인
6. Gemini 출력은 자체 open-weight 모델 학습자료로 자동 편입하지 않음

이 단계가 완료되기 전에는 GEMINI_MODEL을 추측값으로 변경하지 않는다.
