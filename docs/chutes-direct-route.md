# 기존 경로를 유지한 별도 직접 호출

2026-10-17 소유자 요청: 기존 OpenRouter 경로를 삭제하거나 대체하지 않고,
Railway 앱에서 다른 제공자로 직접 요청하는 경로 하나를 더 제공합니다.
경쟁 모델 개발·학습 제한 여부를 최우선으로 검토하고 ZDR은 별도로 확인합니다.

## 구현

- 기존 화면: `/owner/open-models` (기존 동작 보존)
- 추가 화면: `/owner/open-models/chutes` (직접 질문만)
- 추가 목록: `/owner/open-models/chutes/catalog`
- 추가 호출: `/owner/open-models/chutes/call`
- 제공자: Chutes
- 모델: `DeepSeek-V3.2`
- 정확한 호출 ID: `deepseek-ai/DeepSeek-V3.2-TEE`
- API: `https://llm.chutes.ai/v1/chat/completions`
- 서버 인증 키: `CHUTES_API_KEY`

모든 화면과 API에는 기존 소유자 인증 및 계열별 사용 제어가 적용됩니다.
Chutes 키는 OpenRouter·DeepSeek 공식 키와 별개입니다. 새 키가 없으면 인증
정보 필요로 표시합니다. 연결 설정이나 공개 모델 목록 조회만으로 실제
응답 성공이라고 표시하지 않습니다. 키 값은 화면·로그·문서에 기록하지 않습니다.

이 경로는 기존 JSON 모델 연결이나 OpenRouter 프리셋을 읽어 대체하지 않습니다.
Chutes 호출 실패 시 OpenRouter나 다른 제공자로 재시도하지 않습니다.
직접 질문과 문서 첨부·브라우저 받아쓰기를 지원합니다. 사진은 지원하지 않습니다.
기존 튜터·문제출제·자료검토는 기존 작업 화면에 그대로 남습니다.

## 모델 및 호출 근거

- 공식 공개 목록: https://llm.chutes.ai/v1/models
- 공식 설명: https://chutes.ai/docs/models/chutes-deepseek-ai-deepseek-v3-2-tee
- 공식 모델 라이선스: https://huggingface.co/deepseek-ai/DeepSeek-V3.2/blob/main/LICENSE

공개 목록의 root는 `deepseek-ai/DeepSeek-V3.2`입니다. 공식 제공자 설명은
DeepSeek가 공개한 FP8 가중치를 서비스하며 TEE는 실행 환경의 속성이라고
명시합니다. 기존 앱의 V3.2 이름에는 BF16 정밀도 조건이 없으므로 다른
정밀도 모델을 BF16이라고 표시하지 않습니다. 다른 모델 이름을 자동 대체하지 않습니다.

앱은 답변 토큰 수를 기본값으로 지정하지 않습니다. Chutes 공식 설명의
제공자 기본 출력 예산은 1,024토큰이며, 토큰 필드 생략이 무한한 답변을
의미하지는 않습니다. 모델의 문맥·출력·서비스 한도는 별도로 적용됩니다.

## 사용 조건 검토와 한계

모델의 MIT 라이선스에는 경쟁 모델 개발 금지 조항이 없습니다. 2026-03-16
갱신 Chutes 약관 전체에서 경쟁 모델·서비스 개발이나 출력의 모델 학습을
금지하는 조항은 발견하지 못했습니다. 생성 결과의 소유권은 사용자에게
있고, 모델 라이선스와 관련 법률은 준수해야 합니다.
이 판단은 읽은 문서의 검토 결과이며 특정 학습 계획에 관해 제공자에게
서면 승인을 받았다는 뜻이 아닙니다. 모든 목적에 대한 허가나 법적 보증으로
표시하지 않습니다.

- 약관: https://chutes.ai/terms
- 개인정보 정책: https://chutes.ai/privacy

Chutes 약관은 공개 모델의 API 입력·응답을 저장·로그·영속화하지 않으며,
제공자 자신의 모델 학습에 사용하지 않는다고 설명합니다. 이는 제공자
정책에 관한 진술이며 실제 계정·호출·저장 상태를 독립 검증한 것이 아닙니다.
일반 Bearer API 요청에 클라이언트 종단 암호화를 추가 구현하지 않았으며,
TEE만으로 전 경로에 종단 암호화가 적용된다고 주장하지 않습니다.
운영 앱·대량 자동 호출에는 구독형 대신 사용량 과금(PAYGO) 정책이 적용됩니다.
새 계정·결제·유료 모델 호출은 이 코드 변경만으로 수행되지 않습니다.

OpenRouter 약관 7번4호 및 제공자별 추가 조건과는 독립된 Chutes 직접 경로입니다.
기존 OpenRouter 연결의 경쟁 모델 개발·학습 허용 상태는 변경하거나 승인하지 않습니다.
AWS로 직접 바꾸는 것 역시 AWS 서비스약관 50.5와 모델별 계약 확인 없이
학습이 자유롭다고 표시할 수 없습니다.

- https://openrouter.ai/terms
- https://aws.amazon.com/service-terms/
