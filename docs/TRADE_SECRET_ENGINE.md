# EduRecover Trade-Secret Classification and Backup Engine

기준일: 2026-09-27

## 목적

이 모듈은 강화학습 창의 업로드자료·생성자료·채팅기록을 분류하고,
영업비밀·개인정보·격리대상·일반 내부자료의 저장경로와 외부전달 허용여부를
fail-closed 방식으로 결정한다.

구현 파일:

- `app/training/trade_secret_engine.py`
- `app/training/trade_secret_backup_runner.py`
- `tests/test_trade_secret_engine.py`
- `tests/test_trade_secret_backup_runner.py`

## 분류

- `PUBLIC_RELEASED`
- `INTERNAL_CONFIDENTIAL`
- `TRADE_SECRET`
- `PERSONAL_RESTRICTED`
- `QUARANTINED`

명시적인 공개승인이 없으면 공개자료로 분류하지 않는다.

특허 미출원자료, 상품설계, 소스코드, 내부 학습정책, owner가 영업비밀로 표시한
자료, 영업비밀 문맥이 포함된 채팅은 `TRADE_SECRET`으로 처리한다.

출처 또는 무결성이 확인되지 않으면 `QUARANTINED`으로 처리한다.

자격증명/비밀키가 포함된 내용은 일반 원문백업 경로로 보내지 않고
격리 메타데이터/포인터만 보존하도록 `raw_content_backup_allowed=False`로
결정한다.

## 저장 라우팅

영업비밀:

- `SERVER_PRIVATE`
- `DRIVE_TRADE_SECRET`

비식별되지 않은 개인정보:

- `SERVER_RESTRICTED_PERSONAL`
- `DRIVE_RESTRICTED_PERSONAL`

격리대상:

- `SERVER_QUARANTINE`
- `DRIVE_QUARANTINE`

일반 내부자료:

- `SERVER_PRIVATE`
- `DRIVE_GENERAL_PRIVATE`

실제 Google Drive folder ID, credential, 비밀키, 보상가중치 등은 코드에
하드코딩하지 않고 storage adapter 또는 환경설정에서 주입한다.

## 외부 AI 전달

영업비밀, 개인정보 제한자료, 격리자료 및 일반 내부자료는 기본적으로
외부 AI/provider 전달을 허용하지 않는다.

`explicit_public_release=True`가 명시된 공개자료만 기본 외부전달 허용상태가
될 수 있다. 실제 provider 호출은 별도의 privacy/ZDR/owner gate를 추가로 통과해야 한다.

## 30분 백업

`BackupPolicy.interval_minutes` 기본값은 30이다.

`build_backup_plan()`은 마지막 완료시각을 기준으로 현재 백업이 due인지 판정하고
서버/Drive 이중백업 대상과 저장형식을 반환한다.

채팅기록은 `AssetKind.CHAT_TRANSCRIPT`일 때 `.txt` 저장형식을 지정한다.

분류·라우팅·스케줄 판정은 `trade_secret_engine.py`가 담당하고,
`trade_secret_backup_runner.py`는 필요한 서버/Drive adapter를 실제 호출하여
백업 영수증을 검증한다. credential-bearing 자료는 raw-content 금지정책에 따라
adapter에 원문 참조를 전달하지 않는다.

실제 production 서버/Google Drive adapter의 자격증명 연결과 30분 scheduler 배포는
별도 운영연동이 필요하다. 그 실행증거가 없으면 "30분 자동백업 운영완료"라고
보고하지 않는다.

## 테스트

테스트는 다음을 확인한다.

- owner 표시 영업비밀의 이중 영업비밀 라우팅
- 미출원 특허자료의 영업비밀 분류
- 영업비밀 채팅의 TXT 백업형식
- credential 자료의 격리 및 원문백업 차단
- 출처 불완전 자료의 격리
- 비식별되지 않은 개인정보 제한
- 명시적 공개승인 없는 자료의 내부기밀 기본값
- 외부 provider 전달 fail-closed
- 30분 백업 due 판정
- 서버/Drive 이중대상 보장

