# 맥락 이해와 환각 차단 실험

2026년 10월 22일 검토용 첫 구현입니다. 같은 입력과 독립 평가 기록을 비교하는 도구이며 자연어 사실 판정기나 운영 은행은 아닙니다. baseline, prompt, rag, sft, dpo는 기본 모델, 지시문 적용, 검색 자료 적용, 예시 학습 후, 선호 답변 쌍 학습 후의 관측을 구별합니다. 해당 학습을 수행하지 않습니다.

## 기존 구현과 연결

main 1089f23c54b4ef740fc33baf321c28d3c9129d17 기준 추가입니다. 검토 요청 62번의 드림지키미 엔진과 별개입니다. 엔진의 후보와 사용 결과를 Observation으로 변환하는 인증된 연결 및 운영 화면은 미구현입니다. 기존 app/training/eligibility.py와 training/rl/contract.py의 권리·동의·특허 검토·철회 조건을 우회하지 않습니다.

## 실험과 실행

일반 자연수 덧셈의 1+1=2와 물방울 덩어리 수의 1+1=1을 구별합니다. 창의적 표현을 보존하고 단위와 연산을 설명하는지 평가합니다. 논리적 아이디, 실제 모델, 실행 차수, 목표 정정과 자료 변경본을 구별합니다. 같은 폴더 공유는 물리적 분리가 아닙니다. 위험 후보 생성과 실제 위험 실행을 따로 셉니다. 학습과 최종 시험의 자료 가족·중복 입력을 분리하며 누락·실패·미판정을 분모에 남깁니다.

저장소 루트에서 다음 명령을 실행하십시오.

```bash
python -m unittest discover -s tests -p 'test_context_evaluation.py' -v
python scripts/evaluate_context.py --cases CASES.jsonl --observations REVIEWED_OUTPUTS.jsonl --stages baseline prompt rag sft dpo --evidence-kind synthetic --output NEW_REPORT.json
```

각 줄은 Case 또는 Observation 필드의 JSON 객체입니다. Case.model_input()만 모델에 제공하고 expected와 평가 꼬리표는 숨기십시오. 독립 검토자가 원문과 답변을 확인해 Observation을 작성해야 합니다. 모델이 자기 판정이나 review_id를 직접 설정해서는 안 됩니다. live_reviewed 문자열은 실제 시험 증거가 아니며 인증된 기록과 대조해야 합니다.

## 물리적 분리 배치 조건

생성 노드는 제안만 냅니다. 은행·운영 명령 실행 권한과 검토 서명키를 두지 않습니다. 검토 노드는 원문과 후보를 읽으며 목표 변경이나 은행 쓰기를 하지 않습니다. 실행 및 은행 노드는 인증된 검토와 현재 목적·자료·권리·동의 변경본을 소비 직전에 검사하고 원자적으로 저장합니다.

노드를 별도 가상머신 또는 물리 장비에 두고 서비스 계정·쓰기 권한을 분리하십시오. 가상머신과 별도 물리 장비의 분리는 보고에서 구별해야 합니다. 자료 전달 때 출처·정정·권한을 보존하십시오. 이번 환경에는 Docker와 별도 노드가 없어서 배치를 실행하지 않았습니다. 같은 계정의 폴더 분리나 subprocess를 물리적 분리로 보고하지 않습니다. 실제 배치에서 우회 경로, 늦은 응답, 승인 뒤 변경, 재전송 및 에이전트 간 출처 재해석을 시험해야 합니다.

## 은행 등재 전 검사

bank_preflight는 검토한 보고서·현재 상태·정책을 검사하는 순수 함수이며 은행 쓰기나 계약 발급을 하지 않습니다. 모델 성적과 아동 사고유형 관찰은 별도입니다. 공개 기부도 동의·권리·비식별 조건을 통과해야 합니다. 대출·임대·거래·공공목적 무상기부는 각 사용허가를 검사합니다. 데이터 사용허가와 금융 대출은 구별합니다. 등재 뒤 정정·철회 시 실제 후속 사용을 보류하는 운영 연결은 미구현이고 학습 모델의 영향 삭제도 하지 않습니다.

## 공개 기술과 한계

CaMeL은 제어와 비신뢰 데이터를 분리하고 권한을 검사합니다. multi-CaMeL은 에이전트 경계의 출처 재해석 공격과 출처 보존을 다룹니다. Ragas는 검색 자료와 답변의 일치, FActScore는 작은 사실 주장, RAGTruth는 환각 주석 자료를 제공합니다. Promptfoo는 지시문 검사, Inspect는 과제·실행·채점·로그, LM Evaluation Harness는 동일 조건 모델 비교, TRL은 선호 답변 쌍의 실제 학습에 참고합니다. 이들을 설치·연동하거나 성능 시험했다고 주장하지 않습니다.

- https://arxiv.org/abs/2503.18813
- https://arxiv.org/abs/2610.05640
- https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/faithfulness/
- https://aclanthology.org/2023.emnlp-main.741/
- https://aclanthology.org/2024.acl-long.585/
- https://www.promptfoo.dev/docs/configuration/expected-outputs/
- https://inspect.aisi.org.uk/
- https://github.com/EleutherAI/lm-evaluation-harness
- https://huggingface.co/docs/trl/en/dpo_trainer

공개 기술의 존재와 전체 청구항의 동일성은 다릅니다. 신규성·진보성 판단이나 최초 접수 원문 대조를 완료하지 않았습니다.
