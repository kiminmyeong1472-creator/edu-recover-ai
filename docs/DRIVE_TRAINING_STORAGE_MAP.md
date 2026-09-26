# EduRecover 5TB 학습 저장소 맵

기준일: 2026-09-26

Google Drive의 기존 `EduRecover_v3.24_PILOT_CONTROL_20260924` 폴더 아래에 별도 학습영역을 생성하였다. 기존 파일은 이동·삭제하지 않았다.

## Root

- `OWNED_AI_TRAINING_5TB`
- Drive folder id: `1IAWIBGFgD6WRjoZpJq9e8iHsMsGZaWRp`

## 하위 폴더

- `00_MANIFESTS` — 권리/출처/해시/적격성 manifest
- `01_SOURCE_ARCHIVE` — 원천자료 장기보관
- `02_TRAINING_ELIGIBLE` — 모든 학습 적격성 게이트 통과 자료
- `03_DATASET_SNAPSHOTS` — 고정 JSONL/Parquet 및 snapshot manifest
- `04_SOCRATIC_TRAJECTORIES` — 비식별화된 산파식 수업 trajectory
- `05_MODEL_CHECKPOINTS` — adapter/checkpoint 및 training receipt
- `06_EVAL_AND_PROMOTION` — held-out 평가·회귀검사·승격결정
- `07_QUARANTINE` — 권리/출처/개인정보/무결성 미확인 자료
- `08_BACKUP` — 확정 snapshot/checkpoint의 장기 백업

## 불변 규칙

1. `01_SOURCE_ARCHIVE`에 존재한다는 이유만으로 학습 가능하지 않다.
2. 학습기는 `02_TRAINING_ELIGIBLE` 또는 `03_DATASET_SNAPSHOTS`의 확정 자료만 읽는다.
3. 개인 학습자료는 비식별화와 필요한 권리/동의 확인 전 `07_QUARANTINE`을 벗어나지 않는다.
4. dataset snapshot은 content hash와 manifest를 결속한다.
5. checkpoint는 사용한 dataset snapshot hash와 recipe id를 training receipt에 기록한다.
6. 모델 승격은 `06_EVAL_AND_PROMOTION`의 필수 gate 전체 통과 후에만 가능하다.
7. Google Drive는 장기보관/교환계층이다. GPU 학습 중 hot random I/O는 GPU 로컬 또는 객체저장소를 사용한다.
