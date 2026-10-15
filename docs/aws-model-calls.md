# 큐웬·딥시크·노바 호출 연결

에듀리커버의 관리자 화면 `/owner/open-models`에서 모델을 선택해 튜터 문답,
교재에 따른 문제·정답·해설, 교육·의료자료 검토를 요청하는 코드입니다.
다른 프로그램에 요청을 보내고 답을 받는 연결 방식(API)은
`GET /owner/open-models/catalog`, `POST /owner/open-models/draft`입니다.
기존 관리자 인증과 제공자 사용 설정을 적용합니다.

## AWS에서 확인한 호출 대상

| 모델 | AWS 호출 식별자 | 요청을 보내는 지역 |
|---|---|---|
| Qwen3-32B | `qwen.qwen3-32b-v1:0` | 도쿄 `ap-northeast-1` |
| DeepSeek-V3.2 | `deepseek.v3.2` | 도쿄 `ap-northeast-1` |
| Amazon Nova 2 Lite | `global.amazon.nova-2-lite-v1:0` | 서울 `ap-northeast-2`에서 전 세계 경로 |

이는 AWS 공식 문서에서 확인한 연결 예시이며 사용자 계정의 호출 시험 결과가 아닙니다.
서울의 앱 서버에서도 도쿄의 모델 서비스를 호출할 수 있습니다. Nova의 전 세계
경로는 서울 안에서만 처리하는 방식이 아닙니다. 선택 지역과 모델을 명시하며
실패했을 때 다른 모델이나 다른 회사의 직접 연결로 자동 전환하지 않습니다.

기존 25개 선정 목록은 보존했습니다. 표의 Qwen3-32B와 DeepSeek-V3.2는 그 목록의
신버전을 대신한다고 표시하지 않고 별도 AWS 제공 모델로 추가했습니다.
Nova도 공개 가중치 모델이라고 표시하지 않습니다.

## 실행 서버에 필요한 설정

`deployment/aws/model-calls.env.example`은 비밀값 없는 설정 예시입니다.
각 계열의 `MODEL_KEY`(화면의 모델 이름), `MODEL_ID`(AWS 호출 식별자),
`REGION`(요청 지역)을 모두 지정하십시오. 기존 소유자 허용 목록에
`qwen`, `deepseek`, `nova`를 추가하고, 다른 제공자나 기존 중지 설정은 보존하십시오.
앱의 기존 통제 원장에 이 계열이 차단돼 있으면 차단 사유가 표시됩니다.

서버 실행 권한을 관리하는 AWS 역할(IAM role)에 해당 모델의
`bedrock:InvokeModel` 권한이 필요합니다. EC2에 붙인 역할 등 AWS의 기존 인증
방식을 사용하며, 비밀 접근 키를 대화창·이 문서·저장소에 넣지 마십시오.
모델별 제공 지역, 계정 접근 권한과 사용 한도도 충족해야 합니다.
이 코드는 역할을 만들거나 권한을 확대하거나 새 유료 서버를 만들지 않습니다.

연결 설정만 확인하는 검사는 `python scripts/check_aws_model_connections.py`입니다.
실제 호출 검사는 `--invoke`와 `--model-key`를 함께 지정할 때만 진행합니다.
예: `python scripts/check_aws_model_connections.py --invoke --model-key Amazon-Nova-2-Lite`.
이 검사는 선택한 모델마다 간단한 덧셈 요청을 한 번 보내므로 호출료가 발생할 수
있습니다. 기록에는 응답 수신 여부·정답 일치 여부·오류 코드만 남기고, 비밀값과
모델 답변 원문은 출력하지 않습니다. 설정 검사 통과와 실제 응답 수신은 다릅니다.

## 노바의 아마존 클라우드 앱 작업 범위

Nova 2 Lite는 코드 작성·디버깅에 사용할 수 있습니다. AWS 자원 생성·배포를
실행하려면 Nova가 선택한 도구를 앱에서 실행하고 결과를 되돌려주는 연결과,
그 도구에 필요한 AWS 권한이 별도로 필요합니다. 현재 추가한 경로는 모델 문답이며
AWS 자원 생성·배포 명령을 자동 실행하는 경로는 추가하지 않았습니다.
Nova의 격리된 Python 계산 환경과 브라우저 자동화 서비스 Nova Act도 이 문답
경로와 구별됩니다. 실제 AWS 작업이 끝났다는 주장을 모델 답변만으로 기록하지 않습니다.

의료자료 검토는 계속 포함합니다. 초안 생성과 실제 출처 대조, 세 개 이상 외부 AI
검증, 환자 기록 저장은 서로 다른 상태이며 모델 답변만으로 완료 처리하지 않습니다.

## 공식 근거

- [Qwen3-32B 모델·지역·호출 방식](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-qwen-qwen3-32b.html)
- [DeepSeek-V3.2 모델·지역·호출 방식](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-deepseek-deepseek-v3-2.html)
- [Nova 2 Lite 모델·지역](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-amazon-nova-2-lite.html)
- [Nova 대화와 코드 작성](https://docs.aws.amazon.com/nova/latest/nova2-userguide/using-converse-api.html)
- [Nova 도구 호출과 실제 도구 실행의 구별](https://docs.aws.amazon.com/nova/latest/nova2-userguide/using-tools.html)

확인 기준: 2026년 10월 15일. 공식 문서 제공 여부와 사용자 계정 접근은 구분합니다.
