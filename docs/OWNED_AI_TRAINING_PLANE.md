# EduRecover Owner-Controlled AI Training Plane

기준일: 2026-09-26  
상태: 설계/구현 브랜치 초안

## 1. 목적

EduRecover의 장기 목표는 특정 외부 API 모델에 종속된 서비스를 만드는 것이 아니라, 사용자가 통제하는 데이터·교수정책·평가체계·학습절차를 축적하고 교체 가능한 오픈 가중치 기반모델을 지속적으로 개선하는 것이다.

초기 기반모델 후보는 OpenAI의 gpt-oss 계열로 둘 수 있으나, 핵심 인터페이스는 특정 모델명을 필수요소로 사용하지 않는다. 기반모델은 `BaseModelSpec`으로 추상화하여 향후 다른 오픈 가중치 모델로 교체할 수 있어야 한다.

## 2. 운영 AI와 학습 AI의 분리

기존 `app/services/control_plane.py`의 외부 공급자 실행경로와 새 `app/training/` 학습경로를 분리한다.

```text
External managed AI APIs
    -> generation / verification
    -> Trust Chain
    -> student/teacher runtime

                    X  (no automatic training-data flow)

First-party / licensed / owner-authored data
    -> rights + consent + provenance gate
    -> TRAINING_ELIGIBLE dataset snapshot
    -> owner-controlled training
    -> candidate checkpoint
    -> independent evaluation
    -> promotion gate
    -> ACTIVE model
```

외부 관리형 API 출력은 기본적으로 자체 모델 훈련 데이터로 사용하지 않는다. 계약상 명시적 허용이 확인되는 경우에도 별도 정책변경과 권리검토 없이는 자동 편입하지 않는다.

## 3. 산파식 수업 trajectory

대표 실시례는 실제 최상위수학 산파식 수업을 비식별화한 구조를 기준으로 한다. 공개 코드와 일반 운영 DB에는 실명·정확한 장소·원음성 등 식별 가능한 원자료를 넣지 않는다.

권장 trajectory 요소:

1. 문제/교육객체 revision과 hash
2. 학습자의 최초 답변
3. 답변 이유/확신도
4. 경쟁 오개념 또는 원인가설
5. 다음 질문의 목적코드
6. 정답 직접노출 여부
7. 힌트 단계
8. 학생의 재설명/수정
9. 개념회복 판정
10. 동형 또는 전이문항 결과
11. 지연복습 결과
12. 해당 판단을 뒷받침하는 evidence refs

원본 수업기록과 모델 훈련용 구조화 record는 분리한다. 훈련용 record는 비식별화·권리확인·출처무결성 검증을 통과해야 한다.

## 4. 데이터 적격성 상태

- `NOT_TRAINABLE`: 아직 학습용으로 평가하지 않은 상태
- `QUARANTINED`: 권리·동의·출처·무결성 중 하나 이상 미확인
- `ELIGIBLE`: 모든 필수 게이트 통과
- `REJECTED`: 계약/권리상 학습 금지

특히 다음은 기본 `REJECTED`:

- 관리형 제3자 AI API 출력
- ChatGPT App 요청/출력을 자체 경쟁모델 학습으로 우회 이용하는 경로

## 5. 저장계층

Google Drive 5TB는 원본·백업·dataset snapshot·checkpoint archive의 장기 저장소로 활용할 수 있다. 그러나 학습 중 반복 random read/write가 필요한 hot storage로 직접 사용하지 않는다.

권장 분리:

```text
Google Drive
  -> source archive / immutable backup / exported dataset snapshots

Railway/PostgreSQL
  -> metadata / hashes / state / receipts / model registry

Object or GPU-local storage
  -> hot training shards / checkpoints / evaluation cache
```

Google Drive 원본을 학습기로 가져올 때는 manifest와 hash를 고정하고, 학습 완료 후 생성된 checkpoint도 hash와 함께 보관한다.

## 6. 학습 파이프라인

```text
RAW / FIRST-PARTY DATA
    -> de-identification
    -> rights / consent check
    -> provenance + integrity check
    -> TRAINING_ELIGIBLE
    -> immutable dataset snapshot hash
    -> SFT / preference / reward training
    -> candidate checkpoint hash
    -> fixed evaluation snapshot
    -> regression + tutoring-quality gates
    -> PROMOTION_READY
    -> owner approval / atomic activation
```

학생 상호작용 직후 모델 가중치를 즉시 변경하지 않는다. 수업기록은 먼저 격리 저장하고, 검증된 batch만 학습에 편입한다.

## 7. 보상/평가 원칙

단순 정답률만으로 교수행동을 보상하지 않는다. 평가항목 후보는 다음을 포함한다.

- 정답을 조기에 누출하지 않았는지
- 오개념 가설을 실제 응답근거로 분리했는지
- 힌트 의존도가 줄었는지
- 학생이 핵심 추론노드를 스스로 설명했는지
- 동형문항에서 회복이 유지되는지
- 지연복습에서 유지되는지
- 다른 문제로 전이되는지

정확한 가중치·임계값·동점해소 규칙은 저장소 공개문서에 고정하지 않고 owner-controlled policy/영업비밀로 유지한다.

## 8. 모델 승격

후보모델은 평균점수 하나로 승격하지 않는다. Trust Chain과 동일하게 비보상적(non-compensatory) 게이트를 사용한다.

예:

- data-rights gate
- dataset-integrity gate
- held-out evaluation gate
- no-critical-regression gate
- Socratic non-disclosure gate
- reasoning/evidence gate
- privacy gate
- owner-control gate

필수 게이트 중 하나라도 실패하면 `HOLD`다.

## 9. IP/특허 경계

기반모델 명칭 자체를 발명의 필수구성으로 두지 않는다. 특허 후보의 중심은 다음 결속관계다.

1. 실제 교수 trajectory의 출처·revision·근거 결속
2. trajectory의 학습 적격성 전환
3. 산파식 교수행동을 지연숙달/전이와 결속한 보상 생성
4. 적격 dataset snapshot과 후보 checkpoint의 결속
5. 독립 평가 snapshot에 기초한 승격/보류/rollback
6. 결함 교육객체 또는 오염 데이터 발견 시 영향받은 학습증거·모델후보의 선택적 무효화

단순한 지속학습, 버전관리, rollback, 모델 registry 자체는 넓은 선행기술이 있으므로 독립적인 발명의 중심으로 주장하지 않는다.

## 10. 영업비밀 유지

아래는 코드/공개 명세서에 불필요하게 고정하지 않는다.

- 실제 보상 가중치
- 승격 임계값
- 오개념 분류 전체표
- 질문선택 우선순위
- 실제 프롬프트 전문
- 데이터 샘플링 비율
- 재학습 주기
- 모델별 성능/비용 라우팅식
- 내부 데이터셋 전체 목록

## 11. 첫 구현 단계

현재 브랜치에서는 다음 최소 안전골격만 도입한다.

- `app/training/models.py`
- `app/training/eligibility.py`
- `app/training/promotion.py`

실제 GPU 학습, Drive ingest, checkpoint 저장, 자동 승격은 후속 단계로 두며 검증 없이 production에 연결하지 않는다.

