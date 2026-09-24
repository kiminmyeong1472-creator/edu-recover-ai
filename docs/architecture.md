# EduRecover AI v3.24.9 — System Architecture

## 1. 문서 목적

이 문서는 EduRecover AI v3.24.9의 핵심 시스템 구조와 무결성 방어 논리를 정리한다.

EduRecover AI는 단순한 생성형 AI 응용이 아니라, **생성 결과를 신뢰하기 전에 검증하고, 오염을 추적하고, 학습 오류를 교정하며, 최종 산출물의 활성화까지 증거로 통제하는 검증 우선(verification-first) 시스템**을 지향한다.

핵심 아키텍처는 다음 세 축으로 구성된다.

1. **Trust Chain** — 생성 결과의 신뢰·검증·승인·활성화 체계
2. **Vaccine Graph** — 오염·오류·의존관계 추적 및 회복 체계
3. **Socratic Trap** — 오답 원인을 구조화하고 개념 회복을 검증하는 학습 교정 체계

세 구조는 독립 기능이 아니라 하나의 폐쇄형 안전 루프를 이룬다.

```text
Candidate Generation
      |
      v
Trust Chain
  |   PASS / HOLD
  v
Active Revision
  |
  +------> Student-facing Output
  |
  +------> Vaccine Graph
              |
              +--> provenance / dependency tracing
              +--> quarantine / invalidation / recovery
              |
              v
        clean active revision
              |
              v
        Socratic Trap
              |
              +--> misconception diagnosis
              +--> guided contradiction
              +--> concept reconstruction
              +--> isomorphic verification
```

---

## 2. 설계 원칙

### 2.1 생성과 신뢰의 분리

모델이 산출물을 생성했다는 사실은 신뢰의 근거가 아니다.

외부 또는 내부 AI가 만든 모든 결과는 우선 `candidate`로 취급하며, 다음 단계의 검증을 거쳐야 한다.

- 출처 잠금(source lock)
- 정답 유일성
- 근거구간 일치
- 의미 등가성
- 유형 적합성
- 지문 길이/형식 규칙
- 오답 설계 규칙
- 범위 변경 여부
- 환각 여부
- 패키지 스키마 검사
- 독립 검증자 판정
- 활성화 승인

신뢰는 모델 정체성이나 다수결이 아니라 **증거 묶음(evidence bundle)** 에서 발생한다.

### 2.2 Fail-Closed

필수 검증이 하나라도 누락되면 기본 상태는 통과가 아니라 차단이다.

```text
missing receipt -> HOLD
conflicting answer -> HOLD
source hash mismatch -> HOLD
semantic contradiction -> HOLD
provenance gap -> HOLD
render gate failure -> RENDER_BLOCK
```

즉, “문제가 발견되지 않았다”와 “안전성이 증명되었다”를 구분한다.

### 2.3 비보상적 검증

여러 검증축 중 일부가 우수하다고 해서 다른 필수축의 실패를 상쇄할 수 없다.

예를 들어 문항이 자연스럽고 난도가 적절하더라도 정답 근거가 불일치하면 통과할 수 없다.

이를 **non-compensatory validation**으로 정의한다.

### 2.4 원본 보존

오류가 발견되더라도 원래 이벤트나 산출물을 삭제하여 흔적을 없애지 않는다.

대신 아래와 같은 후속 이벤트를 append한다.

- `QUARANTINE`
- `INVALIDATE`
- `SUPERSEDE`
- `RECOVER`
- `CLEAR`

이 원칙은 Vaccine Graph의 기초가 된다.

---

## 3. Trust Chain

## 3.1 목적

Trust Chain은 특정 교육 산출물이 “생성됨”에서 “활성화 가능한 신뢰 상태”로 이동하는 전 과정을 통제한다.

핵심 흐름은 다음과 같다.

```text
SOURCE_LOCKED
  -> BLIND_SOLVING
  -> GOLD_REVEALED
  -> ANSWER_GATE_PASSED
  -> SCHEMA_RECEIPT
  -> SEMANTIC_RECEIPT
  -> PACKAGE_MACHINE_PASS
  -> HUMAN_APPROVAL_PENDING
  -> RELEASE_READY
  -> ACTIVE
```

반대로 검증 실패 시:

```text
ANY REQUIRED FAILURE
  -> HOLD
  -> QUARANTINE or REVISION_REQUIRED
```

### 3.2 Source Lock

검증 대상은 반드시 고정된 원본에 결속되어야 한다.

예시 필드:

- `sourceItemId`
- `sourceItemHash`
- `sourceVersion`
- `sourceType`
- `createdAt`
- `generatorId`

검증 도중 원본이 바뀌면 기존 검증 결과는 재사용할 수 없다.

### 3.3 Blind Solving

검증자는 정답표(gold)를 보기 전에 독립적으로 문제를 해결한다.

목적은 정답표에 대한 후견편향을 줄이고, 다음을 독립적으로 확인하는 데 있다.

- 정답 후보
- 핵심 근거
- 오답 배제 이유
- 모호성
- 복수정답 가능성

### 3.4 Gold Reveal

Blind 결과가 봉인된 뒤 정답표를 공개한다.

이후 다음을 비교한다.

- blind answer vs gold answer
- blind rationale vs source evidence
- validator evidence vs source evidence
- source hash 일치 여부

### 3.5 다수결 배척

Trust Chain에서 “여러 AI가 같은 답을 냈다”는 사실은 충분조건이 아니다.

예를 들어 5개 모델 중 5개가 같은 잘못된 답을 내더라도, 근거구간이 틀리면 통과하지 않는다.

따라서 release condition은 단순 majority가 아니라 다음과 같은 논리곱 구조다.

```text
RELEASE =
  source_integrity
  AND answer_consistency
  AND evidence_alignment
  AND schema_validity
  AND semantic_validity
  AND policy_coverage
  AND provenance_complete
```

### 3.6 실행 영수증

각 핵심 검증 단계는 실행 영수증(receipt)을 남긴다.

권장 필드:

- tool / validator name
- tool version
- input hash
- output hash
- exit code
- verdict
- timestamp
- nonce
- receipt hash
- optional HMAC / signature metadata

이 영수증은 나중에 “무엇을 기준으로 통과시켰는가”를 재구성하는 감사 근거가 된다.

### 3.7 Sticky Negative Finding

한 번 확인된 부정적 판정은 단순 재실행으로 지워지지 않는다.

예:

```text
ANSWER_KEY_CONFLICT
PROVENANCE_GAP
SEMANTIC_CONTRADICTION
SOURCE_HASH_MISMATCH
UNVERIFIED_EXTERNAL_OUTPUT
```

해당 finding은 명시적 해결 이벤트와 새로운 검증 영수증이 있어야 해제된다.

---

## 4. Vaccine Graph

## 4.1 목적

Vaccine Graph는 오류나 오염이 발견되었을 때 “문제 파일 하나를 삭제”하는 방식이 아니라, 그 오류가 어떤 산출물에 영향을 주었는지 의존 그래프를 따라 추적한다.

노드 예:

- source
- prompt
- generated item
- answer key
- validator output
- rubric
- package
- export
- active revision

엣지 예:

- `DERIVED_FROM`
- `VALIDATED_BY`
- `USES`
- `TRANSFORMED_FROM`
- `REPLACED_BY`
- `ACTIVATED_AS`

### 4.2 실제 사용 관계

단순히 같은 세션에 있었다는 이유로 오염을 전파하지 않는다.

실제 사용 또는 실제 접촉을 증명하는 `actual_use_receipt` / `actual_contact_receipt`가 있는 경로를 중심으로 전파한다.

이 방식은 과도한 오염 판정을 줄이면서도 실제 영향 경로는 놓치지 않게 한다.

### 4.3 Quarantine

의심 노드가 발생하면 원본 삭제 대신 `QUARANTINE` 상태를 부여한다.

격리된 노드는 새 활성 산출물 생성에 사용될 수 없다.

### 4.4 Descendant Tracing

오염 노드의 모든 실제 의존 descendant를 추적한다.

```text
bad source
  -> generated item
      -> answer key
          -> worksheet package
              -> student export
```

이 경우 중간 노드만 고치는 것이 아니라 활성 산출물까지 영향 여부를 계산한다.

### 4.5 Counterfactual Recompute

오염 ancestor를 제거했을 때 결과가 어떻게 달라지는지 반사실 재계산한다.

이 과정은 단순 재생성이 아니라 “오염이 없었다면 같은 결과가 나왔는가”를 검증하기 위한 것이다.

### 4.6 Clean-Room Recovery

회복 결과는 기존 오염 경로를 재사용하지 않는 clean-room 경로에서 다시 계산한다.

새 결과는 별도의 revision으로 생성한다.

### 4.7 Recovery Clearance Receipt

회복 완료 조건은 “새 파일 생성 성공”이 아니다.

최종 active output까지 오염 경로가 0임을 증명해야 한다.

개념적으로:

```text
for every active_output:
    assert no_path(quarantined_node, active_output)
```

이를 증명하는 결과를 `recovery_clearance_receipt`로 봉인한다.

### 4.8 Reinfection Signature

성공적으로 복구된 사고 유형은 재감염 방지 규칙으로 변환할 수 있다.

예:

- 특정 잘못된 lineage 패턴
- 특정 provider output fingerprint
- 특정 policy gap
- 특정 hash mismatch pattern
- 특정 오답 설계 실패 패턴

이 시그니처는 다음 실행 이전에 차단 규칙으로 사용한다.

---

## 5. Socratic Trap

## 5.1 목적

Socratic Trap은 학생의 오답을 즉시 정답으로 교체하지 않고, 오답의 구조를 드러내어 학생 스스로 모순을 발견하게 만드는 교정 엔진이다.

핵심 흐름:

```text
Wrong Answer
  -> Error Operator
  -> Contradiction / Counterexample Prompt
  -> Safety & Scope Gate
  -> Required Logic Nodes
  -> Concept Reconstruction
  -> Isomorphic Verification Item
  -> Recovery PASS / RETRY
```

### 5.2 Error Operator

오답을 단순 문자열이 아니라 사고 오류 연산자로 분해한다.

예시:

- 조건 누락
- 인과관계 역전
- 범위 확장
- 부분-전체 혼동
- 극성 반전
- 개념 치환
- 근거 없는 상식 삽입
- 필요조건/충분조건 혼동
- 지문 외 추론

### 5.3 Trap Prompt

오류 유형에 맞춰 학생이 자신의 가정을 검토하게 하는 질문을 생성한다.

목표는 정답 암시가 아니라 오류 구조 노출이다.

예:

```text
학생 가정 A
  -> A가 맞다면 반드시 B여야 함
  -> 본문에서는 not-B가 확인됨
  -> 학생이 A의 모순을 스스로 탐지
```

### 5.4 필수 논리 노드

학생이 우연히 정답을 말했더라도 핵심 추론 노드를 통과하지 않았다면 회복 완료로 간주하지 않는다.

예:

```text
evidence identification
  -> relation interpretation
  -> distractor rejection
  -> target concept articulation
```

### 5.5 Isomorphic Verification

마지막에는 원문을 그대로 반복하지 않고 동일 개념 구조의 동형 문항으로 검증한다.

목적은 다음을 구분하는 것이다.

- 정답 기억
- 힌트 의존
- 실제 개념 회복

---

## 6. 세 아키텍처의 결합

세 구조는 다음과 같이 연결된다.

### 6.1 생성 단계

AI provider가 문제 후보를 생성한다.

모든 후보는 `UNTRUSTED_CANDIDATE` 상태로 시작한다.

### 6.2 Trust Chain 검증

정답, 근거, 유형, 의미, 정책, provenance를 독립 검증한다.

PASS 시에만 active revision 후보가 된다.

### 6.3 Vaccine Graph 기록

모든 입력·출력·검증 결과·승인 관계를 그래프에 기록한다.

향후 오류가 발견될 경우 영향 범위를 추적할 수 있다.

### 6.4 학생 상호작용

학생 오답 발생 시 Socratic Trap이 오류 연산자를 생성하고 교정 시퀀스를 수행한다.

### 6.5 재검증

교정용 질문과 동형 검증 문항 역시 Trust Chain의 생성물 검증을 통과해야 한다.

따라서 교정 엔진이 검증 체계를 우회할 수 없다.

### 6.6 사고 발생 시 회복

나중에 특정 validator, source, prompt, provider output이 잘못되었다고 판명되면 Vaccine Graph가 descendant를 추적하고 영향을 받은 active revision을 격리한다.

그 후 clean-room recompute와 Trust Chain 재검증을 거쳐 새 revision을 활성화한다.

---

## 7. 외부 AI Provider 경계

외부 모델은 generator 또는 auxiliary validator가 될 수 있으나 신뢰의 최종 권한을 갖지 않는다.

권장 adapter 계층:

```text
Provider Adapter
  -> Provider Guard
  -> Input Policy Check
  -> Output Normalization
  -> Candidate Registry
  -> Trust Chain
```

외부 AI 결과가 내부 포맷으로 변환되더라도 반드시 재검증한다.

다음은 금지된다.

- 외부 모델 응답을 그대로 active 상태로 저장
- 다수 모델 일치만으로 release
- source hash 미결속 상태의 검증
- provenance 누락 상태의 승인
- HOLD 상태 산출물의 student render

---

## 8. 활성 Revision과 Atomic Activation

최종 출력은 항상 명시적 `active revision`을 참조해야 한다.

새 revision은 다음 조건을 모두 충족하기 전 기존 revision을 대체하지 않는다.

- target key 일치
- source hash 일치
- version binding 일치
- 모든 필수 receipt 존재
- 독립 validator PASS
- approval 조건 충족
- atomic activation 성공

활성화는 가능한 한 단일 트랜잭션으로 수행한다.

실패 시 이전 정상 revision을 유지한다.

---

## 9. 데이터 및 저장 계층 권장 구조

초기 구현 권장 예시:

```text
app/
  api/
  domain/
  trust/
  vaccine/
  socratic/
  validators/
  providers/
  rendering/
  storage/

docs/
  architecture.md

tests/
  unit/
  integration/
  adversarial/

receipts/
  schema/
  semantic/
  recovery/

quarantine/
  metadata/
```

초기 단계에서는 SQLite를 사용할 수 있으나, 다음 원칙은 DB 종류와 무관하게 유지한다.

- append-oriented audit trail
- hash binding
- versioned revision
- explicit status transition
- referential integrity
- transaction-safe activation

---

## 10. 핵심 상태 모델

권장 상태:

```text
DRAFT
CANDIDATE
SOURCE_LOCKED
BLIND_SOLVING
VALIDATING
HOLD
QUARANTINED
RECOVERY_REQUIRED
HUMAN_APPROVAL_PENDING
RELEASE_READY
ACTIVE
SUPERSEDED
INVALIDATED
```

상태 전이는 임의 문자열 변경이 아니라 정책에 의해 제한한다.

---

## 11. 보안 및 무결성 불변식

### Invariant A — Active output must be validated

```text
ACTIVE(x) -> has_complete_validation_receipts(x)
```

### Invariant B — No active output may depend on quarantined ancestry

```text
ACTIVE(x) -> not exists q:
  QUARANTINED(q) and path(q, x)
```

### Invariant C — Source identity must not drift

```text
validation.source_hash == active_revision.source_hash
```

### Invariant D — Majority cannot override evidence failure

```text
evidence_failure == true -> HOLD
```

### Invariant E — Recovery creates a new revision

```text
invalidated_revision != recovered_revision
```

### Invariant F — Student rendering uses active package only

```text
render(student) -> package.status == ACTIVE
```

---

## 12. 감사 가능성

모든 중요한 판정은 사후 설명 가능해야 한다.

최소 감사 질문:

1. 이 산출물의 원본은 무엇인가?
2. 어떤 모델 또는 도구가 생성했는가?
3. 어떤 validator가 검증했는가?
4. 각 validator는 어떤 입력 hash를 보았는가?
5. 정답과 근거는 일치했는가?
6. 어떤 negative finding이 있었는가?
7. 누가 또는 무엇이 release를 승인했는가?
8. active revision은 언제 바뀌었는가?
9. 과거에 quarantine된 ancestor가 있는가?
10. recovery clearance가 증명되었는가?

이 질문에 영수증과 그래프만으로 답할 수 있어야 한다.

---

## 13. 테스트 전략

### 13.1 Unit Test

- hash binding
- state transition
- receipt validation
- error operator classification
- graph path detection

### 13.2 Integration Test

- candidate -> trust -> release
- provider -> adapter -> validation
- quarantine -> recovery -> activation
- wrong answer -> Socratic Trap -> verification

### 13.3 Adversarial Test

다음 공격 또는 장애를 의도적으로 주입한다.

- 동일 정답 다수결이지만 잘못된 근거
- 정답키 변경
- source hash drift
- receipt 누락
- 외부 provider의 형식 정상/의미 오류
- quarantine ancestor를 우회한 재활성화
- student renderer의 HOLD 패키지 접근
- Socratic Trap의 정답 직접 노출

시스템은 이러한 경우 release가 아니라 HOLD 또는 BLOCK을 선택해야 한다.

---

## 14. 구현 우선순위

v3.24.9 초기 저장소에서는 다음 순서를 권장한다.

1. domain model / status machine
2. evidence receipt schema
3. Trust Chain orchestrator
4. validator interface
5. Vaccine Graph event model
6. quarantine / recovery engine
7. Socratic Trap error operator model
8. provider adapters
9. student render gate
10. adversarial test suite

---

## 15. 결론

EduRecover AI의 핵심은 더 많은 AI를 붙이는 것이 아니라, **AI가 만든 결과를 어떤 증거와 절차로 믿을 것인가**에 있다.

- Trust Chain은 신뢰의 성립 조건을 통제한다.
- Vaccine Graph는 신뢰가 깨졌을 때 영향 범위와 회복을 통제한다.
- Socratic Trap은 학생의 잘못된 추론을 개념 회복으로 전환한다.

세 구조를 하나의 체계로 결합함으로써 생성, 검증, 활성화, 사고 대응, 학습 회복을 동일한 무결성 원칙 아래 관리한다.
