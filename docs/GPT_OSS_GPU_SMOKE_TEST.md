# gpt-oss-20b 1차 GPU Smoke Test

기준일: 2026-09-26

## 목적

이 단계는 EduRecover 교수능력을 학습시키는 본 실험이 아니다. 먼저 학습 파이프라인이 실제 GPU에서 다음을 완주하는지만 검증한다.

1. 라이선스가 명확한 데이터 획득
2. 고정 revision
3. 소규모 dataset snapshot 생성
4. SHA-256 검증
5. gpt-oss-20b 로드
6. LoRA SFT
7. checkpoint 저장
8. training receipt 생성
9. 외부 Hub 자동 업로드 없음

## 공개 Smoke Dataset

- dataset: `HuggingFaceH4/Multilingual-Thinking`
- pinned revision: `d57770a279da10d30be4845451a499aef8caf5b9`
- license: Apache-2.0
- 기본 샘플 수: 64
- hidden reasoning/thinking 필드: 제외
- 용도: `PIPELINE_SMOKE_TEST_ONLY`

OpenAI Cookbook에서 해당 데이터셋을 gpt-oss fine-tuning 예제로 사용한다.

## GPU 기준

OpenAI 공식 Cookbook 노트북은 **단일 H100 80GB GPU** 기준으로 작성되어 있다. 더 작은 GPU에서는 batch size와 sequence length를 줄일 수 있다고 안내한다.

EduRecover 첫 실행은 재현성 우선으로 H100 80GB 급을 기준 환경으로 잡는다. 비용 최적화는 smoke test가 성공한 후 별도 수행한다.

## 실행 순서

```bash
python -m pip install torch --index-url https://download.pytorch.org/whl/cu128
python -m pip install -r requirements-training.txt

python scripts/prepare_public_smoke_dataset.py \
  --output-dir ./datasets/local/smoke-v0.1 \
  --count 64 \
  --seed 20260926

python scripts/train_gpt_oss_sft.py \
  --dataset ./datasets/local/smoke-v0.1/smoke_public_v0_1.jsonl \
  --manifest ./datasets/local/smoke-v0.1/smoke_public_v0_1.manifest.json \
  --recipe ./training/recipes/gpt_oss_20b_openai_baseline.example.json \
  --output-dir ./training_runs/gpt-oss-20b-smoke-v0.1
```

## PASS 조건

- manifest hash check PASS
- blocked source class 0
- personal data 0
- model load 성공
- training step 완료
- adapter/checkpoint 저장
- `edurecover_training_receipt.json` 생성
- public Hub push 0
- dataset/checkpoint Git commit 0

## 실제 산파식 데이터 전환 조건

강원도 최상위수학 대표 실시례 관련 현재 보존 증거는 실제 문제와 일부 교수맥락까지만 확인된다. 실제 학습자 답변 및 턴별 교수대화가 불완전하므로 현재 상태에서는 SFT target으로 사용하지 않는다.

완전한 인간 원자료가 확보되면:
- 비식별화
- 권리/동의 확인
- source hash 고정
- 실제 turn reconstruction
- early-answer-reveal 검사
- TRAINING_ELIGIBLE 승격

후 별도 proprietary snapshot v0.1을 만든다.

