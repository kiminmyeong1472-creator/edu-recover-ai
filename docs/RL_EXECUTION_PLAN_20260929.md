# 영업비밀 — EduRecover 강화학습 실행계획

작성·확인: 2026-09-29. 상품 개발용 내부 문서. 특허 명세서로 자동 편입하지 않는다.

## 확정 상품 운영 범위 — 2026-09-29 18:00 KST

사용자 결정: 소유자가 지정한 내부 학습자는 GPU 학습을 우선 진행하고, 무료 서비스는 학습자료 누적, 향후 유료 서비스는 GPU 학습 기능으로 구분한다. **유료 서비스는 현재 개설하지 않는다.**

| 구분 | 목표 기능 | 현재 단계 |
|---|---|---|
| 소유자 지정 내부 학습자 | 지속 축적 및 GPU 추가학습의 우선 대상 | 유료 미개설과 독립적으로 첫 실험을 준비 |
| 무료 서비스 | 수업 기록·오답·교수행동·성과 자료 누적 | 무료 이용만으로 개인별 GPU 작업을 자동 생성하지 않는 상품 기준 |
| 향후 유료 서비스 | GPU 학습 기능 제공 | 미개설; 이번 변경으로 요금제·가입·결제·상용 학습을 시작하지 않음 |

이 구분은 확정한 상품 방향이다. 내부 학습자를 유료 가입 조건으로 막지 않도록 향후 실행 권한을 구분한다. 가격, GPU 사용량, 개인별 adapter 또는 공통 모델 구조는 아직 결정하지 않았다. 모델 재학습과 일상적인 추론·튜터링 호출도 구분한다.

원자료 축적은 계속 진행한다. GPU 훈련 묶음에는 출처·목적·검수 조건을 통과한 입력을 넣고 학습용과 평가용을 분리한다. 무료 서비스의 누적 자료가 다른 목적의 모델 학습에 자동 전용된다고 가정하지 않는다. 원문 사진·학생 식별정보·실제 대화 전문은 저장소에 넣지 않는다.

## 수업 1회당 자동 누적 로직 — 2026-09-29 20:32 KST

수업 중에는 학생 응답을 저장하고 보상·학습자격을 갱신한다. 교사가 수업 종료(`STOP`)를 누르면 해당 수업에 오프라인 학습 묶음을 정확히 하나 만든다. 종료를 다시 눌러도 같은 묶음을 갱신하므로 GPU 작업이 중복 생성되지 않는다.

| 상태 | 의미 | 자동 동작 |
|---|---|---|
| `EMPTY` | 저장된 학습증거가 없음 | 묶음만 보존하고 대기 |
| `REVIEW_PENDING` | 응답은 있으나 검수·출처·독립평가 조건이 부족함 | 검수 결과가 들어올 때마다 같은 묶음을 갱신 |
| `READY_FOR_EXPORT` | 현재 수업에 GPU 입력 후보로 내보낼 수 있는 적격 기록이 하나 이상 있음 | 적격 revision과 묶음 해시를 저장 |

`READY_FOR_EXPORT`는 GPU 임대나 모델 훈련을 자동 시작하지 않는다. 수업 중 생각하는 시간에 GPU 요금이 발생하지 않도록 자료를 서버에 누적하고, GPU 실행은 준비된 묶음을 모아 별도 작업으로 수행한다. 서버 API `/tutor/api/training-batches`에서 소유자만 묶음 상태를 조회할 수 있다.

## 확정 수학 교수 기준

학생이 문제의 한국어 뜻을 자기 말로 설명하고, 주어진 정보와 구할 것, 문장·그림·수치의 의미, 조건 사이 관계를 찾은 뒤 **직접 식을 세우도록** 질문한다. 답·공식·식을 먼저 제시하지 않는 것이 기본이다. 부모의 명시적인 설명·힌트 요청은 해당 범위의 예외로 기록한다.

AI가 식을 제시한 뒤 학생이 계산한 것, 선택형 질문에 답한 것, 학생이 관계를 설명하며 식을 직접 만든 것을 평가에서 구분한다. 학생의 “어려워”만으로 전체 식 선제공을 자동 허용하거나, 과거 대화 전체를 이상적인 산파식 시범으로 채택하지 않는다. 이 수학 교수 기준을 자동출제 전반의 인간승인 의무로 확대하지 않는다.

## 현재 확인된 상태

- 운영 서비스의 영속저장·영업비밀 구간 제공·교수증거 기록 변경은 main `d4c432c9a57d0cfceae338be48cd6d9ca6650873`에 배포됐다.
- 기존 `train_gpt_oss_sft.py`는 지도학습(SFT)+LoRA 실행기였다. 강화학습을 실행했다는 뜻이 아니다.
- 이번 추가분은 GRPO+LoRA 방식의 **다음 교수질문 선택 실험**이다. GPU 실행 전 검토본이다.
- 현재 작업환경에는 NVIDIA GPU와 학습 라이브러리가 없다. GPU 임대 계정·결제·작업 연결도 확인되지 않았다.
- 확인한 Drive의 TRAINING_ELIGIBLE, DATASET_SNAPSHOTS, SOCRATIC_TRAJECTORIES, MANIFESTS 폴더는 비어 있었다. 운영 DB 전체가 비었다는 뜻은 아니다.
- 최초 수학 실시예 메타데이터와 별도로, 사용자가 제공한 실제 문답·사진에서 4개 문항 사례의 검토본을 작성했다. 13·14번은 도움을 받은 최종 답까지 확인됐고, 15번은 부분 문답과 교수방식 수정 지시를 확인했다. 원문은 별도 비공개 자료로 보존한다. 검토본 작성은 훈련 적격 입력 봉인이나 GPU 실행 완료가 아니다. 무도움·지연 전이 성과를 만들어 채우지 않는다.
- 실제 가중치 갱신, 실제 학습성과, GPU 호환성 검증은 아직 완료되지 않았다.
- 수업 종료별 학습 묶음 생성과 검수 후 상태 갱신 로직은 구현됐고 전체 회귀시험 173개 및 하위시험 21개가 통과했다.

## 첫 실험의 정확한 범위

하나의 목표개념과 검수된 수업상태를 입력으로 주고, 그 개념에 사전 결속된 2~8개의 질문 중 다음 질문 ID를 선택하도록 학습한다. 학생용 객관식 선지를 생성하거나 오답 매력도를 높이는 학습이 아니다.

각 질문의 보상은 **그 상태에서 그 질문이 적절한지 교사가 검수한 점수**다. AI가 생성한 선택에 따라 실제 정책을 GRPO로 갱신한다. 과거 학생이 다른 질문에 응답하여 성공했다는 사실을 새 질문의 효과로 붙이지 않는다. 이 첫 단계의 점수 상승은 학생의 장기 파지·전이 효과를 입증하지 않는다.

검수된 SFT 대화가 있으면 기존 SFT 학습을 별도 선행 실험으로 수행할 수 있다. 이번 GRPO 실행기는 원본 모델에 새 attention LoRA를 붙이는 작은 기준 실험이며, 기존 SFT adapter를 이어받는 기능은 아직 포함하지 않는다. 자유로운 질문 생성과 실제 다중 턴 수업의 최종 전이 보상은 다음 단계다.

## 장애물과 해결 방법

| 장애물 | 지금 준비한 해결 방법 | 남은 실제 조건 |
|---|---|---|
| SFT만 있고 RL 실행부 없음 | `scripts/train_gpt_oss_grpo.py` 추가 | GPU에서 실제 스모크 실행 필요 |
| 저장된 증거 메타데이터를 곧바로 학습데이터로 오인 | 실제 대화/상태·개념·질문·교사 점수·근거를 필수로 하는 별도 스키마 | 교사가 검수한 원자료 |
| 학습 데이터 부족 | 우선 좁은 개념군의 검수 상태 20~50건을 운영상 목표로 준비하고 원수업 그룹을 나눠 평가용을 따로 보존 | 이 수치는 출발 규모 제안이며 성능 보장이나 법정 최소치가 아님 |
| 검수 없는 자료, 외부 API 출력 유입 | 출처·권리·동의·용도·원자료 해시·검수 ID 검사 | 실제 권리/동의/교사 검수 증빙 |
| 결함 자료가 0점으로 학습에 계속 참여 | 결함/철회 자료는 실행 전 제외, 실행 중 발견하면 중단 | 최신 철회 목록을 GPU에 전달하는 운영 절차 |
| 뒤늦게 발견한 결함 | 실행 영수증의 snapshot/model 해시로 영향 식별 후 이전 깨끗한 adapter로 복귀 또는 재학습 | 파일 삭제/보상 취소만으로 이미 학습된 가중치가 지워지지는 않음 |
| GPU·메모리 없음 | H100 80GB 1대, BF16 원본 + 작은 LoRA, 그룹 2개, 짧은 문맥으로 첫 시험 | 사용자 GPU 계정 및 임대 |
| OOM/호환성 | 실패 시 HOLD, 배치·생성길이 감소 후 새 실행; 라이브러리 후보 버전 고정 | 80GB가 모든 설정에서 충분하다는 보장은 아님 |
| 비용 지속 발생 | 최대 4시간 작업 타이머, Runpod Stop 명령, 콘솔 확인 | 계정 청구 상한을 보장하는 기능은 아님; 중지 실패·설치시간은 별도 관리 |
| 무료 Gemini API 한도 0 | 자체 GPU의 gpt-oss 학습은 Gemini API를 호출하지 않음 | 자동출제 API 복구는 별도 운영 과제 |
| Google Drive 인증 미완료 | 첫 실험은 승인된 파일만 GPU로 복사하고 결과를 별도 암호화하여 백업 가능 | 자동 Drive 백업에는 소유자 OAuth 동의 필요 |
| 영업비밀 유출 | 해당 실험 자료만 반입, private 저장, 외부 추적·Hub 업로드 비활성화 | GPU 계정 접근권한·저장 암호화·백업복원 확인 |
| 모델 개선 여부 미확인 | 학습 전/후 같은 미사용 평가자료 비교, 파라미터 해시·업데이트 수·보상 변동 기록 | 학생 전이/지연검증 및 기존 교수능력 회귀평가 |
| 자동 서비스 승격 위험 | 모든 출력은 후보 상태, 자동 배포/승격 없음 | 독립 평가와 기존 승격 게이트 |
| 특허 검토 미완료 | 기존 실제 수업자료 학습 검토 게이트 유지 | 코드 체크박스는 법률상 비침해 판단을 대신하지 않음 |

## GPU 선택과 비용

Runpod 공식 가격 페이지 확인값:

| 1 GPU | 시간당 GPU 표시가 | 4시간 GPU 계산액 |
|---|---:|---:|
| H100 PCIe 80GB | $2.89 | $11.56 |
| H100 SXM 80GB | $3.49 | $13.96 |
| A100 PCIe/SXM 80GB | $1.59 | $6.36 |

첫 후보: H100 80GB 1대, GPU 작업 최대 4시간. 첫 실험 예산 제안은 **총 $20 이내를 목표**로 하되 실제 콘솔 견적·설치시간·저장공간·세금을 확인한 뒤 확정한다. 이는 구매 승인이나 강제 청구 상한이 아니다. A100은 재고·비용 대안이며, H100과 동일한 시간 내 완료를 보장하지 않는다.

OpenAI 공식 SFT 예제는 H100 80GB 1대에서 수행되도록 작성됐다. 그 예제는 GRPO 벤치마크가 아니므로 이 실험의 실행시간이나 메모리를 보증하지 않는다. 16GB는 gpt-oss-20b의 일부 추론 구성을 설명하는 수치이며 본 학습 사양으로 쓰지 않는다.

저장: 모델 캐시·실험 산출물을 위해 약 100GB 이상의 `/workspace` 여유 공간부터 확인한다. 대규모 원본모델 체크포인트를 매번 저장하지 않고 adapter와 실행 기록을 보존한다. Runpod volume disk는 실행 중 $0.10/GB/월, 중지 중 $0.20/GB/월 표시 기준이며, 기간 비례 비용은 실제 청구화면으로 확인한다. Stop은 GPU를 해제해도 저장비가 남는다. Terminate는 volume disk를 삭제하므로 검증된 백업 전에는 실행하지 않는다.

## 실행 순서

1. GPU 임대 전에 원자료와 교사 검수표를 준비한다. 테스트 fixture를 교육자료로 사용하지 않는다.
2. `training/rl/REVIEW_FORMAT.md`에 따라 기록과 검수 메타데이터를 만든다. 원수업/학생/유사문항 계보 그룹은 train/eval 양쪽에 섞지 않는다. 단순 텍스트 중복검사는 의미상 유사문항 누출까지 탐지하지 못하므로 사람이 확인한다.
3. `prepare_rl_bundle.py`로 해시가 일치하는 승인본만 봉인한다. 이 도구는 새로운 승인을 만들어내지 않는다.
4. 준비된 봉인본을 CPU에서 `--check-only`로 통과시킨 뒤 GPU를 임대한다.
5. GPU의 CUDA/PyTorch 환경과 `requirements-rl.txt`를 설치·확인하고 모델 commit을 고정한다.
6. `run_bounded_rl.py --stop-runpod`로 실행한다. Runpod CLI 인증이 필요하다. 비밀키를 소스코드·채팅·로그에 적지 않는다.
7. 후보 adapter와 영수증을 내려받고 해시/복원 확인 후 필요 없는 Pod·저장공간을 정리한다.
8. 실제 학생 효과와 교수능력 회귀평가는 별도 통과 후 승격한다.

### GPU 작업 명령 예시

비공개 저장소의 이 브랜치를 GPU의 `/workspace/edu-recover-ai`에 받아 둔 상태에서 사용한다. 준비되지 않은 자리표시자 값을 임의로 채워 실행하지 않는다.

```bash
python -m venv /workspace/rl-venv
source /workspace/rl-venv/bin/activate
python -m pip install torch==2.8.0 --index-url https://download.pytorch.org/whl/cu128
python -m pip install -r requirements-rl.txt
python -m pip freeze > /workspace/rl-runtime-lock.txt
```

모델의 현재 commit을 한 번 확인하여 정확한 SHA로 고정한다. 공개 가중치 다운로드에 필요하지 않은 계정 권한을 추가하지 않는다.

```bash
python -c 'from huggingface_hub import model_info; print(model_info("openai/gpt-oss-20b").sha)'
```

```bash
python scripts/train_gpt_oss_grpo.py \
  --dataset /workspace/approved-bundle/records.jsonl \
  --manifest /workspace/approved-bundle/manifest.json \
  --revocations /workspace/approved-bundle/revocations.json \
  --recipe training/recipes/gpt_oss_20b_grpo_pilot.json \
  --model-revision EXACT_MODEL_COMMIT_SHA \
  --output-dir /workspace/training_runs/pilot-001 --check-only
```

```bash
python scripts/run_bounded_rl.py --seconds 14400 --stop-runpod -- \
  --dataset /workspace/approved-bundle/records.jsonl \
  --manifest /workspace/approved-bundle/manifest.json \
  --revocations /workspace/approved-bundle/revocations.json \
  --recipe training/recipes/gpt_oss_20b_grpo_pilot.json \
  --model-revision EXACT_MODEL_COMMIT_SHA \
  --output-dir /workspace/training_runs/pilot-001
```

타이머는 런처 실행 시점부터의 제한이며 Pod를 만든 시점부터의 총 과금시간과 다르다. 설치·모델 다운로드도 시간을 사용한다. 중단 명령 실패 시 콘솔에서 직접 Stop해야 한다. GPU OOM이 나면 임의로 모델 공급자를 바꾸지 않고 원인을 확인한 뒤 새 실행으로 재시도한다.

## 시작·완료 판정

- `--check-only` 통과: 검증만 완료. 학습 시작 아님.
- `RUNNING`: 학습 루프 호출 시작. 완료 아님.
- `HOLD`: 입력·GPU·의존성·시간·철회·가중치 검사 실패. 결과 승격 금지.
- `TRAINED_REQUIRES_INDEPENDENT_REVIEW`: optimizer step > 0, 실제 adapter 해시 변경, 그룹 내 보상 차이 확인, 평가기록 및 adapter 저장. 학생 효과나 운영승격 완료 아님.
- 영수증은 서명된 원격 증명서가 아니라 해당 실행기의 감사기록이다. GPU 콘솔 로그 및 파일 해시와 함께 확인한다.

## 사용자님이 직접 필요한 일

1. GPU 임대 계정의 로그인·결제수단 연결 및 첫 예산 확정. 현재 연결된 임대 계정이 없어 이 부분을 대신 확정할 수 없다.
2. 제공된 수업 문답·사진은 이미 검토본으로 정리했다. 같은 자료를 다시 입력할 필요는 없다. 다음 준비는 출처별 입력 선별, 교수 질문 후보와 근거 점수, 학습용·평가용 분리다. 실제로 없는 전이 결과·교사 검수·자료 이용 동의를 만들어 승인하지 않는다.
3. 자동 Google Drive 백업을 사용할 경우 소유자 OAuth 동의. 수동 암호화 백업으로 첫 학습을 진행하면 이 항목이 선행 장애물일 필요는 없다.

바로가기: [Runpod Pods](https://console.runpod.io/pods) · [Runpod 가격](https://www.runpod.io/pricing) · [EduRecover 영업비밀 관리](https://edurecover-prod-production.up.railway.app/owner/secrets)

## 확인 자료

- [OpenAI gpt-oss SFT 예제](https://developers.openai.com/cookbook/articles/gpt-oss/fine-tune-transfomers)
- [TRL GRPO 문서](https://huggingface.co/docs/trl/grpo_trainer)
- [TRL 0.24 GRPO 구현](https://github.com/huggingface/trl/blob/v0.24.0/trl/trainer/grpo_trainer.py)
- [Runpod 공식 가격](https://www.runpod.io/pricing)
- [Runpod Pod 중지·종료](https://docs.runpod.io/pods/manage-pods)
- [Runpod 저장소 과금·보존](https://docs.runpod.io/pods/storage/types)

현재 검증은 CPU 단위·회귀 테스트다. 실제 CUDA 실행, 공식 예제와 다른 GRPO 조합의 메모리 적합성, 첫 교육 데이터 검수는 남아 있다. 의료특허 후속 작업은 사용자 지시에 따라 이 작업 뒤에 재개한다.
