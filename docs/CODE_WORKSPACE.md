# EduRecover 공통 코딩 작업 공간

이 기능은 모델 제공자와 별도로 파일 읽기·쓰기, 명령 실행, 실제 시험 결과와 생성 파일을 관리합니다. 관리 화면은 `/owner/code-workspace`입니다. 입력한 코드는 운영 앱 프로세스가 아닌 Railway의 별도 격리 가상 머신에서만 실행됩니다. 운영 앱의 모델 키·관리자 키는 가상 머신에 전달하지 않습니다.

## 설치와 실제 실행의 구분

- 서버 코드와 화면 배포만으로 실제 실행 성공을 의미하지 않습니다.
- 실행 환경 생성에는 `RAILWAY_API_TOKEN` 또는 SDK에서 지원하는 `RAILWAY_TOKEN`과 `RAILWAY_ENVIRONMENT_ID`가 필요합니다. 토큰은 Railway 서비스 환경변수에만 저장하며 채팅·소스·브라우저 출력에 노출하지 않습니다.
- 기본 제공 이미지에서 Python 3·Bash·Git·Node.js 사용 가능 여부를 검사합니다. 프로젝트 의존성 설치는 격리 환경 안의 명령으로 실행할 수 있습니다.
- 각 실행은 작은 CPU 가상 머신을 만들고 종료 시 제거합니다. 생성·사용 요금은 Railway 정책을 따릅니다. 학습용 GPU를 생성하는 기능이 아닙니다.
- 현재 운영 구성인 웹 작업자 1개·복제본 1개에서만 사용합니다. 복제본을 늘리기 전에는 데이터베이스 기반 실행 잠금·취소 관리로 바꾸어야 합니다.
- 토큰 미설정, 생성 실패, 모델 호출 실패, 명령 오류, 시험 실패를 각각 기록합니다. 사용자 취소는 실행 중인 명령의 제한 시간 후 환경 정리로 이어집니다.
- 초기 유휴 제한은 Trial/Free 호환 5분입니다. 모델 응답이 오래 지연되어 VM이 유휴 종료되면 실제 실행은 실패로 기록되며 성공으로 표시하지 않습니다.

## 실행 방식

1. 직접 실행: 사용자가 코드 파일과 명령을 입력하고 명시적으로 시작합니다.
2. 모델 도구 실행: 정확히 설정된 모델 하나가 파일·명령 도구를 요청하고 실제 결과를 받아 수정합니다. 모델 설정·사용 허용·기존 호출 정책을 유지하며 다른 모델로 대체하지 않습니다.
3. 운영 시스템 반영: 현재 단계에서는 수행하지 않습니다. 최신 작업 순서의 3순위에서 시험 결과·변경 비교·적용·되돌리기를 별도로 구현합니다.

## 네 평가 분야

| 분야 | 공개 시험 이름 | 자체 시험 |
|---|---|---|
| 코드 문제 풀이 | LiveCodeBench | 순서 유지 중복 제거 |
| 저장소 코드 수정 | SWE-bench Verified·Pro, DeepSWE 각각 | 중앙값 함수 오류 수정 |
| 터미널 작업 | Terminal-Bench | CSV 읽기와 결과 파일 생성 |
| 과학 계산 코딩 | SciCode | 평균·모집단 표준편차 |

이 네 분야는 화면 정리를 위한 구분입니다. 자체 소규모 시험을 공식 시험 점수로 표시하지 않습니다. 미실행은 0점으로 표시하지 않습니다. 모델 하나와 과제 하나를 선택해 각각 실행하며 종료 코드·출력·오류·파일·소요 시간을 기록합니다. 공급자가 반환한 실제 비용을 받지 못하면 비용 미확인으로 표시합니다. 공개 점수는 시험 이름·버전·도구·추론 조건·모델 정밀도가 같은지 확인한 뒤 비교해야 합니다.

## 공식 구현 근거

- https://docs.railway.com/sandboxes
- https://docs.railway.com/guides/code-execution-sandboxes
- https://github.com/railwayapp/railway-ts-sdk/blob/97fdb4f61d351319d5633c2c92a86ca2ff0062d8/src/graphql/operations/sandbox.graphql
- https://chutes.ai/docs/guides/agents-and-tools


## 외부 AI 코드 초안 요청과 별도 실행 승인

이 추가 기능은 에이전트나 도구 호출 지원 없이 기존 `call_model`을 한 번 호출합니다. 화면에서 **외부 AI에 코드만 요청 · 확인 후 실행**을 선택하면, 선택한 AI에 현재 편집기의 모든 파일과 요청을 보내는 승인 항목이 나타납니다. 코드 요청은 VM 생성, 명령 실행, 패키지 설치 또는 운영 반영을 수행하지 않습니다.

- 기본값은 꺼짐입니다. 대표님의 활성화 승인 이후 운영자가 `EDURECOVER_CODE_GENERATION_ENABLED=true`로 설정합니다. `false`로 되돌리면 이후 초안 요청을 차단합니다. 이미 받은 코드와 실행 기록을 삭제하지 않습니다.
- 사용 허용과 연결 설정을 갖춘 정확한 기존 모델 및 `learning` 또는 `chutes` 경로만 사용합니다. 요청에 새 URL, 인증 키, 제공자 또는 설치 명령을 넣을 수 없습니다. 기존 27개 목록·Chutes 변형·OpenRouter 경로는 변경하지 않습니다.
- `POST /owner/code-workspace/generate`에는 관리자 인증과 정확한 불리언 `explicit_authorization: true`가 필요합니다. 요청은 `model_key`, `route`, `prompt`, `files`, 선택적 `max_tokens`를 받습니다. 기본 출력 한도는 4096, 허용 범위는 256~8192 토큰입니다. 토큰은 모델이 처리하는 글 조각이며 요금의 계산 단위입니다.
- 한 번의 추론 시도만 합니다. 실패 시 다른 회사·모델로 자동 전환하거나 자동 재시도하지 않습니다. 한 번의 요청과 출력 제한은 원화·달러 총비용 상한이 아닙니다. 실제 요금은 제공자 계정에서 확인합니다.
- AI 응답은 `{"summary":"설명","files":[{"path":"main.py","content":"print(2)"}]}` 형식만 받습니다. 명령 필드는 허용하지 않습니다. 중복 JSON 키, 경로 이탈, 예약 디렉터리, 중복 파일, 크기·개수 초과는 전체 초안을 거절합니다.
- 변경 파일을 기존 편집 파일과 합친 뒤 최대 40개, 파일당 UTF-8 128 KiB, 합계 512 KiB를 다시 검사합니다. 각 파일의 SHA256과 초안 확인값을 반환합니다.
- 성공하면 편집기에 초안을 불러오고 **직접 코드 입력**으로 전환합니다. 실행 명령은 AI가 바꾸지 않습니다. 전송 승인과 실행 승인 선택은 모두 해제합니다. 사용자가 파일과 명령을 다시 검토하고 별도로 **코드 실행**을 눌러야 기존 격리 실행기로 넘어갑니다.
- 초안은 브라우저 편집 상태에만 있고 새 실행 기록으로 저장되지 않습니다. 창을 새로 열기 전에 필요한 내용을 보관합니다. 실행 후 결과 파일은 기존 실행 기록과 다운로드 기능으로 보관할 수 있습니다.

### 연결 범위

현재 통신 구현은 OpenAI 호환 chat/completion, AWS Bedrock Converse, SageMaker 엔드포인트입니다. 제공자의 텍스트 생성이 가능하고 요구한 JSON 파일 형식으로 응답해야 합니다. 회사의 기능 지원과 이 계정의 실제 연결 성공은 별개입니다. Anthropic Messages, Gemini 원래 API, OpenAI Responses 원래 형식은 이 변경에서 새 어댑터를 설치하지 않습니다. 기존에 승인된 호환 경로가 있으면 그 경로를 사용할 수 있습니다.

AWS CloudShell 공식 FAQ는 프로그램 상호작용 API를 지원하지 않는다고 명시합니다. 따라서 CloudShell을 상시 원격 실행 API로 연결하지 않습니다. CloudShell에서 사람이 파일·명령을 수동 실행하는 사용법과 현재 격리 실행기를 구분합니다. AWS 런타임 전환은 기존 6순위 작업에 맞춰 별도로 검토합니다.

### 변경 파일과 검증

- `app/services/code_generation.py`: 요청 승인·파일 검증·한 번의 외부 호출·미실행 초안·SHA256.
- `app/services/code_execution_api.py`: 관리자 전용 초안 API와 활성화 상태. 기존 실행 입력의 파일 검사를 공유합니다.
- `app/services/code_execution_workspace.py`: 코드 요청 버튼, 별도 전송 승인, 검토 후 실행 승인.
- `tests/test_code_generation.py`: 승인·비활성화·응답 오류·제공자 오류·파일 합계·인증·VM 미생성 시험.
- `tests/test_code_execution_workspace.py`: 실제 화면 JavaScript로 승인 분리·오류 시 편집 내용 유지·자동 실행 방지 시험.

오프라인 검증: `python3 -m unittest discover -s tests -p 'test_code*.py'`. 외부 API와 VM은 시험용 응답으로 대체하며 유료 추론·운영 반영 성공을 뜻하지 않습니다.

공식 자료 확인일: 2026년 10월 17일 한국 시간.
- OpenAI 도구 호출: https://developers.openai.com/api/docs/guides/function-calling
- Claude 도구: https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview
- Gemini 도구 호출: https://ai.google.dev/gemini-api/docs/function-calling
- AWS Bedrock: https://docs.aws.amazon.com/bedrock/latest/userguide/tool-use-client-side.html
- AWS CloudShell: https://aws.amazon.com/cloudshell/faqs/
- OpenRouter: https://openrouter.ai/docs/guides/features/tool-calling
