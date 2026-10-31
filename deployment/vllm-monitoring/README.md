# vLLM 대시보드 자동 설정

설정 파일을 시작할 때 자동으로 읽는 방식(프로비저닝)을 적용했습니다.
여러 프로그램을 함께 실행하는 Docker Compose를 시작하면 측정값 수집 프로그램인
Prometheus와 그래프 표시 프로그램인 Grafana가 함께 실행됩니다.
측정자료 연결과 공식 vLLM 대시보드를 자동 등록하므로 화면 구성 파일(JSON)을
복사하거나 Grafana 화면에서 가져올 필요가 없습니다.

## 실행

Docker Engine과 Compose v2가 설치된 **기존 vLLM 서버**의 공식 저장소에서 실행하십시오.

```bash
docker compose -f deployment/vllm-monitoring/compose.yaml up -d
```

기본 측정값 주소는 `http://host.docker.internal:8000/metrics`입니다.
vLLM은 해당 서버의 Docker 연결 주소에서 접근할 수 있어야 합니다.
vLLM을 호스트에서 실행한다면 Docker가 접근 가능한 주소에 연결되어 있어야 합니다.
다른 서버의 vLLM을 쓰는 경우 실제 측정값 주소를 한 번 지정하십시오.

```bash
VLLM_METRICS_URL=http://실제-vllm-주소:8000/metrics \
  docker compose -f deployment/vllm-monitoring/compose.yaml up -d --force-recreate
```

같은 값을 유지하려면 이 폴더의 `.env`에 `VLLM_METRICS_URL=실제주소`를 저장하고
아래처럼 파일을 명시해 실행하십시오. 이 파일에는 인증키나 비밀번호를 넣지 마십시오.
URL 안의 인증정보는 허용하지 않습니다.

```bash
docker compose --env-file deployment/vllm-monitoring/.env \
  -f deployment/vllm-monitoring/compose.yaml up -d --force-recreate
```

HTTPS 주소는 기본 인증서 검사를 유지합니다.

이 구성은 이미 실행 중인 vLLM의 측정값을 연결합니다. 모델을 다운로드하거나,
그래픽 연산 장치(GPU)를 임대하거나, 첨부 화면의 70B 모델·4개 장치 설정을 적용하는
구성은 포함하지 않습니다. 현재 EduRecover 웹 앱의 실행 명령도 변경하지 않습니다.

## 처음 접속

- Grafana 주소: http://localhost:3000/d/edurecover-vllm
- 사용자 이름: `admin`
- 관리자 비밀번호는 최초 실행에서 무작위로 생성되며 재실행 때 그대로 보존됩니다.

비밀번호는 아래 명령으로 해당 서버의 터미널에서만 확인하십시오.
이 명령은 설정 프로그램을 다시 실행하지 않으며, 값은 일반 로그에 출력하지 않습니다.

```bash
docker compose -f deployment/vllm-monitoring/compose.yaml run --rm --no-deps \
  --entrypoint cat configure /credentials/grafana_admin_password
```

두 웹 포트는 기본적으로 해당 서버 안에서만 열립니다. 원격 접속은 기존 인증된
접속 경로 또는 SSH 터널을 사용하십시오. 다른 포트를 쓰려면 `GRAFANA_PORT`와
`PROMETHEUS_PORT`를 지정하십시오. 초기 로그인 이후 비밀번호를 화면에서 바꿨다면
파일의 초기 비밀번호가 아닌 변경한 비밀번호를 사용하십시오.

## 자동 처리 순서

1. `configure`가 측정값 주소를 확인하고 Prometheus 설정 파일을 만듭니다.
2. 최초 관리자 비밀번호를 생성하거나 기존 비밀번호 파일을 보존합니다.
3. 초기 설정이 성공한 뒤 Prometheus와 Grafana가 시작됩니다.
4. Grafana가 고정 연결 식별자 `edurecover-vllm-prometheus`를 자동 등록합니다.
5. 공식 대시보드를 해당 연결로 자동 등록합니다. GitHub 다운로드나 수동 가져오기는 없습니다.

일반 재시작과 `down` 이후 재실행에서는 Docker의 영속 저장 공간인 볼륨을 재사용합니다.
측정 이력은 최대 7일 또는 1GB 기준으로 정리합니다.
다음 명령은 프로그램만 중단하고 볼륨을 보존합니다.

```bash
docker compose -f deployment/vllm-monitoring/compose.yaml down
```

`down -v`는 비밀번호·Grafana 저장 내용·측정 이력을 삭제하므로 일반 중지에 사용하지 마십시오.
측정값 주소를 바꾼 뒤에는 위의 `up -d --force-recreate`로 다시 시작하십시오.
단순 `restart`는 변경된 환경변수를 적용하지 않습니다.

## 클로드 검증 인계

사용자 지시: “당장 자동세팅방식 설정해. 검증은 클로드가 한다.”

이 변경은 설정 구현입니다. 실제 Docker 기동, 로그인, 수집 성공, 그래프 표시와
모델 응답은 아직 검증하지 않았습니다. 작성 환경에는 Docker가 설치돼 있지 않습니다.
이 구성의 배포 대상 서버와 실제 vLLM 주소도 확정하지 않았습니다.

클로드가 이어서 확인할 항목:

1. `docker compose -f deployment/vllm-monitoring/compose.yaml config --quiet`로 설정 문법 확인.
2. 첫 기동에서 `configure` 종료코드 0, 두 서비스 실행과 이미지 버전 확인.
3. 수동 등록 없이 측정자료 연결과 `/d/edurecover-vllm` 대시보드가 생성되는지 확인.
4. Prometheus의 `up{job="vllm"}`이 1인지 확인하고, 실제 모델 요청 후 그래프가 바뀌는지 확인.
5. 사용 중인 vLLM 버전의 측정 항목 이름과 공식 대시보드 질의가 일치하는지 확인.
6. `down`과 재실행 후 초기 비밀번호·저장 내용 보존 및 중복 대시보드 미생성 확인.
7. 잘못된 주소에서 성공으로 표시되지 않는지, 주소 변경 후 재생성으로 적용되는지 확인.

검증 보고는 설정 검사·서비스 기동·실제 모델 응답을 각각 구분하십시오.
대시보드가 열려도 실제 측정값 수집이나 모델 실행 성공을 의미하지 않습니다.
클로드에게 직접 전송했다는 의미는 아니며, 이 파일과 변경 검토 요청을 인계 자료로 사용합니다.

## 공식 근거

- [vLLM 공식 예제](https://docs.vllm.ai/en/latest/examples/observability/prometheus_grafana/)
- [Grafana 자동 설정](https://grafana.com/docs/grafana/latest/administration/provisioning/)
- [Docker Compose 시작 순서](https://docs.docker.com/compose/how-tos/startup-order/)
- 원본 버전과 수정 범위: [UPSTREAM.md](UPSTREAM.md)
