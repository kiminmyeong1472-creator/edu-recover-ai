#!/usr/bin/env bash
# CloudFormation embeds this script, substituting only validated resource IDs.
set -euo pipefail
umask 077
AWS_REGION='${AWS::Region}'
IMAGE_URI='${ImageUri}'
SECRET_ARN='${ApplicationSecretArn}'
SECRET_VERSION='${ApplicationSecretVersionId}'
DATA_VOLUME_ID='${SelectedDataVolumeId}'
FORMAT_EMPTY_DATA_VOLUME='${FormatEmptyDataVolume}'
export AWS_REGION AWS_DEFAULT_REGION="$AWS_REGION" AWS_PAGER=''

dnf install -y docker awscli-2 util-linux e2fsprogs
systemctl enable --now docker amazon-ssm-agent
mkdir -p /var/lib/edurecover /etc/edurecover

volume_serial=$(printf '%s' "$DATA_VOLUME_ID" | tr -d '-')
data_device=''
for attempt in $(seq 1 180); do
  data_device=$(lsblk -dn -o NAME,SERIAL | awk -v serial="$volume_serial" '$2 == serial {print "/dev/" $1; exit}')
  [ -n "$data_device" ] && break
  sleep 2
done
if [ -z "$data_device" ]; then
  echo 'DATA_VOLUME_NOT_ATTACHED' >&2
  exit 1
fi
filesystem=$(blkid -o value -s TYPE "$data_device" || true)
if [ -z "$filesystem" ]; then
  if [ "$FORMAT_EMPTY_DATA_VOLUME" != 'true' ] || [ -n "$(wipefs --no-act --noheadings "$data_device")" ] || [ "$(lsblk -n "$data_device" | wc -l)" -ne 1 ]; then
    echo 'EXISTING_DATA_VOLUME_FORMATTING_FORBIDDEN' >&2
    exit 1
  fi
  mkfs.ext4 "$data_device"
  filesystem='ext4'
fi
if [ "$filesystem" != 'ext4' ]; then
  echo 'DATA_VOLUME_EXT4_REQUIRED' >&2
  exit 1
fi
volume_uuid=$(blkid -o value -s UUID "$data_device")
test -n "$volume_uuid"
if ! grep -Fq "UUID=$volume_uuid /var/lib/edurecover " /etc/fstab; then
  printf 'UUID=%s /var/lib/edurecover ext4 defaults,nofail 0 2\n' "$volume_uuid" >> /etc/fstab
fi
mountpoint -q /var/lib/edurecover || mount /var/lib/edurecover
mountpoint -q /var/lib/edurecover
chown 10001:10001 /var/lib/edurecover
chmod 700 /var/lib/edurecover

# These files contain identifiers only, never the original SecretString.
printf '%s\n' "$IMAGE_URI" > /etc/edurecover/image-uri
printf '%s\n' "$AWS_REGION" > /etc/edurecover/region
printf '%s\n' "$SECRET_ARN" > /etc/edurecover/secret-arn
printf '%s\n' "$SECRET_VERSION" > /etc/edurecover/secret-version
cat > /usr/local/sbin/edurecover-aws-run <<'RUN_SCRIPT'
#!/usr/bin/env bash
set -euo pipefail
export AWS_PAGER=''
image_uri=$(cat /etc/edurecover/image-uri)
region=$(cat /etc/edurecover/region)
registry=$(printf '%s' "$image_uri" | cut -d / -f 1)
mountpoint -q /var/lib/edurecover
aws ecr get-login-password --region "$region" | docker login --username AWS --password-stdin "$registry" >/dev/null
docker pull "$image_uri"
docker rm -f edurecover-aws >/dev/null 2>&1 || true
exec docker run --name edurecover-aws --read-only --init \
  --security-opt no-new-privileges --cap-drop ALL \
  --tmpfs /tmp:rw,noexec,nosuid,size=256m --tmpfs /run:rw,noexec,nosuid,size=16m \
  --log-driver local --log-opt max-size=10m --log-opt max-file=3 \
  --publish 127.0.0.1:8000:8000 \
  --mount type=bind,src=/var/lib/edurecover/state,dst=/app/.edurecover_classroom_state \
  --env AWS_REGION="$region" \
  --env EDURECOVER_APPLICATION_SECRET_ARN="$(cat /etc/edurecover/secret-arn)" \
  --env EDURECOVER_APPLICATION_SECRET_VERSION_ID="$(cat /etc/edurecover/secret-version)" \
  "$image_uri"
RUN_SCRIPT
chmod 700 /usr/local/sbin/edurecover-aws-run
cat > /etc/systemd/system/edurecover-aws.service <<'UNIT'
[Unit]
Description=EduRecover AWS private migration candidate
Requires=docker.service
After=docker.service network-online.target
Wants=network-online.target
RequiresMountsFor=/var/lib/edurecover
[Service]
Type=simple
UMask=0077
ExecStart=/usr/local/sbin/edurecover-aws-run
ExecStop=/usr/bin/docker stop --time 30 edurecover-aws
Restart=on-failure
RestartSec=15
TimeoutStopSec=45
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
# Data restoration and a reviewed start are separate from infrastructure creation.
# The application service deliberately remains disabled and stopped at launch.
echo 'AWS_PRIVATE_HOST_READY_APPLICATION_NOT_STARTED'
