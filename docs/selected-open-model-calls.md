# 선정한 25개 공개 가중치 모델의 요청 연결

선정한 25개 모델을 모두 개별 서버 주소에 연결할 수 있도록 설정 예시를 마련했습니다.
아직 실제로 사용할 모델 서버 주소는 **0개** 확인됐으며, 25개가 모두 실행 중이거나
호출에 성공했다고 확인한 것은 아닙니다. 연결했다고 해서 반드시 호출에 성공하는 것은 아닙니다.

학습으로 정해진 모델의 수치 자료인 **가중치**를 공개한 모델과,
다른 프로그램이 요청을 보내고 답을 받는 **연결 방법(API)**은 서로 다릅니다.
공식 가중치 저장소가 있어도 바로 요청할 수 있는 서버가 제공되는 것은 아닙니다.
이 문서의 공식 저장소 링크는 모델의 정확한 이름과 가중치 공개를 확인하는 근거입니다.
사용할 서버의 운영 여부, 계정 권한, 비용, 실행하는 가중치와 응답 형식은 별도로 확인해야 합니다.

## 현재 연결 범위

앱 실행 서버는 기존 Railway 운영 계획과 EC2 이전 계획을 유지합니다.
모델 요청은 앱 실행 서버에서 운영자가 지정한 외부 모델 서버로 보냅니다.
가중치를 실행하는 별도 서버를 사용하면 앱 실행 서버에 25개 모델의 가중치를
모두 내려받거나 학습할 필요는 없습니다. 이 변경으로 모델 서버를 새로 만들거나 유료 자원을 신청하지 않습니다.

현재 확인한 기존 비밀키 관련 변수는 `OPENAI_API_KEY`, `GEMINI_API_KEY`와
AWS 인증 변수의 **이름**입니다. 이 변수들이 있다는 사실은 선정한 25개 모델의
주소나 사용 권한이 준비됐다는 뜻이 아닙니다. 이 예시는 각 모델용 비밀키를 별도
변수에서 읽으며, 기존 키를 자동으로 재사용하거나 다른 제공자의 요청으로 바꾸지 않습니다.

이 25개 연결 예시에는 AWS Bedrock, SageMaker 또는 Nova 연결을 넣지 않았습니다.
기존 AWS 연결 코드는 별도로 유지합니다. Nova의 실제 호출 시험은 보류 상태이며,
이 문서의 검사로 Nova 호출을 시작하지 않습니다.
기존 추가 AWS 후보 4개도 원래 선정한 25개와 섞지 않습니다.

## 실제 운영에 넣을 수 없는 예시 파일

`deployment/railway/open-model-bindings.example.json`에는 원래 선정 목록과 같은
순서로 25개의 이름이 들어 있습니다. `model-01.example.com`부터
`model-25.example.com`까지는 **설명용 가짜 주소**이며, 실제 모델 서버가 아닙니다.
프로그램도 이 주소들을 잘못된 설정으로 거부합니다.
이 파일을 그대로 환경변수에 넣거나 그대로 연결 검사에 사용하지 마십시오.

운영용 사본에는 사용 권한과 모델이 확인된 실제 서버 주소만 넣으십시오.
아직 준비하지 않은 모델의 항목은 운영용 사본에서 제외하십시오.
예시 원본의 25개 목록은 그대로 보존하십시오.
각 항목의 뜻은 다음과 같습니다.

| 설정 항목 | 뜻과 운영 시 확인할 내용 |
| --- | --- |
| 바깥쪽 모델 이름 | `SOURCE_MODEL_CATALOG`에 저장된 선정 이름입니다. 대소문자와 버전 문자열을 그대로 사용하십시오. |
| `transport` | 요청을 보내는 방식입니다. `openai_compatible`은 OpenAI와 같은 요청·응답 구조를 따르는 서버를 뜻합니다. OpenAI가 그 모델을 제공한다는 뜻은 아닙니다. |
| `endpoint_url` | 요청을 받을 **전체 주소**입니다. 암호화된 연결인 HTTPS를 사용하며, 일반 대화는 `/v1/chat/completions`, Base 모델의 문장 이어쓰기는 `/v1/completions`로 끝납니다. 제공자가 지정한 전체 주소로 바꾸십시오. |
| `model_id` | 상대 서버가 요청에서 받는 모델 식별자입니다. 예시에는 공식 가중치 저장소의 정확한 식별자를 넣었습니다. 실제 서버가 받는 식별자와 다르면 해당 서버의 확인된 값으로 바꾸십시오. |
| `api_key_env` | 비밀키를 보관한 서버 환경변수의 **이름**입니다. 예시의 `EDURECOVER_OPEN_MODEL_01_API_KEY`부터 `EDURECOVER_OPEN_MODEL_25_API_KEY`까지는 값이 아닙니다. 실제 비밀키는 배포 환경의 해당 변수에 보관하십시오. |
| `request_format` | `chat`은 역할별 대화 내용을 보내는 방식이며, `completion`은 앞 문장을 보내 뒤 문장을 생성하는 방식입니다. 두 방식을 상대 서버가 지원하는지 확인하십시오. |

서버를 실행하는 도구인 vLLM이나 SGLang에서 `--served-model-name`으로
요청용 이름을 바꾸면 `model_id`도 그 이름과 일치해야 합니다.
공식 저장소 이름을 임의의 제공자 별칭으로 바꾸거나, 비슷한 모델로 자동 대체하지 않습니다.
한 서버에 여러 모델이 있더라도 실제로 선택한 가중치와 요청용 식별자가 일치하는지 확인하십시오.
주소에는 비밀키, 사용자 암호 또는 비밀값을 담은 추가 문자열을 넣지 마십시오.

## 원래 선정한 25개와 공식 가중치 저장소

표의 번호는 예시 주소와 비밀키 변수의 번호에 대응합니다.
전체 목록은 `app/services/open_model_catalog.py`의
`SOURCE_MODEL_CATALOG`와 같은 25개입니다.
아래 요청 방식은 이 앱의 예시 설정이며, 표만으로 실제 서버 제공을 뜻하지 않습니다.

| 번호 | 보존한 선정 이름 | 소유자 통제의 계열 이름 | 공식 가중치 저장소 식별자 | 예시 요청 방식 |
| --- | --- | --- | --- | --- |
| 01 | `DeepSeek-V4-Pro` | `deepseek` | [`deepseek-ai/DeepSeek-V4-Pro`](https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro) | 대화 · `chat` |
| 02 | `DeepSeek-V4-Flash` | `deepseek` | [`deepseek-ai/DeepSeek-V4-Flash`](https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash) | 대화 · `chat` |
| 03 | `DeepSeek-R1` | `deepseek` | [`deepseek-ai/DeepSeek-R1`](https://huggingface.co/deepseek-ai/DeepSeek-R1) | 대화 · `chat` |
| 04 | `DeepSeek-V4.1-Flash` | `deepseek` | [`deepseek-ai/DeepSeek-V4.1-Flash`](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash) | 대화 · `chat` |
| 05 | `Qwen3.6-27B` | `qwen` | [`Qwen/Qwen3.6-27B`](https://huggingface.co/Qwen/Qwen3.6-27B) | 대화 · `chat` |
| 06 | `Qwen3.6-35B-A3B` | `qwen` | [`Qwen/Qwen3.6-35B-A3B`](https://huggingface.co/Qwen/Qwen3.6-35B-A3B) | 대화 · `chat` |
| 07 | `Qwen3.5-397B-A17B` | `qwen` | [`Qwen/Qwen3.5-397B-A17B`](https://huggingface.co/Qwen/Qwen3.5-397B-A17B) | 대화 · `chat` |
| 08 | `Qwen3.8-27B` | `qwen` | [`Qwen/Qwen3.8-27B`](https://huggingface.co/Qwen/Qwen3.8-27B) | 대화 · `chat` |
| 09 | `gpt-oss-20b` | `gpt-oss` | [`openai/gpt-oss-20b`](https://huggingface.co/openai/gpt-oss-20b) | 대화 · `chat` |
| 10 | `gpt-oss-120b` | `gpt-oss` | [`openai/gpt-oss-120b`](https://huggingface.co/openai/gpt-oss-120b) | 대화 · `chat` |
| 11 | `gemma-4-E2B-it` | `gemma` | [`google/gemma-4-E2B-it`](https://huggingface.co/google/gemma-4-E2B-it) | 대화 · `chat` |
| 12 | `gemma-4-E4B-it` | `gemma` | [`google/gemma-4-E4B-it`](https://huggingface.co/google/gemma-4-E4B-it) | 대화 · `chat` |
| 13 | `gemma-4-12B-it` | `gemma` | [`google/gemma-4-12B-it`](https://huggingface.co/google/gemma-4-12B-it) | 대화 · `chat` |
| 14 | `gemma-4-26B-A4B-it` | `gemma` | [`google/gemma-4-26B-A4B-it`](https://huggingface.co/google/gemma-4-26B-A4B-it) | 대화 · `chat` |
| 15 | `gemma-4-31B-it` | `gemma` | [`google/gemma-4-31B-it`](https://huggingface.co/google/gemma-4-31B-it) | 대화 · `chat` |
| 16 | `GLM-5.1` | `glm` | [`zai-org/GLM-5.1`](https://huggingface.co/zai-org/GLM-5.1) | 대화 · `chat` |
| 17 | `GLM-5.2` | `glm` | [`zai-org/GLM-5.2`](https://huggingface.co/zai-org/GLM-5.2) | 대화 · `chat` |
| 18 | `GLM-5.3-Flash` | `glm` | [`zai-org/GLM-5.3-Flash`](https://huggingface.co/zai-org/GLM-5.3-Flash) | 대화 · `chat` |
| 19 | `Mistral-Small-3.2-24B-Instruct-2506` | `mistral` | [`mistralai/Mistral-Small-3.2-24B-Instruct-2506`](https://huggingface.co/mistralai/Mistral-Small-3.2-24B-Instruct-2506) | 대화 · `chat` |
| 20 | `Mistral-Large-3-675B-Base-2512` | `mistral` | [`mistralai/Mistral-Large-3-675B-Base-2512`](https://huggingface.co/mistralai/Mistral-Large-3-675B-Base-2512) | 문장 이어쓰기 · `completion` |
| 21 | `Mistral-Small-4-119B-2603` | `mistral` | [`mistralai/Mistral-Small-4-119B-2603`](https://huggingface.co/mistralai/Mistral-Small-4-119B-2603) | 대화 · `chat` |
| 22 | `phi-4` | `phi` | [`microsoft/phi-4`](https://huggingface.co/microsoft/phi-4) | 대화 · `chat` |
| 23 | `NVIDIA-Nemotron-3.5-Lightning-30B-A3B-BF16` | `nemotron` | [`nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-BF16`](https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-BF16) | 대화 · `chat` |
| 24 | `NVIDIA-Nemotron-3-Nano-4B-BF16` | `nemotron` | [`nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16`](https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16) | 대화 · `chat` |
| 25 | `NVIDIA-Nemotron-3-Super-120B-A12B-BF16` | `nemotron` | [`nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-BF16`](https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-BF16) | 대화 · `chat` |

## 제공자 이름을 연결할 때 보존할 구별

- **DeepSeek 원래 Flash와 새 Flash:** [공식 DeepSeek 모델 목록](https://api-docs.deepseek.com/quick_start/pricing/)에 표시된
  제공자 요청 이름이 가리키는 모델을 먼저 확인하십시오. 기존 `deepseek-v4-flash`
  요청 이름은 원래 버전이 종료된 뒤 V4.1 Flash를 제공하므로, 원래 `DeepSeek-V4-Flash`를
  보존하려면 원래 가중치를 실행하는 서버를 사용해야 합니다. 예시에서
  `deepseek-ai/DeepSeek-V4-Flash`와 `deepseek-ai/DeepSeek-V4.1-Flash`를 분리한 이유입니다.
  현재 제공자 이름 `deepseek-v4-pro`도 `V4-Pro-0813`을 가리키므로 가중치 버전을
  별도로 대조해야 합니다. 종료된 `deepseek-reasoner`를 원래 R1의 현재 연결로 등록하지 않습니다.
- **Mistral Base와 Instruct:** [Large 3 Base 공식 카드](https://huggingface.co/mistralai/Mistral-Large-3-675B-Base-2512)는
  지시를 따르도록 추가 학습하지 않은 기초 모델이라고 명시합니다.
  같은 카드의 실행 예시는 `Instruct-2512`를 가리킵니다.
  이를 Base 실행 근거로 사용하거나 `mistral-large-2512`를 Base의 제공자 식별자로
  자동 등록하지 마십시오. Base 예시는 `completion`과 `/v1/completions`를 사용하며,
  실제 서버가 정확한 Base 가중치를 실행하고 문장 이어쓰기를 지원하는지 확인해야 합니다.
- **Mistral Small 버전:** [Small 4 공식 문서](https://docs.mistral.ai/models/mistral-small-4-0-26-03)의
  제공자 식별자는 `mistral-small-2603`입니다.
  [Small 3.2 공식 문서](https://docs.mistral.ai/models/mistral-small-3-2-25-06)는
  `mistral-small-2506`을 사용 중단 예정 모델로 표시합니다.
  기존 이름이 남아 있다는 이유만으로 현재 계정에서 호출된다고 판단하지 않습니다.
- **NVIDIA Lightning 수치 정밀도:** [선정한 BF16 공식 카드](https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-BF16)는
  수치를 16비트로 보관하는 BF16 가중치입니다.
  [NVIDIA 제공 서비스의 카드](https://build.nvidia.com/nvidia/nemotron-3.5-lightning-30b-a3b/modelcard)는
  수치를 더 작게 줄인 NVFP4 버전입니다.
  `nvidia/nemotron-3.5-lightning-30b-a3b`라는 제공자 이름을 선정한 BF16 항목에
  자동 배정하지 마십시오.
- **NVIDIA의 서버별 요청 이름:** [Nano 4B 공식 카드](https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16)의
  자세한 vLLM 실행 예시는 `nemotron3-nano-4B-BF16`,
  [Super 공식 카드](https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-BF16)는
  `nvidia/nemotron-3-super`를 `--served-model-name`으로 정합니다.
  예시 JSON의 전체 저장소 이름을 유지할지 이 값을 사용할지는 실제 서버 설정에 따라 정하십시오.
- **Qwen 서비스 주소:** Alibaba의 모델 제공 서비스를 사용하더라도 작업공간과 지역에 따라
  실제 주소와 사용 가능한 모델이 달라집니다. 공식 문서의
  [3.6 27B](https://www.alibabacloud.com/help/en/model-studio/qwen3-6-27b),
  [3.6 35B](https://www.alibabacloud.com/help/en/model-studio/qwen3-6-35b-a3b),
  [3.5 397B](https://www.alibabacloud.com/help/en/model-studio/qwen3-5-397b-a17b),
  [3.8 27B](https://www.alibabacloud.com/help/en/model-studio/qwen3-8-27b) 식별자와
  [해당 작업공간의 요청 주소](https://www.alibabacloud.com/help/en/model-studio/qwen-api-via-openai-chat-completions)를
  함께 확인하십시오. 예시에는 임의의 Alibaba 주소를 넣지 않았습니다.
- **GLM 제공자 식별자:** [5.1](https://docs.z.ai/guides/llm/glm-5.1),
  [5.2](https://docs.z.ai/guides/llm/glm-5.2),
  [5.3 Flash](https://docs.z.ai/guides/vlm/glm-5.3-flash)의 공식 제공자 문서는
  `glm-5.1`, `glm-5.2`, `glm-5.3-flash`를 설명합니다.
  제공자가 운영하는 서버와 직접 가중치를 실행하는 서버는 요청용 이름이 다를 수 있습니다.
- **Gemma와 Hugging Face:** Hugging Face는 가중치를 보관하는 곳이기도 하며,
  가중치 페이지가 있다는 이유로 그 회사의 통합 요청 주소를 사용할 수 있는 것은 아닙니다.
  E2B·E4B·12B 공식 페이지에는 연결된 실행 제공자가 없다고 표시됩니다.
  [공식 Gemma API 안내](https://ai.google.dev/gemma/docs/core/gemma_on_gemini_api)는 26B·31B를
  다룹니다. 이 사실을 5개 전부에 같은 주소를 사용할 근거로 삼지 마십시오.
  Hugging Face 통합 요청 주소도 이 예시에 자동 배정하지 않았습니다.
- **gpt-oss 실행 형식:** [20b](https://huggingface.co/openai/gpt-oss-20b)와
  [120b](https://huggingface.co/openai/gpt-oss-120b)는 모델이 사용하는
  대화·도구 표현 규칙인 Harmony를 올바르게 처리하는 실행 도구가 필요합니다.
  가중치 이름만 설정했다고 일반 텍스트 생성 서버가 같은 방식으로 작동하는 것은 아닙니다.

## 소유자 허용과 실제 요청 조건

관리자 화면 `/owner/open-models`, 모델 목록
`GET /owner/open-models/catalog`, 모델에 직접 질문하는
`POST /owner/open-models/call`, 초안 생성
`POST /owner/open-models/draft`는 기존 소유자 인증을 적용합니다.
다음 조건을 모두 만족해야 선택한 모델을 요청할 수 있습니다.

1. `EDURECOVER_OPEN_MODEL_BINDINGS_JSON`에 그 모델의 실제 주소, 식별자,
   비밀키 변수 이름과 요청 방식이 있어야 합니다.
2. 해당 비밀키가 실행 서버 환경에 있고, `EDURECOVER_OPEN_MODELS_ENABLED=true`여야 합니다.
3. 기존 소유자 통제에서 모델 계열을 허용해야 합니다. 선정 목록의 계열은
   `deepseek`, `qwen`, `gpt-oss`, `gemma`, `glm`, `mistral`, `phi`, `nemotron`입니다.
   허용할 계열은 기존 `EDURECOVER_ALLOWED_PROVIDERS` 또는 통제 원장의
   `allowed_providers`에 추가하되, 기존 목록을 통째로 바꾸지 마십시오.
4. 전체 AI 중지(`EDURECOVER_AI_HOLD` 또는 통제 원장의 `hold_all_ai`),
   차단 목록과 검증 전용 제한도 그대로 적용합니다. 이 연결을 위해 기존 중지나
   제한을 자동으로 해제하지 않습니다.

이 코드는 서버 주소와 모델 이름을 추측하지 않으며, 실패했을 때 다른 모델,
다른 회사 또는 Nova로 자동 전환하지 않습니다.
공개 화면이나 응답에는 비밀키, 실제 연결 주소, 계정 정보가 노출되지 않아야 합니다.

## 연결 검사와 실제 응답 수신

`scripts/check_open_model_connections.py`는 기본으로 원래 선정한 25개의
연결 설정을 확인합니다. 기본 실행은 모델 요청을 보내지 않으며,
실제 주소로 바꾼 운영 설정을 준비한 뒤 다음 명령으로 확인합니다.

```bash
python scripts/check_open_model_connections.py
```

모델의 답을 실제로 받는 검사는 `--invoke`를 명시하고 모델을 선택할 때만 실행합니다.
실제 주소·비밀키·모델 접근 권한·소유자 허용이 모두 준비된 경우의 한 모델 예시는 다음과 같습니다.

```bash
python scripts/check_open_model_connections.py --invoke --model-key gpt-oss-20b
```

실제 요청에는 제공자의 사용량과 요금이 발생할 수 있습니다.
예시의 가짜 주소로 검사를 통과시키거나 호출을 시도하지 마십시오.
검사 기록은 비밀값이나 답변 원문을 출력하지 않습니다.
설정 검사 통과, 실제 응답 수신, 정답 일치, 수업 자료의 출처 대조와 의료자료 검토는
각각 다른 확인입니다. 아직 실제로 호출해 보지 않은 모델을 연결 성공으로 표시하지 않습니다.
가중치를 새 자료로 바꾸는 **학습**과 기존 가중치에 질문하는 **API 호출**도 별개입니다.
이 연결 기능은 모델을 학습하거나 새 가중치를 만들어 저장하지 않습니다.

공식 공개 자료 확인 기준: 2026년 10월 15일 UTC.
공식 저장소와 문서의 공개 여부를 확인했으며, 사용자 계정의 실제 응답 수신은 확인하지 않았습니다.

