# vLLM 대시보드 원본과 변경 기록

원본 프로젝트: vllm-project/vllm  
고정한 변경 기록: `e4db46c79bca354fef07197465faea14ca563c09`  
원본 파일 식별값: `6144f2bfd3adf44db0f335afce6bd61db7c8dfae`  
원본: https://github.com/vllm-project/vllm/blob/e4db46c79bca354fef07197465faea14ca563c09/examples/observability/prometheus_grafana/grafana.json  
사용허가: Apache License 2.0. 전체 원문은 [LICENSE.vllm](LICENSE.vllm)에 포함했습니다.

이 파일은 공식 예제를 EduRecover용으로 수정한 것이며 vLLM 프로젝트가 승인한
별도 제품이나 배포 인증을 뜻하지 않습니다. 2026년 10월 24일 다음 항목을 변경했습니다.

- 대시보드 식별자를 `edurecover-vllm`, 제목을 `EduRecover vLLM`으로 고정했습니다.
- 서버 내부 일련번호 `id`를 비워 다른 Grafana에서도 생성할 수 있게 했습니다.
- Prometheus 연결 참조를 `edurecover-vllm-prometheus`로 고정하고 불필요한 연결 선택 변수를 제거했습니다.
- 원본 작성자의 장비 경로가 들어간 모델 선택 초기값을 비웠습니다.
- 자동 갱신은 15초, 화면 직접 편집은 비활성으로 설정했습니다.
- 이 변경 사실을 JSON의 설명 필드에도 기록했습니다.
- 원본의 측정값 질의와 패널 계산식은 변경하지 않았습니다. 사용 중인 vLLM 버전과의 일치는 클로드 검증 대상입니다.

인용된 `examples/production/grafana/dashboard.json` 대신 현재 확인한 공식 파일을 사용했습니다.
대시보드 원문은 저장소에 포함되어 있으며 실행할 때 GitHub에서 다시 받지 않습니다.

서비스 이미지는 Grafana `13.2.4`, Prometheus `v3.15.1`을 사용합니다.
버전은 공식 발표에서 확인했으며 실제 이미지 내려받기와 기동은 검증하지 않았습니다.
초기 설정용 Python 이미지는 `python:3.13-alpine`이며 패치 수준이 바뀔 수 있습니다.

- https://github.com/grafana/grafana/releases/tag/v13.2.4
- https://github.com/prometheus/prometheus/releases/tag/v3.15.1

