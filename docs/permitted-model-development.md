# 자체 AI 개발·학습용 모델 경로

검토일: 2026-10-17 (한국 시간)

## 목적과 우선순위

소유자가 모델의 응답을 활용하여 자체 AI를 개발하거나 다른 모델을 학습시키는 데 필요한 권리를 먼저 확인한다. 입력·응답을 제공자가 보관하지 않는 조건인 ZDR은 이번 경로의 선정 조건으로 요구하지 않고 후속 검토로 미룬다. 직접 호출한다는 사실이나 공개 모델이라는 이름만으로 경쟁 모델 개발 제한이 없다고 판정하지 않는다.

기존 OpenRouter 경로와 Chutes 경로는 유지한다. 이번에 추가하는 `/owner/open-models/learning`은 기존 경로와 별도로, 공식 API의 출력 이용 허용을 확인한 항목과 공개 가중치를 직접 운영할 때의 이용 권한을 확인한 항목을 모아 표시한다. 공개 가중치는 모델을 구성하는 수치 자료이며, 이를 사용할 권리와 외부 사업자의 API를 사용할 계약은 서로 다르다.

## DeepSeek 공식 API: 다른 모델 학습·증류 명시 허용

DeepSeek [Open Platform Terms of Service](https://cdn.deepseek.com/policies/en-US/deepseek-open-platform-terms-of-service.html)는 2026-04-22 공개, 2026-04-29 시행이다. 제4.2조는 관련 법령과 약관을 준수하는 조건으로 입력·출력의 파생제품 개발 및 다른 모델 학습에의 사용을 허용하며, 모델 증류도 예로 든다. 증류는 한 모델의 응답 등을 이용해 다른 모델을 학습시키는 방법이다.

API 특약이 연결하는 [일반 이용약관](https://cdn.deepseek.com/policies/en-US/deepseek-terms-of-use.html)(2026-03-27 갱신) 제4.2조에도 같은 허용이 있다. 일반약관 서문은 특정 기능의 약관과 충돌할 경우 해당 특정약관이 우선한다고 규정한다. 따라서 확인한 두 약관 사이에서 다른 모델 학습 허용이 상충하는 상황은 발견하지 못했다.

이는 법령·제3자 권리·개인정보·상표 등에 관한 의무까지 없어진다는 뜻이 아니다. 일반약관 제3.6조의 지식재산권 침해·불공정경쟁 금지도 남아 있다. 다만 이를 다른 모델의 개발 자체를 일괄 금지하는 조항으로 표시하지 않는다.

새 경로의 공식 API 연결 대상은 다음 두 항목이다. 이 두 요청은 Railway의 EduRecover 서버가 DeepSeek 공식 서버로 직접 전송하며 OpenRouter와 Chutes를 거치지 않는다.

| 화면에서 선택하는 이름 | 공식 API에 보내는 모델 식별값 |
| --- | --- |
| DeepSeek-V4.1-Flash | `deepseek-flash` |
| DeepSeek-V4-Pro | `deepseek-v4-pro` |

서버의 전용 인증 변수는 `DEEPSEEK_API_KEY`이다. 기존 인증정보를 재사용하며, 모델 표시 이름만 같다고 다른 제공자나 다른 버전으로 대체하지 않는다. 이 공식 API 연결에는 이번 작업에서 ZDR을 요구하지 않는다.

## 공개 가중치 27개 항목과 외부 API 계약의 구별

공개 가중치 27개 항목은 각 모델의 정확한 배포 저장소와 라이선스에 근거하여 별도로 표시한다. 모델별 출처와 조건은 `app/services/training_model_policy.py`와 `docs/permitted-model-inventory.json`의 목록에 기록한다. 이 숫자는 현재 실행 가능한 외부 API가 27개 있다는 뜻이 아니다.

- [Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0)은 해당 자료의 수정·파생물 제작·배포 권한을 부여한다. 배포 시 라이선스, 변경 사실, 원저작자 고지 및 해당하는 NOTICE를 보존해야 한다.
- [MIT License](https://opensource.org/license/mit)는 사용·수정·배포·판매 등을 허용하며 저작권·허용 고지 보존을 요구한다.
- [OpenMDW 1.1](https://openmdw.ai/license/1-1/)은 모델 자료에 대한 이용 권한을 부여하고, 해당 모델의 출력 사용·수정·공유에는 그 라이선스상의 제한이나 의무를 부과하지 않는다고 명시한다. 적용 버전은 각 모델의 실제 라이선스를 따른다.
- NVIDIA 모델은 이름이나 계열만 보고 OpenMDW로 묶지 않는다. 예를 들어 [Nemotron 3 Super 120B 공식 카드](https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-BF16)는 [NVIDIA Nemotron Open Model License](https://www.nvidia.com/en-us/agreements/enterprise-software/nvidia-nemotron-open-model-license/)를 연결한다. 이 조건은 상업이용과 파생물 개발·배포를 허용하지만, NVIDIA의 별도 API나 실행 소프트웨어 계약까지 대신하지 않는다.

공개 가중치를 직접 내려받아 운영하는 경우와 OpenRouter·Chutes·그 밖의 외부 API에서 응답을 받는 경우를 구분한다. 외부 API를 연결하려면 그 사업자의 계약에서 해당 출력의 학습 이용도 확인해야 한다. ZDR을 충족한다는 사실은 이러한 이용 권한을 대신하지 않는다. Chutes는 이번 명시 허용 목록의 확정 API 경로로 옮기지 않는다.

## 연결 상태와 이번 작업의 범위

`/owner/open-models/learning/catalog`는 모델의 권리 검토 범위, 근거와 검토일, 연결 상태를 제공한다. 실제 질문은 `/owner/open-models/learning/call`로 전송한다. 기존 OpenRouter 설정을 새 경로의 대체 연결로 사용하지 않는다.

공개 가중치의 권리를 확인했더라도 해당 모델을 실제 실행하는 자체 서버가 없으면 `not_configured`, 즉 연결 설정 필요로 표시한다. 라이선스 확인만으로 호출 설정 완료나 응답 성공을 표시하지 않는다. 공식 API도 전용 인증정보와 기존 소유자 사용 허용 조건이 충족되어야 호출할 수 있다.

이번 변경에 포함된 모든 모델에 대해 실제 응답을 새로 요청하여 검증한 것은 아니다. 연결 설정 검사·자동화 검사와 실제 유료 요청의 성공 여부는 구별한다. 과거에 확인된 DeepSeek 응답을 다른 모델이나 다른 경로의 성공 증거로 확장하지 않는다.

이번 작업은 별도 화면과 호출 범위를 구성하는 데 한정한다. 유료 GPU 서버의 구매·임대나 새 유료 서비스 가입을 진행하지 않는다. 음성 입력의 중복 문제 수정도 사용자 지시에 따라 마지막 작업으로 보류한다. 모델이 작성한 코드를 실행하거나 모델 학습을 시작하지 않는다.

이 문서는 검토 시점의 약관과 배포 라이선스에 따른 구현 근거이다. 모든 목적의 이용 적법성이나 향후 약관 불변을 보장하지 않으며, 새로운 제공자 또는 경로를 추가할 때 해당 계약을 다시 확인한다.
