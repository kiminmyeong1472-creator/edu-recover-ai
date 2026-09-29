# 암호화 백업 실행기·저장소 연결 (2026-09-28)

## 구현과 운영 상태를 구분

실행 코드는 기존 `app/services/encrypted_backup.py`의 EDURBK v1/v2 포맷과
소유자 암호/기존 키 우선순위를 그대로 사용한다. 별도 암호화 포맷·새 마스터키·키 교체를
도입하지 않았다. 서버 저장, Drive 업로드, 검증, 영수증, 30분 실행 판단을 연결했다.

코드 추가만으로 운영 백업이 설치되거나 데이터가 복구 가능해진 것은 아니다.
2026-09-28 읽기 전용 점검 당시 `edurecover-prod`에는 Railway volume이 없었고
백업 manifest·저장경로·Drive 서비스계정 설정 이름도 확인되지 않았다.
기존 `EDURECOVER_BACKUP_*` 암호화 변수 이름의 존재는 백업 성공 근거가 아니다.
실제 설정을 입력하거나 클라우드 파일을 업로드한 변경은 이 구현 작업에 포함하지 않았다.

## 파일과 호출점

- `app/training/backup_adapters.py`: 암호문 공동 생성, 서버 원자 저장·읽기 검증,
  명시적 서비스계정 Drive Shared Drive 업로드·읽기 검증.
- `app/training/trade_secret_backup_runner.py`: 모든 필수 대상 사전 점검,
  부분 성공을 보존하는 실패 영수증.
- `app/training/backup_service.py`: 등록된 파일만 snapshot, 자산별 30분 간격,
  내용/분류/대상 변경 시 즉시 다시 실행, SQLite 온라인 backup, 배타 잠금, 영수증.
- `get_backup_service_status()`: 비밀값·파일 내용·네트워크 요청 없는 구성 상태.
- `run_configured_backup_once()`: 동기식 한 회 실행. 웹에서 연결할 때 owner 인증과
  threadpool을 적용해야 한다. 공개 health나 공개 학생 경로에 연결하지 않는다.

## 필수 구성

| 환경변수 | 의미 |
|---|---|
| `EDURECOVER_BACKUP_ENABLED` | 명시적 `true`일 때만 실행 |
| `EDURECOVER_BACKUP_MANIFEST` | 아래 형식의 owner 관리 JSON 파일 |
| `EDURECOVER_BACKUP_SOURCE_ROOT` | 승인된 원본/내보내기 루트 |
| `EDURECOVER_BACKUP_SERVER_ROOT` | 암호문 저장소. 사전에 존재하는 권한 0700 디렉터리 |
| `EDURECOVER_BACKUP_STATE_DIR` | 영수증/잠금/임시처리 디렉터리. 사전에 존재하는 권한 0700 디렉터리 |
| `EDURECOVER_BACKUP_DRIVE_SERVICE_ACCOUNT_FILE` 또는 `EDURECOVER_BACKUP_DRIVE_SERVICE_ACCOUNT_JSON` | 정확히 하나의 명시적 서비스계정 자격증명. 파일은 권한 0600, secret mount 사용 |
| `EDURECOVER_BACKUP_DRIVE_FOLDERS_JSON` | 분류별 승인된 Shared Drive 폴더 ID 매핑 |
| `RAILWAY_VOLUME_MOUNT_PATH` | Railway가 제공한 실제 영속 볼륨 mount. production에서는 source/server/state 모두 해당 mount 내부여야 함 |

기존 소유자 백업 암호 또는 legacy master key가 유효해야 한다. 키를 채팅·Git·manifest에 넣지 않는다.
API 키·ChatGPT Drive connector·default application credentials는 대신 사용하지 않는다.
다른 Secret Store를 조회하거나 토큰을 새로 발급하는 관리 기능을 만들지 않았다.

Drive 폴더 키는 정책에 따라 `DRIVE_GENERAL_PRIVATE`, `DRIVE_TRADE_SECRET`,
`DRIVE_RESTRICTED_PERSONAL`, `DRIVE_QUARANTINE`이다. 실제 자산에 필요한 키가
누락되면 서버 쓰기도 시작하지 않는다. 폴더를 자동 생성하거나 공유 범위를 변경하지 않는다.
폴더 접근권한 조회 결과 `anyone` 또는 `domain`이 있으면 거부한다.
승인된 폴더의 user/group 수신자는 소유자가 별도로 관리해야 한다.

Google 공식 문서상 서비스계정은 개인 파일 소유권/스토리지 할당량이 없어
**Shared Drive 업로드가 필요**하다. 개인 My Drive 폴더는 이 어댑터에서 거부한다.
개인 My Drive를 운영 대상으로 쓰려면 별도 승인된 사용자 OAuth 구현이 필요하며,
이 구현이 그 승인·토큰을 이미 보유한다고 가정하지 않는다.

## 등록 manifest (형식 예시, 실제 운영 등록이 아님)

```json
[
  {
    "asset_id": "approved-learning-export",
    "source": "exports/learning.sqlite3",
    "kind": "TRAINING_RECORD",
    "provenance_complete": true,
    "contains_personal_data": true,
    "deidentified": false,
    "sqlite_snapshot": true
  },
  {
    "asset_id": "credential-record",
    "source": "secret-reference-only",
    "kind": "CONFIGURATION",
    "contains_credentials": true
  }
]
```

`source`는 승인된 root 기준 상대 경로이다. 절대경로·상위경로·root 밖 경로·파일
심볼릭 링크는 거부한다. 디렉터리 전체를 자동 수집하지 않는다. SQLite는 read-only
연결에서 online backup API를 사용한 뒤 `PRAGMA quick_check`를 확인한다.
일반 파일은 복사 중 inode/size/mtime 변화가 발견되면 실패시킨다.
파일 작성자는 원자 교체나 불변 내보내기를 사용해야 한다.
`expected_sha256`을 등록하면 실제 snapshot과 일치해야 한다.

**세션 토큰 등 인증정보가 들어 있는 DB 전체를 일반 자료로 등록하지 않는다.**
필요하면 인증정보를 제거한 명시적 export를 등록하거나 별도 승인된 비밀보관
복원 절차를 사용한다. `contains_credentials=true` 자산은 원본을 열거나 복사·해시하지
않으며 식별 메타데이터만 암호화한다. 이 영수증의 `metadata_only=true`는
원본 자격증명 파일이나 DB를 복원할 수 있다는 뜻이 아니다.
단순 해시화된 학생 식별자를 자동으로 익명정보라고 간주하지 않는다.

## 검증 및 실패 의미

1. 등록 범위·분류 확인, 모든 필수 서버/Drive 대상 사전 점검.
2. snapshot을 기존 암호화 함수로 한 번 암호화, 원본 SHA-256 대조.
3. 해당 암호문의 인증 복호화와 평문 SHA-256 대조. 임시 평문 삭제.
4. 서버에는 0600 임시 파일 → fsync → SHA-256 → 원자 rename → 디렉터리 fsync.
   최종 저장물을 다시 읽어 SHA-256을 확인.
5. 같은 암호문을 Drive resumable upload로 업로드. 인증 요청의 업로드 주소는
   Google의 고정 HTTPS 호스트·경로만 허용하며 redirect를 따르지 않음.
6. Drive file ID/부모/크기/삭제 여부를 확인하고 암호문을 다시 내려받아 전체 SHA-256 비교.
7. 모두 확인된 자산만 완료시각을 갱신하고 owner 전용 영수증을 원자 저장.

`restore_verified`는 현재 암호문과 현재 백업 암호를 이용한 복원 대조를 뜻한다.
장래 암호 분실·변경 후에도 복구된다는 보증이 아니다. 이전 암호/키 보존 규칙은
`ENCRYPTED_BACKUP_POLICY.md`를 따른다. 실제 저장물 readback이 동일 암호문인지
검사하므로 local restore 검증과 연결된다.

서버 성공 후 Drive가 실패하면 해당 사이클은 `failed`이며 서버 영수증만 남는다.
완료시각은 갱신하지 않는다. 예외 본문·응답 본문·자격증명은 로그에 복사하지 않는다.
실패 업로드 객체의 자동 삭제는 수행하지 않는다. 보존기간 삭제정책도 설치하지 않는다.
보관량과 실패 업로드는 owner 운영 절차에서 관리해야 한다.

`verified` = 이번 회차의 필수 저장·검증 완료, `not_due` = 새 백업 수행 안 함,
`blocked` = 구성 부족, `failed` = 처리/검증/영수증 저장 실패, `busy` = 다른 실행 진행 중.
영수증의 `receipt_persisted`도 별도로 확인한다. health 200이나 암호화 self-test
성공을 모든 자료의 백업 성공으로 표시하지 않는다.

## 30분 실행

```sh
python -m app.training.backup_service --status
python -m app.training.backup_service --once
```

동일한 원본·server·state 영속 mount를 가진 실행 환경에서 운영 스케줄러의
`*/30 * * * *`에 `--once` 명령을 등록한다. 실행기는 30분 미경과 및 동일 revision이면
`not_due`를 반환한다. 내용/분류/저장대상이 달라지면 30분 전이라도 다시 저장한다.
`flock`으로 동일 state의 중복 실행을 차단한다. 서로 다른 state 디렉터리를 갖는
복제본에서는 이 잠금이 공유되지 않으므로 단일 worker로 운영한다.
별도 Railway cron service가 기존 service의 로컬 데이터를 자동 볼 수 있다고 가정하지 않는다.
웹 서비스에는 `EDURECOVER_BACKUP_SCHEDULER_ENABLED=true`와 위의 모든 저장소 구성이
확인된 경우에만 시작하는 30분 실행 루프를 추가했다. 동일 프로세스·동일 mount에서
시작 시 한 번, 이후 30분마다 실행하며 중복 task 생성과 동일 state의 중복 실행을 막는다.
현재 운영 환경에 이 플래그를 입력하거나 백업 성공을 확인한 것은 아니다.
`GET /owner/backup-status`와 `POST /owner/backup-run`은 owner 인증이 필요하다.
수동 실행은 threadpool에서 처리하며 blocked/failed는 HTTP 503, busy는 409를 반환한다.
상태 응답은 자격증명·원본·자산별 저장경로를 출력하지 않는다.

운영 데이터가 현재 컨테이너에만 있다면 **기존 데이터 내보내기/검증 없이
배포하거나 빈 볼륨으로 경로를 바꾸지 않는다.** 경로 변경은 기존 파일을 이동하지 않는다.

## 검증 범위 및 공식 자료

테스트는 실제 로컬 암호화·복호화, 서버 파일 권한/원자저장, credential source 미열람,
잘못된 해시/대상/공유/리다이렉트 차단, Drive readback 손상·부분 실패,
SQLite snapshot, 실행간격, 영수증/동시실행을 검증한다.
외부 Drive 서비스계정 발급·실제 업로드·실서버 restore·30분 scheduler 적용은 수행하지 않았다.

- [Google Drive: Shared drives overview](https://developers.google.com/workspace/drive/api/guides/about-shareddrives)
- [Google Drive: Upload file data](https://developers.google.com/workspace/drive/api/guides/manage-uploads)
- [Google Drive: files resource](https://developers.google.com/workspace/drive/api/reference/rest/v3/files)
