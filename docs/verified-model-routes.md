# 2026-10-17 소유자 요청에 따른 추가 모델 연결

운영 기능 스위치: `EDURECOVER_VERIFIED_MODEL_ROUTES_ENABLED=true`.
기존 JSON 연결은 덮어쓰지 않고, 비어 있는 모델 연결만 추가합니다.
기존 소유자 허용 목록은 유지하면서 아래 모델 계열의 사용 허용을 추가합니다.
전체 중지, 명시적인 차단, 검증 전용 제한은 그대로 우선 적용됩니다.
스위치를 끄면 이번 기본 연결과 추가 허용만 해제됩니다.

## 연결 및 공개 API 목록 확인

2026-10-17 공식 공개 API를 읽어 아래 식별자와 ZDR 경로 존재를 확인했습니다.
이 확인은 해당 계정의 잔액·키 권한이나 실제 응답 성공을 검증한 것이 아닙니다.
실제 호출은 소유자 인증 화면에서 질문을 보낼 때만 실행합니다.

- https://openrouter.ai/api/v1/models
- https://openrouter.ai/api/v1/endpoints/zdr
- https://openrouter.ai/docs/guides/features/zdr

| 앱 이름 | 정확한 호출 식별자 | 확인 당시 ZDR 경로 수 |
|---|---|---:|
| Qwen3.6-27B | `qwen/qwen3.6-27b` | 5 |
| Qwen3.6-35B-A3B | `qwen/qwen3.6-35b-a3b` | 8 |
| Qwen3.5-397B-A17B | `qwen/qwen3.5-397b-a17b` | 6 |
| Qwen3.8-27B | `qwen/qwen3.8-27b` | 17 |
| gpt-oss-20b | `openai/gpt-oss-20b` | 10 |
| gpt-oss-120b | `openai/gpt-oss-120b` | 24 |
| gemma-4-26B-A4B-it | `google/gemma-4-26b-a4b-it` | 12 |
| gemma-4-31B-it | `google/gemma-4-31b-it` | 13 |
| GLM-5.1 | `z-ai/glm-5.1` | 6 |
| GLM-5.2 | `z-ai/glm-5.2` | 25 |
| GLM-5.3-Flash | `z-ai/glm-5.3-flash` | 30 |
| Mistral-Small-3.2-24B-Instruct-2506 | `mistralai/mistral-small-3.2-24b-instruct` | 4 |
| Mistral-Small-4-119B-2603 | `mistralai/mistral-small-2603` | 3 |
| phi-4 | `microsoft/phi-4` | 2 |
| NVIDIA-Nemotron-3.5-Lightning-30B-A3B-BF16 | `nvidia/nemotron-3.5-lightning` | 3 |
| Qwen3-32B | `qwen/qwen3-32b` | 3 |
| Amazon-Nova-2-Lite | `amazon/nova-2-lite-v1` | 2 |
| Amazon-Nova-Pro | `amazon/nova-pro-v1` | 2 |
| Claude-Haiku-5.5 | `anthropic/claude-haiku-5.5` | 6 |
| Gemini-2.5-Flash-Lite | `google/gemini-2.5-flash-lite` | 2 |

모든 추가 연결은 기존 Railway `OPENROUTER_API_KEY`를 참조합니다.
키 값은 읽거나 복사하지 않습니다. 각 요청은 `provider.zdr=true`,
`provider.data_collection="deny"`를 전송하므로 ZDR 경로가 없으면 실패합니다.
다른 모델로 자동 대체하지 않습니다. 같은 모델의 ZDR 제공자 간 라우팅은 허용됩니다.
Luna는 기존 Azure 전용·제공자 대체 금지 설정을 유지합니다.
DeepSeek Flash/Pro는 기존 공식 직접 API와 전용 키를 그대로 사용합니다.
나머지 계열의 별도 제공자 키가 현재 등록되어 있지 않아 이번 추가 연결은
OpenRouter를 사용합니다. 직접 제공자 연결이 완료됐다고 표시하지 않습니다.

## 사용 조건 확인 범위

ZDR은 데이터 보관 조건이며 경쟁 모델 개발 허용과 별개입니다.
공개 가중치 라이선스와 API 서비스 계약도 별개입니다.
OpenRouter 약관 5.1·5.8은 각 모델의 약관을 별도로 적용합니다.
https://openrouter.ai/terms

다음 공개 모델 저장소의 모델 카드 및 라이선스를 확인했습니다.
Apache-2.0·MIT에는 경쟁 모델 개발 자체를 금지하는 조항이 없습니다.
이는 모든 API 경로의 조건이 없다는 보증이 아닙니다.

- Qwen3.6-27B: https://huggingface.co/Qwen/Qwen3.6-27B (공식 모델 카드 및 LICENSE)
- Qwen3.6-35B-A3B: https://huggingface.co/Qwen/Qwen3.6-35B-A3B (공식 모델 카드 및 LICENSE)
- Qwen3.5-397B-A17B: https://huggingface.co/Qwen/Qwen3.5-397B-A17B (공식 모델 카드 및 LICENSE)
- Qwen3.8-27B: https://huggingface.co/Qwen/Qwen3.8-27B (공식 모델 카드 및 LICENSE)
- gpt-oss-20b: https://huggingface.co/openai/gpt-oss-20b (공식 모델 카드 및 LICENSE)
- gpt-oss-120b: https://huggingface.co/openai/gpt-oss-120b (공식 모델 카드 및 LICENSE)
- gemma-4-26B-A4B-it: https://huggingface.co/google/gemma-4-26B-A4B-it (공식 모델 카드 및 LICENSE)
- gemma-4-31B-it: https://huggingface.co/google/gemma-4-31B-it (공식 모델 카드 및 LICENSE)
- GLM-5.1: https://huggingface.co/zai-org/GLM-5.1 (공식 모델 카드 및 LICENSE)
- GLM-5.2: https://huggingface.co/zai-org/GLM-5.2 (공식 모델 카드 및 LICENSE)
- GLM-5.3-Flash: https://huggingface.co/zai-org/GLM-5.3-Flash (공식 모델 카드 및 LICENSE)
- Mistral-Small-3.2-24B-Instruct-2506: https://huggingface.co/mistralai/Mistral-Small-3.2-24B-Instruct-2506 (공식 모델 카드 및 LICENSE)
- Mistral-Small-4-119B-2603: https://huggingface.co/mistralai/Mistral-Small-4-119B-2603 (공식 모델 카드 및 LICENSE)
- phi-4: https://huggingface.co/microsoft/phi-4 (공식 모델 카드 및 LICENSE)
- NVIDIA-Nemotron-3.5-Lightning-30B-A3B-BF16: https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-BF16 (공식 모델 카드 및 LICENSE)
- Qwen3-32B: https://huggingface.co/Qwen/Qwen3-32B (공식 모델 카드 및 LICENSE)

Gemma 4는 구형 Gemma 약관과 구분해야 합니다. 공식 약관 페이지도
Gemma 4에는 별도 Apache-2.0을 안내합니다.
https://ai.google.dev/gemma/terms
https://ai.google.dev/gemma/apache_2
NVIDIA Lightning은 OpenMDW-1.1입니다.
https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-BF16/blob/main/LICENSE

Claude, Gemini, Nova는 공개 라이선스 모델로 분류하지 않습니다.
Claude는 Haiku 5.5, Gemini는 텍스트 요청용 저가 모델인 2.5 Flash-Lite를 선택했습니다.
해당 모델을 경쟁 모델 개발 제한이 없는 모델로 표시하지 않습니다.
Claude 서비스의 경쟁 개발 제한은 ZDR이나 중개 경로로 없어지지 않습니다.
https://www.anthropic.com/legal/commercial-terms
https://ai.google.dev/gemini-api/terms
https://aws.amazon.com/service-terms/

## 연결하지 않은 9개 항목

- DeepSeek-V4-Flash, DeepSeek-R1, DeepSeek-V3.2: 기존 소유자의 DeepSeek OpenRouter 제외 요청 유지. 현재 직접 API 프리셋 두 모델에 다른 이름을 임의로 연결하지 않습니다.
- gemma-4-E2B-it, gemma-4-E4B-it, gemma-4-12B-it: 확인한 OpenRouter 공개 목록에 정확한 모델 없음.
- Mistral-Large-3-675B-Base-2512: 공개 API의 Large Instruct를 Base로 대체하지 않습니다.
- NVIDIA-Nemotron-3-Nano-4B-BF16: 30B 등 다른 크기로 대체하지 않습니다.
- NVIDIA-Nemotron-3-Super-120B-A12B-BF16: 공개 목록의 FP8 모델을 BF16이라고 표시하지 않습니다.

이 항목은 별도 공식 직접 제공자, Bedrock/SageMaker 배포 식별자와 권한을
확인해야 연결할 수 있습니다. 유료 GPU 서버나 신규 결제를 자동 생성하지 않습니다.
