# Nano 4B BF16 사용자 GPU 서버 운영 준비

우선순위: 기존 27개 모델 연결을 먼저 진행하고 NVIDIA 개인 계정 실행은 모든 기존 작업 뒤로 보류합니다. 종합 추론용 후속 후보는 Super 120B BF16입니다. 이 Nano 구성은 작은 시험 예제로 보관하며 최종 모델 선택·우선순위 변경이나 실제 실행을 뜻하지 않습니다.

이 폴더는 사용자가 보유하거나 사용자 계정으로 임대한 NVIDIA GPU 서버에서 실행할 구성입니다. Chutes API 이용이나 Railway 앱 배포만으로 GPU 모델 서버가 생기지는 않습니다. 이 파일을 저장한 상태는 서버 운영 완료가 아닙니다.

실행 전에 GPU 제공자 계정과 이용 약관, 서버 선택과 비용, 도메인, 전용 인증 키를 확정해야 합니다. 이 구성에는 가입·결제·서버 생성 명령이 없습니다. 아직 GPU에서 모델 다운로드·구동·응답 검사를 수행하지 않았습니다.

## 준비 조건

- Linux, NVIDIA GPU와 호환 드라이버, NVIDIA Container Toolkit, Docker Engine와 Compose의 GPU 지원이 필요합니다.
- 모델은 `nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16` 한 개이며 가중치·토크나이저·사용자 코드 버전을 `dfaf35de3e30f1867dd8dbc38a7fc9fb52d3914f`로 고정했습니다. 다른 모델이나 양자화 모델로 자동 변경하지 않습니다.
- 공식 카드는 A10G·A100·H100·GeForce RTX 및 Linux를 명시하지만 최소 GPU 메모리 숫자는 제시하지 않습니다. BF16 가중치만 약 7.95GB이고 실행 캐시가 추가됩니다. 24GB GPU는 시작 후보에 대한 추정이며 기본 262144 문맥에서 작동한다는 보장이 아닙니다. 메모리가 부족하면 자동 축소하지 않고 실패합니다. 사용자가 선택할 때만 `NEMOTRON_MAX_MODEL_LEN`을 낮출 수 있습니다.
- 모델 카드의 공식 지원 언어는 영어와 코딩 언어입니다. 한국어 품질은 실제 검사 전이며 보장하지 않습니다. 사진·음성 입력 모델이 아닙니다.
- GPU 서버의 공개 DNS 이름과 이를 가리키는 DNS 레코드가 필요합니다. 인증서 발급을 위해 80/443 포트가 도달 가능해야 합니다. 8000 포트는 외부에 열지 않습니다.

## 승인한 GPU 서버에서만 실행

비공개 `.env`에 도메인·인증서 연락 이메일·32자 이상의 무작위 전용 토큰을 설정하십시오. 이 토큰은 NVIDIA 또는 OpenAI에서 발급받는 키가 아니라 사용자의 모델 서버를 보호하는 새 토큰입니다. 앱 Railway 환경의 `EDURECOVER_NEMOTRON_API_KEY`에 동일한 값을 안전하게 등록하십시오. 키를 채팅·저장소·스크린샷·명령 출력에 노출하지 마십시오.

실제 운영을 선택한 뒤 이 폴더에서 `docker compose up --build -d`를 실행하십시오. 이미지 빌드 때 고정된 NVIDIA 공식 parser만 다운로드하고 SHA256을 확인합니다. 모델은 처음 시작할 때 고정 버전으로 다운로드합니다. 공개 저장소이므로 HF 토큰을 요구하도록 설정하지 않았습니다.

앱의 서버 주소는 `https://실제-도메인/v1/chat/completions`, 모델 ID는 `nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16`입니다. URL 자리에 예시 도메인을 등록하지 마십시오. 실제 DNS·TLS·키·모델 목록 확인과 간단한 실제 응답 확인이 끝나기 전에는 연결 완료로 표시하지 않습니다.

실제 GPU 서버·도메인·전용 키를 준비하고 사용자 직접 운영을 확인한 뒤에만 Railway 앱에 다음 환경 변수를 등록하십시오. 네 항목이 없으면 호출 준비가 완료되지 않습니다. 토큰은 비공개 설정 창에서만 등록하십시오.

| 앱 환경 변수 | 설정할 내용 |
|---|---|
| `EDURECOVER_NEMOTRON_SELF_HOSTED_ENABLED` | 직접 운영 연결을 켤 때 `true` |
| `EDURECOVER_NEMOTRON_SELF_HOSTED_CONFIRMED` | 사용자 직접 운영 서버임을 확인한 뒤 `true` |
| `EDURECOVER_NEMOTRON_NANO_ENDPOINT_URL` | `https://실제-도메인/v1/chat/completions` |
| `EDURECOVER_NEMOTRON_API_KEY` | GPU 서버와 동일한 비공개 전용 토큰 |

## 노출과 보관 범위

Caddy가 HTTPS를 처리하며 외부에서 접근 가능한 모델 경로는 `POST /v1/chat/completions`, `GET /v1/models`뿐입니다. 두 경로는 vLLM 전용 토큰 인증을 요구합니다. 관리·메트릭·문서·health 경로를 공개하지 않습니다. 서버 방화벽과 GPU 제공자 관리 계정 보안은 별도 설정 대상입니다.

요청·응답·접근 로그와 vLLM/Hugging Face 사용 통계를 끄고 Docker 로그 저장을 사용하지 않습니다. 기본 설정에서는 `docker compose logs`로 시작 오류도 확인할 수 없습니다. 진단 목적으로 로그를 켜기 전에 입력·응답·인증 헤더가 저장될 가능성을 검토하십시오. 모델 다운로드 캐시와 TLS 인증서는 볼륨에 남습니다. GPU 제공자, 호스트 OS, Railway 앱, 백업·모니터링 설정까지 확인한 것은 아니므로 이 구성만으로 ZDR(입력·응답 무보관)을 보증하지 않습니다.

앱 답변의 기본 출력 토큰 제한을 추가하지 않았습니다. 고정된 모델의 `generation_config.json`에는 `max_new_tokens` 또는 `max_length`가 없습니다. vLLM 0.15.1의 채팅 API는 요청에 출력 길이가 없으면 입력을 제외한 남은 문맥과 실행 플랫폼 한도에서 출력 길이를 계산합니다. 따라서 별도의 16·512·1024 토큰 상한을 추가하지 않습니다. 모델의 종료 토큰, 문맥 총량과 실행 엔진의 처리 한도는 적용되며, 무한한 출력 길이를 뜻하지 않습니다. 실제 GPU 응답에서 중단 여부와 길이를 확인해야 합니다.

## 다음 모델

Lightning 30B BF16는 공식 H100 80GB 또는 A100 80GB 1장 구성이 있습니다. Super 120B BF16는 H100 80GB 8장 또는 B200/B300 2장 구성이 있습니다. 두 모델은 이 Nano 구성에 포함하지 않으며 별도 GPU·실행 버전·메모리 검증 후 추가합니다.

## 공식 근거 (2026-10-17 확인)

- [NVIDIA Nano 4B 공식 모델 카드](https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16)
- [고정 버전 공식 parser](https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16/blob/dfaf35de3e30f1867dd8dbc38a7fc9fb52d3914f/nano_v3_reasoning_parser.py)
- [NVIDIA Nemotron 사용 허가](https://www.nvidia.com/en-us/agreements/enterprise-software/nvidia-nemotron-open-model-license/): 상업 이용과 파생 모델 개발·배포를 허용하며 고지·법령 준수 등 조건이 적용됩니다.
- [vLLM 0.15.1 서버 인자](https://docs.vllm.ai/en/v0.15.1/cli/serve/)
- [vLLM 인증의 범위](https://docs.vllm.ai/en/v0.15.1/usage/security/)
- [vLLM 0.15.1 출력 길이 계산](https://docs.vllm.ai/en/v0.15.1/api/vllm/entrypoints/utils/)
- [고정된 모델의 생성 설정](https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16/blob/dfaf35de3e30f1867dd8dbc38a7fc9fb52d3914f/generation_config.json)
- [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
- [Caddy 자동 HTTPS](https://caddyserver.com/docs/automatic-https)
