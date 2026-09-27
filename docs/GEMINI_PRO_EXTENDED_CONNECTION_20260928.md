# EduRecover Gemini Pro Extended 연결 설계·운영 검증서

- 작성일: 2026-09-28 (KST)
- 적용 대상: EduRecover AI 문제 생성 및 교차검증 채널
- 결론: Gemini 앱의 `Extended thinking`에 대응하는 API 프로필을
  `gemini-3.1-pro-preview` + `HIGH` thinking으로 구성한다.

## 1. 명칭과 구현의 구분

`Pro`와 `Extended thinking`은 Gemini 앱에서 사용자에게 보이는 선택 명칭이다.
API에 `gemini-pro-extended`라는 모델 ID를 넣는 방식이 아니다. EduRecover에서는
현재 Pro API 모델인 `gemini-3.1-pro-preview`를 지정하고 `thinking_level=HIGH`를
함께 보내 앱의 Extended thinking과 기능적으로 대응시키는 프로필을 사용한다.

Google 공식 문서:

- Gemini 3.1 Pro Preview 모델: https://ai.google.dev/gemini-api/docs/models/gemini-3.1-pro-preview
- Thinking 설정: https://ai.google.dev/gemini-api/docs/thinking
- Gemini 앱의 Extended thinking 설명: https://support.google.com/gemini/answer/16275805?hl=en
- API 릴리스 노트: https://ai.google.dev/gemini-api/docs/changelog

## 2. 적용 설정

| 구분 | 적용값 | 이유 |
|---|---|---|
| SDK | `google-genai>=1.0,<2.0` | Google 공식 Gen AI SDK |
| 모델 | `gemini-3.1-pro-preview` | 현재 Pro 계열 API 모델 |
| 사고 수준 | `HIGH` | 복잡한 문제 생성·검증용 Extended 대응 |
| 응답 형식 | `application/json` | 기존 문제 파서와 정확히 연결 |
| 키 변수 | `GEMINI_API_KEY` | 기존 Railway 보안변수 유지 |
| 호출 위치 | 서버 백엔드 | 키의 브라우저 노출 방지 |

적용 코드:

- `app/core/config.py`: 모델과 사고 수준 환경변수
- `app/services/auto_generator.py`: 문제 생성 시 Pro/HIGH/JSON 설정
- `app/services/provider_verifier.py`: 교차검증에도 같은 프로필 적용
- `app/main.py`: 소유자 상태 화면에 모델·사고 수준·프로필 표시
- `app/pilot_page.py`: 교사 화면의 제공자 명칭 표시

## 3. 보안 및 데이터 처리 경계

1. API 키는 코드·화면·로그에 기록하지 않고 Railway 서버 환경변수로만 읽는다.
2. 기존 키를 임의 재발급하거나 교체하지 않는다.
3. Google 유료 API 이용과 EduRecover의 자체 ZDR 통과 표시는 같은 개념이 아니다.
   Google 약관과 프로젝트 청구 상태가 확인되지 않은 상태에서 ZDR을 임의 확정하지
   않는다.
4. Google 공식 약관상 무료 서비스 입력·출력은 제품 개선 등에 사용될 수 있으므로
   민감정보를 보내면 안 된다. 활성 결제 프로젝트를 통한 유료 서비스는 입력·출력을
   제품 개선에 사용하지 않지만, 안전·보안 및 법적 요구를 위한 제한적 로그 처리는
   남을 수 있다.
5. 학생 개인정보·민감정보는 전송 전에 EduRecover 개인정보 필터 및 제공자 허용
   게이트를 통과해야 한다.

관련 공식 문서:

- Gemini API 추가 약관: https://ai.google.dev/gemini-api/terms
- API 키 관리: https://ai.google.dev/gemini-api/docs/api-key

## 4. 2026년 API 키 전환 점검

Google은 2026-05-28부터 AI Studio의 신규 키를 authorization key로 발급한다.
제한 없는 표준 키는 Gemini API가 거부할 수 있다. 현재 키가 실패할 경우에도 곧바로
교체하지 않고 다음 순서로 진단한다.

1. 오류 상태와 예외 유형을 확인한다.
2. AI Studio에서 Key Type이 Standard인지 Authorization인지 확인한다.
3. Standard라면 Gemini API 전용 제한이 적용되어 있는지 확인한다.
4. 기존 키 수정 또는 신규 auth key 전환이 필요한 경우 소유자 승인을 받은 뒤 시행한다.

## 5. 오류 분리 기준

| 증상 | 우선 확인 |
|---|---|
| 401/403 | 키 유형, 프로젝트, IAM, Generative Language API 활성화, 키 제한 |
| 404 | 모델 ID 및 모델 수명주기 |
| 429 | 프로젝트별 할당량·결제 등급·요청 빈도 |
| 500/503 | 공급자 일시 장애; 제한적 재시도 |
| JSON 파싱 실패 | 응답 MIME 설정, 안전 차단, 출력 완결성 |

Preview 모델은 운영 사용이 가능하지만 수명주기와 사용량 제한이 더 자주 바뀔 수
있다. 정식 stable Pro 모델이 제공되면 별도 검증 후 명시적 모델 ID로 교체한다.

## 6. 배포 완료 판정 기준

- 전체 자동화 테스트 통과
- Railway 환경변수에 모델과 사고 수준 반영
- 운영 배포 SUCCESS
- `/health` HTTP 200
- `/ai-status`에서 Gemini 준비 상태 확인
- 운영 `/channel/gemini/generate`에서 문제 1건 실제 생성 성공
- 응답의 `effective_provider`가 `gemini`이고 실행 영수증이 존재

실제 운영 호출 결과는 배포 검증 후 이 문서에 추가한다.

## 7. 2026-09-28 운영 검증 결과

| 항목 | 결과 |
|---|---|
| 자동화 테스트 | 51 passed, 5 subtests passed |
| Railway 배포 | SUCCESS |
| `/health` | HTTP 200 |
| `/ai-status` | Gemini `ready=true` |
| Pro 실제 생성 | HTTP 502 (내부 upstream 429) |
| Google 오류 | `RESOURCE_EXHAUSTED` |
| 확정 원인 | `gemini-3.1-pro` 무료 등급 입력 토큰 한도 0 |

Google의 원문 오류는 현재 프로젝트의
`generate_content_free_tier_input_token_count` 한도가 0이라고 명시했다. 따라서
API 키 형식이나 모델 ID가 잘못된 것이 아니라, 키가 연결된 Google Cloud 프로젝트가
Gemini 3.1 Pro의 유료 사용 등급을 갖지 못한 상태다.

### 남은 소유자 조치

1. Google AI Studio의 **Projects** 또는 **Billing**에서 현재
   `GEMINI_API_KEY`와 연결된 프로젝트를 선택한다.
2. **Set up billing**을 눌러 유료 등급을 활성화한다.
3. Prepay로 지정되면 Google 정책상 최소 미화 5달러 상당의 크레딧을 충전한다.
4. 결제 반영 후 동일한 운영 생성 시험을 다시 실행한다.

결제·선불 충전은 비용과 계약을 발생시키므로 자동으로 시행하지 않았다. 키 역시
교체하지 않았다. 사용자가 결제를 활성화한 뒤 실제 생성 1건이 성공해야 본 연결을
최종 완료로 판정한다.
