# gpt-oss 기반모델 사용정책

기준일: 2026-09-26

## 1. 지위

EduRecover의 초기 자체학습 기반모델 후보로 OpenAI `gpt-oss-20b`를 사용할 수 있다.

OpenAI 공식 문서 기준:

- gpt-oss는 OpenAI API/ChatGPT에서 제공되는 폐쇄형 모델과 별개의 오픈 가중치 모델이다.
- 사용자가 통제하는 인프라 또는 호스팅 제공자에서 실행할 수 있다.
- 오픈 도구를 사용해 파인튜닝/적응할 수 있다.
- Apache 2.0 라이선스로 제공되며 상업적 사용·수정·재배포가 폭넓게 허용된다.
- gpt-oss-20b는 약 21B total / 3.6B active parameter 모델이며, OpenAI는 약 16GB 메모리에서의 추론 배치를 안내한다.
- gpt-oss-120b는 약 117B total / 5.1B active parameter이며 단일 80GB GPU급 실행을 목표로 한다.

공식 참고:
- https://help.openai.com/en/articles/11870455-openai-open-weight-models-gpt-oss
- https://openai.com/index/introducing-gpt-oss/
- https://developers.openai.com/cookbook/topic/gpt-oss

실제 배포 시점에는 모델 카드, gpt-oss usage policy, 라이선스 전문을 다시 검토한다.

## 2. 소유권/경계

EduRecover가 소유·통제해야 하는 핵심 자산:

- 자체 데이터셋 및 dataset snapshot
- 산파식 교수 trajectory 구조
- 오개념/원인가설 구조
- 보상·평가 정책
- 승격/rollback 정책
- EduRecover 자체 코드
- 자체 생성 adapter/checkpoint 및 그 메타데이터
- 특허 및 영업비밀

gpt-oss 원본 가중치 자체를 독점 소유한다고 표현하지 않는다. 원본은 해당 라이선스에 따라 사용하는 기반모델이다.

## 3. 폐쇄형 OpenAI API와의 분리

OpenAI API는 개발·검증·서비스 기능에 사용할 수 있으나, 관리형 API/App의 요청·출력을 EduRecover 자체 경쟁모델 훈련으로 자동 유입시키지 않는다.

training plane에는 `THIRD_PARTY_API_OUTPUT` 및 `CHATGPT_APP_REQUEST` 출처를 기본 REJECTED로 처리하는 경계를 유지한다.

## 4. 모델 교체성

코드는 `BaseModelSpec`으로 모델을 추상화한다.

따라서 다음 변경이 가능해야 한다.

```text
gpt-oss-20b
    -> gpt-oss-120b
    -> 향후 OpenAI 오픈 가중치 모델
    -> 다른 라이선스 적합 오픈 가중치 모델
```

데이터·교수정책·평가체계가 특정 모델 공급자에 종속되어서는 안 된다.

## 5. 초기 학습전략

첫 실험은 전체 파라미터 학습보다 adapter 기반 SFT/QLoRA 계열을 우선 검토한다.

이유:
- 비용 및 GPU 요구량을 낮출 수 있음
- 여러 실험 버전을 독립 보관하기 쉬움
- 기반모델 교체 실험이 상대적으로 용이함

정확한 학습 hyperparameter는 초기 작은 검증세트로 결정하고, 영업비밀 정책에 따라 공개 저장소 문서에 고정하지 않는다.

## 6. 승격 금지

학습 성공 = 서비스 승격이 아니다.

후보 checkpoint는 다음이 모두 확인되기 전 ACTIVE로 전환하지 않는다.

- 데이터 권리
- dataset hash
- 학습 recipe hash
- held-out 평가
- 산파식 정답 조기노출 방지
- 기존 기능 regression 부재
- 개인정보/비식별화
- owner 승인 정책

하나라도 실패하면 HOLD한다.
