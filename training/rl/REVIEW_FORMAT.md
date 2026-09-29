# 내부 전용 — 다음 질문 선택용 검수 형식

이 문서는 스키마 설명이다. 예시 수업이나 실제 학생 반응을 만들지 않는다. `tests/test_rl_contract.py`는 단위시험용이며 학습자료가 아니다.

`records.jsonl`의 각 행은 다음 필드를 갖는 JSON object다.

| 필드 | 의미 |
|---|---|
| record_id | 고유한 검수 상태 ID |
| group_id | 원수업·학생·유사문항 계보 그룹. train/eval 혼합 금지 |
| target_concept_id | 사전 고정 목표개념 |
| concept_plan_revision | 오개념가설·사전 질문군을 결속한 계획 개정본 |
| dialogue | role=user/assistant, content=실제 검수 내용인 배열 |
| source_refs | 원자료 및 검수 기록 참조 목록 |
| review_id / review_status | 검수증빙 ID / HUMAN_APPROVED |
| reward_basis | REVIEWED_NEXT_QUESTION_QUALITY |
| content_status | VALID |
| split | train 또는 eval |
| actions | 아래 질문 객체 2~8개 |

각 actions 항목: `action_id`, `question`, `target_concept_id`, `content_revision`, `content_status=VALID`, `answer_disclosed=false`, `reviewed_score`(-1~1 유한수), `score_rationale`.

점수는 **해당 입력 상태에서 해당 다음 질문의 적절성에 대한 교사 검수**다. 최소 2개의 서로 다른 점수가 있어야 한다. 질문 자체의 오류나 개인정보 결함은 낮은 점수로 처리하지 않고 원자료를 수정·재검수한다. 표면형이나 맥락을 바꾸어도 목표개념은 유지한다. 점수·검수 근거는 모델 입력에서 제외된다.

`review.json` 필수 필드: `training_status=ELIGIBLE`, `review_id`, `reviewed_dataset_sha256`, `allowed_use_scopes=["TUTOR_POLICY"]`, `rights_state=CONFIRMED`, `consent_state=CONFIRMED 또는 NOT_REQUIRED`, `source_classes`, `provenance_complete=true`, `contains_personal_data`.

개인정보가 있는 경우 `deidentified=true`, `consent_state=CONFIRMED`를 요구한다. FIRST_PARTY_TUTORING에는 기존 `patent_clearance=CLEARED_FOR_TUTOR_MODEL_TRAINING` 검토 게이트가 추가로 필요하다. 직접 작성 자료 역시 실제 출처와 권리가 확인되어야 한다. API 출력·ChatGPT 요청·출처불명 자료로 표시된 자료는 이 실행기가 받지 않는다.

`revocations.json`: `schema=edurecover.revocations.v1`, 실제 `review_id`, `record_ids` 배열, `content_revisions` 배열. 해당되는 철회가 없다는 실제 검토를 마친 경우에만 빈 배열을 쓴다. GPU에 전달한 파일은 권한 있는 운영자가 최신 상태로 갱신해야 한다. 운영 DB와 자동으로 동기화되는 기능은 아직 없다.

```bash
python scripts/prepare_rl_bundle.py \
  --records /private/reviewed-records.jsonl \
  --review /private/review.json \
  --revocations /private/revocations.json \
  --output-dir /private/approved-bundle
```

`reviewed_dataset_sha256`는 교사가 승인한 **정확한 파일**의 SHA-256이다. 봉인 도구는 파일 해시와 검수를 확인할 뿐, 교사를 대신하여 점수나 학습 승인을 만들지 않는다. 생성되는 `snapshot_hash`는 자신을 제외한 manifest를 UTF-8 정규 JSON(sort_keys=true, separators=(',', ':'), ensure_ascii=false)으로 직렬화한 SHA-256이다.
