# AWS 단일 서버 이식 실행 구성

현재 파일은 실행 가능한 배포 구성이다. **AWS 계정 조회·리소스 생성·모델 호출·Railway 변경·서비스 전환은 아직 실행하지 않았다.** 기존 코드·자료·계정과 `DREAMKEEPER-001` 책임체계를 이어받는다. 앱은 EC2 한 대에서 `uvicorn app.main:app --workers 1`로 실행한다. SQLite를 여러 서버/worker가 공유하는 배포는 이 구성의 대상이 아니다.

## 고정된 보존·접속 조건

| 항목 | 실제 구성 |
|---|---|
| EBS 데이터 | 암호화 `gp3`, 기본 5 GiB, 삭제·교체 시 `Retain` |
| 호스트 EBS mount | `/var/lib/edurecover` |
| 복원 대상 | `/var/lib/edurecover/state` (복원 전 없어야 함) |
| 복원 검증 manifest | `/var/lib/edurecover/state.edurmig-manifest.json` (state의 **sibling**) |
| 컨테이너 실제 bind mount | `/var/lib/edurecover/state` → `/app/.edurecover_classroom_state` |
| 앱 상태 설정 | `EDURECOVER_CLASSROOM_STATE_DIR`와 `EDURECOVER_PERSISTENT_MOUNT_PATH` 모두 `/app/.edurecover_classroom_state` |
| 관리 접속 | SSM만 사용, 보안그룹 inbound 규칙 0개, SSH 키·공개 도메인 없음 |
| 앱 포트 | 호스트 `127.0.0.1:8000`에만 bind, SSM 터널로 확인 |
| 기본 outbound | 기존 IGW 경로 + outbound용 public IPv4. 이는 앱 공개가 아니다. IPv4·EC2·EBS 사용료는 실제 실행 후 발생한다. |
| NAT 대안 | `outbound_mode=existing-nat`: 기존 정상 NAT만 사용. 이 스택은 NAT/IGW/VPC를 만들지 않는다. |
| 비밀값 | 외부 기존 Secrets Manager ARN의 **명시 VersionId**를 메모리로 읽는다. 새 Secret·암호키·rotation 없음 |
| AWS 인증 | EC2 instance role. 기존 Secret에 든 static AWS 키는 원본 저장소에서 보존하되 runtime 주입에서 제외 |
| 실행 권한 | Bedrock 읽기만 기본 허용. 정확한 승인 리소스 ARN과 `allow_approved_bedrock_runtime=true`가 있어야 `InvokeModel` 권한 추가 |
| ZDR | 사용자 최신 일시 해제 지시로 앱 `EDURECOVER_REQUIRE_ZDR=false`. 실제 AWS 보관정책 변경·ZDR 확인·신규 승인으로 해석하지 않는다. |
| 소유자 통제 | 원본 `EDURECOVER_AI_HOLD`, 허용/차단 원장, 출처·검증·라우팅 조건 그대로. 추가 전면 HOLD는 만들지 않는다. |
| 자체 AI | 모델 설치·GPU 생성·학습 실행·Amazon Nova 자동선택 없음. 실제 training receipt 기능은 비활성 유지 |

Docker용 제외 규칙의 실제 자동선택 파일은 `Dockerfile.aws.dockerignore`다. `.dockerignore.aws`는 같은 내용의 검토용 사본이다. 원문 prompt/response 및 실제 키 값을 배포 로그에 남기지 않는다. 컨테이너 access log도 비활성이다.

오프라인 검증은 `python -m pytest tests/test_aws_deployment.py`, `bash -n deployment/aws/bootstrap.sh`, `cfn-lint deployment/aws/cloudformation.json`으로 실행한다. AWS 실계정이나 키 없이 동작한다. AWS 배치 리전은 기존 Secret의 오래된 `AWS_REGION`보다 우선하며 Secret 원본 값은 바꾸지 않는다.

## 실행 전 필요한 실제 값

`config.example.json`을 계정별 설정 파일로 복사한다. `null`인 필수 값은 **현재 미확인** 표시이며, 채워지지 않으면 스크립트가 `MISSING_REQUIRED_FIELDS`로 종료한다. 가짜 VPC·AMI·ECR·Secret ID로 진행하지 않는다.

- 같은 AWS 계정·서울 리전의 VPC, subnet, Availability Zone, 공식 Amazon Linux 2023 x86_64 AMI.
- 기존 private ECR repository ARN, 빌드 결과의 `@sha256:` 고정 image URI.
- 원본 환경변수를 이름→문자열 JSON으로 보존한 **기존** Secret ARN과 원본 VersionId. DB 암호화키·백업키·서명키·가명 salt·소유자 인증값 등 원본을 바꾸거나 새로 만들지 않는다.
- 고객관리 KMS로 Secret을 암호화했다면 해당 정확한 `secret_kms_key_arn`.
- 재사용 EBS라면 detached/available 상태, 같은 AZ, 암호화·ext4를 확인한 `existing_data_volume_id`. 기존 볼륨은 절대 포맷하지 않는다.
- `EDURECOVER_BEDROCK_CLAUDE_INFERENCE_PROFILE` 및 `EDURECOVER_BEDROCK_OPENAI_INFERENCE_PROFILE`의 실제 사용가능한 프로필은 기존 Secret 환경맵에서 지정한다. `EDURECOVER_PROVIDER_TRANSPORT=bedrock`을 강제하여 GPT/Claude 직접 API fallback을 허용하지 않는다. Gemini enterprise client는 기존 구현을 따른다.

운영 PC에는 AWS CLI v2, Docker, Session Manager plugin이 필요하다. 배포 담당자에게만 CloudFormation/IAM PassRole/ECR push/SSM RunCommand 관련 권한이 필요하다. 서버 role에는 지원 관리용 `AmazonSSMManagedInstanceCore`, 해당 ECR pull·기존 Secret 읽기·Bedrock 읽기만 기본 부여한다. Bedrock Invoke의 정확한 프로필 및 그 기반 모델 ARN을 검토하기 전 광범위 runtime 권한을 추가하지 않는다.

## 순서

1. **로컬 설정 확인** — AWS 호출 없이 필수 값과 ARN·리전·digest·옵션을 검증한다.

   ```bash
   python deployment/aws/launch.py --config migration-aws.json --validate-local
   ```

2. **이미지 빌드·private ECR push** — 실제 repository URI와 변경된 소스의 고유 tag를 인자로 전달한다. `--repository-uri` 및 `--image-tag`가 없으면 종료한다. `.env`·상태 DB·키 파일은 context에서 제외하고, 복사 대상 텍스트의 평문 키 패턴을 검사한다. 출력된 immutable image URI를 `migration-aws.json`에 반영한다. 이 명령은 이미지 push를 실행하며 EC2를 생성하지 않는다.

   ```bash
   python deployment/aws/deploy.py --build-and-push --source-root . --repository-uri "$ECR_REPOSITORY_URI" --image-tag "$SOURCE_REVISION_TAG"
   ```

3. **미실행 change set 작성** — CLI·계정·VPC/subnet/AZ·DNS·기존 IGW/NAT 경로·AMI·ECR digest·Secret VersionId·기존 EBS를 읽기 검증한다. 기존 같은 이름의 stack을 자동 update하지 않는다. 이어 CloudFormation `CREATE` change set만 작성한다. 아직 EC2/EBS/app 실행은 없다.

   ```bash
   python deployment/aws/launch.py --config migration-aws.json --plan
   ```

4. **검토한 change set 실행** — 검토한 정확한 change-set ARN 및 `--confirm-billed-resources`가 모두 있어야 리소스를 생성한다. AWS 실제 접근이 차단된 현재 작업에서는 이 단계가 실행되지 않았다.

   ```bash
   python deployment/aws/launch.py --config migration-aws.json --execute-change-set "$REVIEWED_CHANGE_SET_ARN" --confirm-billed-resources
   ```

   생성된 호스트는 Docker·SSM·EBS만 준비한다. `edurecover-aws.service`는 **중지·비활성** 상태다. CloudFormation 완료와 SSM 온라인, bootstrap의 `AWS_PRIVATE_HOST_READY_APPLICATION_NOT_STARTED`를 확인해야 한다.

5. **원본 데이터 복원** — 기존 Railway 쓰기를 일시 정지한 시점의 원본을 `scripts/aws_migration_snapshot.py`로 추출·암호화·검증한다. 원본 서버 자체를 중지했다고 추정하지 않는다. 승인한 비공개 전송 경로로 암호화 archive를 호스트 `/var/lib/edurecover-transfer/state.edurmig`에 옮긴 뒤 아래를 **SSM 관리 세션 안에서** 실행한다. 원문 archive와 임시 복원 암호파일 모두 소유자만 접근하도록 보호한다. `/var/lib/edurecover/state`와 sibling manifest가 이미 존재하면 복원은 거부된다.

   AWS 이미지에는 `scripts/aws_migration_snapshot.py`와 `cryptography` 및 `deployment/aws/restore_from_file.py`가 함께 들어있다. 따라서 AL2023 호스트에 Python package를 추가 설치할 필요가 없다. passphrase는 `/run`의 임시 private 파일을 readonly로 mount하며 Docker 인자·Docker 환경변수·로그에 넣지 않는다. 복원 컨테이너의 network는 `none`이다.

   ```bash
   set -euo pipefail
   trap 'sudo rm -f /run/edurecover-restore/passphrase' EXIT INT TERM
   sudo systemctl stop edurecover-aws.service
   sudo test -f /var/lib/edurecover-transfer/state.edurmig
   sudo test ! -L /var/lib/edurecover-transfer/state.edurmig
   sudo chmod 600 /var/lib/edurecover-transfer/state.edurmig
   sudo install -d -m 700 /run/edurecover-restore
   sudo bash -c 'set +x; umask 077; read -rs -p "원본 migration 암호: " migration_pass; printf "%s" "$migration_pass" > /run/edurecover-restore/passphrase; unset migration_pass'
   image_uri=$(sudo cat /etc/edurecover/image-uri)
   aws_region=$(sudo cat /etc/edurecover/region)
   registry=$(printf '%s' "$image_uri" | cut -d / -f 1)
   aws ecr get-login-password --region "$aws_region" | sudo docker login --username AWS --password-stdin "$registry"
   sudo docker pull "$image_uri"
   sudo docker run --rm --network none --read-only --user 0:0 \
     --security-opt no-new-privileges --cap-drop ALL --cap-add DAC_OVERRIDE \
     --tmpfs /tmp:rw,noexec,nosuid,size=256m \
     --mount type=bind,src=/var/lib/edurecover,dst=/restore \
     --mount type=bind,src=/var/lib/edurecover-transfer/state.edurmig,dst=/transfer/state.edurmig,readonly \
     --mount type=bind,src=/run/edurecover-restore/passphrase,dst=/run/restore-passphrase,readonly \
     --entrypoint python "$image_uri" -m deployment.aws.restore_from_file
   sudo rm -f /run/edurecover-restore/passphrase
   trap - EXIT INT TERM
   ```

   원본 계정·자료 ID·암호키를 재생성하지 않는다. 복원 성공 시 호스트 `/var/lib/edurecover/state.edurmig-manifest.json`와 `verified_staging_restore` receipt가 생긴다. 컨테이너 출력의 `/restore/...`는 위 host mount에 대응한다. migration archive에는 환경변수 비밀값이 들어있지 않으므로 기존 Secret 보존은 별도 필수다. 전체 서비스 복원 검증은 다음 단계에서 별도로 진행한다.

   복원 컨테이너만 `DAC_OVERRIDE`를 추가한다. 이미 UID10001/0700으로 보호한 EBS mount에 root 복원 도구가 접근하는 데 필요한 권한이며, 네트워크는 없다. 실제 앱 컨테이너는 UID10001·`--cap-drop ALL`을 유지한다.

6. **검증 후 단일 서버 기동** — 아래 명령은 SSM으로 서비스를 먼저 정지하고, 모든 manifest 파일의 경로·SHA-256·크기·SQLite `integrity_check`·계정상태를 검증한다. 누락·추가 파일·symlink가 있으면 종료한다. **전수 검증 후에만** state root·하위 디렉터리/파일을 UID/GID 10001에 맞춰 `0700/0600`으로 조정한 뒤 앱을 시작한다. 파일 내용·계정 ID·암호키를 바꾸지 않는다. 결과는 SSM command ID이며 실제 성공은 별도 조회해야 한다.

   ```bash
   python deployment/aws/deploy.py --config migration-aws.json --start-restored-host --confirm-start-restored-host
   ```

7. **비공개 확인** — SSM 터널을 열고 운영 PC의 `http://127.0.0.1:8000/health`, 소유자 로그인, 기존 자료·계정·암호화 데이터 해독을 확인한다. 터널 대상 서버가 잘못된 Secret ARN/VersionId이면 시작하지 않는다. prelaunch 동안 백업 scheduler는 중복 실행 방지를 위해 비활성이다. 실제 backup/cutover는 원본 보존과 별도 검토 후 처리한다.

   ```bash
   python deployment/aws/deploy.py --config migration-aws.json --port-forward
   ```

위 단계의 원본 해시·SQLite 성공만으로 전체 서비스 복원·모델 호출·ZDR·공개 전환 성공을 보고하지 않는다. `full_service_restore_verified=false`는 실제 소유자 인증·읽기·쓰기를 검증하기 전 유지한다. Railway의 변수·배포·도메인·자료는 이 도구가 수정하지 않는다.

## 삭제·교체와 데이터

데이터 EBS는 삭제 및 교체 모두 Retain이다. 스택 삭제·인스턴스 교체 전에 앱을 중지하고 파일시스템을 안전하게 unmount해야 한다. Retain은 backup을 대체하지 않으며 재사용 볼륨의 AZ를 바꿀 수 없다. 보존된 EBS는 이후 `existing_data_volume_id`로 다시 연결할 수 있다. 앱을 public bind·공개 도메인으로 바꾸는 코드는 없다.

## 공식 근거

- [CloudFormation EBS VolumeAttachment 및 같은 AZ·unmount 요구](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-ec2-volumeattachment.html)
- [CloudFormation DeletionPolicy](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-attribute-deletionpolicy.html)
- [CloudFormation UpdateReplacePolicy](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-attribute-updatereplacepolicy.html)
- [SSM Session Manager 포트 전달](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-sessions-start.html)
- [Amazon Linux 2023 Docker 설치](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-docker.html)
