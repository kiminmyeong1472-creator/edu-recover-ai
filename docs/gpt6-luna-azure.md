# GPT-6 Luna를 Azure 경유로 호출

관리자 모델 화면 `/owner/open-models`에서 `GPT-6-Luna`를 선택합니다.
기존 25개 선정 모델과 AWS 추가 후보는 그대로 유지합니다.

## 운영 설정

기존 `OPENROUTER_API_KEY`를 사용합니다. 비밀키를 문서나 연결 JSON에 넣지 마십시오.

```dotenv
EDURECOVER_OPEN_MODELS_ENABLED=true
EDURECOVER_GPT6_LUNA_AZURE_ENABLED=true
```

두 번째 설정은 다음 연결만 추가합니다. 기존 모델 연결 JSON은 변경하지 않습니다.

| 항목 | 고정값 |
|---|---|
| 표시 모델 | `GPT-6-Luna` |
| 요청 주소 | `https://openrouter.ai/api/v1/chat/completions` |
| 모델 식별자 | `openai/gpt-6-luna` |
| 제공업체 | `azure` |
| 비밀키를 읽는 변수 | `OPENROUTER_API_KEY` |

호출마다 `provider.zdr=true`, `provider.data_collection="deny"`,
`provider.only=["azure"]`, `provider.allow_fallbacks=false`를 강제합니다.
무보관 대상 Azure 경로가 없으면 실패하며 다른 제공업체로 전환하지 않습니다.
전체 무보관 설정을 꺼도 이 모델의 조건은 해제되지 않습니다.
다른 주소·모델·호출 방식으로 Luna를 연결한 설정은 호출 전에 거부합니다.
고정 연결을 켠 상태에서 같은 모델을 연결 JSON에도 추가하면 설정 충돌로 거부합니다.

기존 소유자 로그인, 명시적 호출 동의, `openai` 계열 허용·차단·보류 규칙을
그대로 적용합니다. 이 설정은 해당 규칙이나 직접 OpenAI 연결의 무보관 증빙을 바꾸지 않습니다.

## 확인 범위

관리자 화면은 연결과 키 존재 여부를 확인한 상태를 실제 응답 성공과 구분합니다.
실제 호출은 소유자가 모델을 선택해 질문을 전송할 때만 발생합니다.
브라우저 OpenRouter 채팅의 성공은 EduRecover 서버 호출의 성공과 별개입니다.
무보관 강제는 OpenRouter가 분류한 제공업체 경로에 적용되며, 별도로 켠 로그·도구의 저장을
해제하는 설정은 아닙니다.

공식 문서:
- https://openrouter.ai/docs/guides/features/zdr
- https://openrouter.ai/docs/guides/routing/provider-selection
