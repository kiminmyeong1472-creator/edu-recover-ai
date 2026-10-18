#!/usr/bin/env python3
"""Validate, plan, or explicitly execute a new private AWS host stack."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from common import IMAGE_RE, aws, load_config, safe_main, verify_account

ROOT = Path(__file__).resolve().parent


def verify_resources(config: dict) -> None:
    verify_account(config)
    vpc = aws(config, "ec2", "describe-vpcs", "--vpc-ids", config["vpc_id"])["Vpcs"][0]
    subnet = aws(config, "ec2", "describe-subnets", "--subnet-ids", config["subnet_id"])["Subnets"][0]
    if vpc["State"] != "available" or subnet["State"] != "available" or subnet["VpcId"] != config["vpc_id"] or subnet["AvailabilityZone"] != config["availability_zone"]:
        raise RuntimeError("VPC_SUBNET_AZ_VALIDATION_FAILED")
    dns = aws(config, "ec2", "describe-vpc-attribute", "--vpc-id", config["vpc_id"], "--attribute", "enableDnsSupport")
    if not dns.get("EnableDnsSupport", {}).get("Value"):
        raise RuntimeError("VPC_DNS_REQUIRED")
    # The stack creates no NAT/IGW. Public IPv4 is egress-only: no ingress rules,
    # no public listener, no domain. Existing private NAT is an optional mode.
    tables = aws(config, "ec2", "describe-route-tables", "--filters", "Name=association.subnet-id,Values=" + config["subnet_id"])["RouteTables"]
    if not tables:
        tables = aws(config, "ec2", "describe-route-tables", "--filters", "Name=vpc-id,Values=" + config["vpc_id"], "Name=association.main,Values=true")["RouteTables"]
    routes = [r for t in tables for r in t.get("Routes", [])
              if r.get("DestinationCidrBlock") == "0.0.0.0/0" and r.get("State") == "active"]
    if config["outbound_mode"] == "public-egress":
        igw_ids = [r["GatewayId"] for r in routes if r.get("GatewayId", "").startswith("igw-")]
        if not igw_ids:
            raise RuntimeError("EXISTING_INTERNET_GATEWAY_ROUTE_REQUIRED")
        gateways = aws(config, "ec2", "describe-internet-gateways", "--internet-gateway-ids", *igw_ids)["InternetGateways"]
        if not any(a.get("VpcId") == config["vpc_id"] and a.get("State") == "available"
                   for g in gateways for a in g.get("Attachments", [])):
            raise RuntimeError("INTERNET_GATEWAY_NOT_ATTACHED")
    else:
        nat_ids = [r["NatGatewayId"] for r in routes if r.get("NatGatewayId")]
        if not nat_ids:
            raise RuntimeError("EXISTING_PRIVATE_SUBNET_NAT_ROUTE_REQUIRED")
        nat = aws(config, "ec2", "describe-nat-gateways", "--nat-gateway-ids", *nat_ids)["NatGateways"]
        if not any(x["State"] == "available" for x in nat):
            raise RuntimeError("EXISTING_NAT_GATEWAY_NOT_AVAILABLE")
    image = aws(config, "ec2", "describe-images", "--image-ids", config["ami_id"])["Images"][0]
    if image.get("Architecture") != "x86_64" or image.get("State") != "available" or "al2023" not in image.get("Name", "").lower() or image.get("ImageOwnerAlias") != "amazon":
        raise RuntimeError("OFFICIAL_AL2023_X86_AMI_REQUIRED")
    ecr = IMAGE_RE.fullmatch(config["ecr_image_uri"])
    repo = aws(config, "ecr", "describe-repositories", "--repository-names", ecr.group(3))["repositories"][0]
    if repo["repositoryArn"] != config["ecr_repository_arn"]:
        raise RuntimeError("ECR_REPOSITORY_NOT_VERIFIED")
    images = aws(config, "ecr", "describe-images", "--repository-name", ecr.group(3), "--image-ids", "imageDigest=sha256:" + ecr.group(4))["imageDetails"]
    if not images:
        raise RuntimeError("ECR_DIGEST_NOT_PRESENT")
    secret = aws(config, "secretsmanager", "describe-secret", "--secret-id", config["application_secret_arn"])
    if secret.get("ARN") != config["application_secret_arn"] or secret.get("DeletedDate") or config["application_secret_version_id"] not in secret.get("VersionIdsToStages", {}):
        raise RuntimeError("EXTERNAL_PINNED_SECRET_NOT_VERIFIED")
    key = secret.get("KmsKeyId", "")
    if key and key != "alias/aws/secretsmanager":
        observed_key = aws(config, "kms", "describe-key", "--key-id", key)["KeyMetadata"]["Arn"]
        if config["secret_kms_key_arn"] != observed_key:
            raise RuntimeError("SECRET_KMS_KEY_ARN_REQUIRED")
    if config["existing_data_volume_id"]:
        volume = aws(config, "ec2", "describe-volumes", "--volume-ids", config["existing_data_volume_id"])["Volumes"][0]
        if not volume.get("Encrypted") or volume["AvailabilityZone"] != config["availability_zone"] or volume["State"] != "available" or volume.get("Attachments"):
            raise RuntimeError("ENCRYPTED_AVAILABLE_SAME_AZ_VOLUME_REQUIRED")


def parameters(config: dict) -> list[dict]:
    mapping = {
        "VpcId": config["vpc_id"], "SubnetId": config["subnet_id"],
        "AvailabilityZone": config["availability_zone"], "AmiId": config["ami_id"],
        "InstanceType": config["instance_type"], "ImageUri": config["ecr_image_uri"],
        "EcrRepositoryArn": config["ecr_repository_arn"], "ApplicationSecretArn": config["application_secret_arn"],
        "ApplicationSecretVersionId": config["application_secret_version_id"],
        "SecretKmsKeyArn": config["secret_kms_key_arn"], "DataKmsKeyArn": config["data_kms_key_arn"],
        "DataVolumeSizeGb": str(config["data_volume_size_gb"]), "ExistingDataVolumeId": config["existing_data_volume_id"],
        "EnableApprovedBedrockRuntime": str(config["allow_approved_bedrock_runtime"]).lower(),
        "ApprovedBedrockResourceArns": ",".join(config["approved_bedrock_resource_arns"]),
        "OutboundMode": config["outbound_mode"],
    }
    return [{"ParameterKey": k, "ParameterValue": v} for k, v in mapping.items()]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--validate-local", action="store_true")
    modes.add_argument("--plan", action="store_true", help="Read AWS and create an unexecuted CREATE change set")
    modes.add_argument("--execute-change-set", help="Exact previously reviewed change-set ARN")
    parser.add_argument("--confirm-billed-resources", action="store_true")
    args = parser.parse_args()
    config = load_config(args.config)
    if not args.plan and not args.execute_change_set:
        print("LOCAL_CONFIGURATION_VALID; no AWS request executed")
        return
    verify_resources(config)
    if args.execute_change_set:
        if not args.confirm_billed_resources:
            raise ValueError("EXECUTION_REQUIRES_CONFIRM_BILLED_RESOURCES")
        change = aws(config, "cloudformation", "describe-change-set", "--change-set-name", args.execute_change_set,
                     "--stack-name", config["stack_name"])
        if change.get("ChangeSetType") != "CREATE" or change.get("Status") != "CREATE_COMPLETE" or change.get("ExecutionStatus") != "AVAILABLE":
            raise RuntimeError("REVIEWED_AVAILABLE_CREATE_CHANGE_SET_REQUIRED")
        observed = {p["ParameterKey"]: p["ParameterValue"] for p in change.get("Parameters", [])}
        if observed != {p["ParameterKey"]: p["ParameterValue"] for p in parameters(config)}:
            raise RuntimeError("REVIEWED_CHANGE_SET_PARAMETERS_MISMATCH")
        aws(config, "cloudformation", "execute-change-set", "--change-set-name", args.execute_change_set,
            "--stack-name", config["stack_name"])
        print("STACK_CREATION_SUBMITTED; verify stack completion and private host before restoring data")
        return
    # CREATE only: existing stacks are never silently updated or replaced.
    stacks = aws(config, "cloudformation", "list-stacks").get("StackSummaries", [])
    if any(x["StackName"] == config["stack_name"] and x["StackStatus"] != "DELETE_COMPLETE" for x in stacks):
        raise RuntimeError("STACK_ALREADY_EXISTS_CREATE_ONLY")
    template = ROOT / "cloudformation.json"
    aws(config, "cloudformation", "validate-template", "--template-body", "file://" + str(template))
    reply = aws(config, "cloudformation", "create-change-set", "--stack-name", config["stack_name"],
                "--change-set-name", "edurecover-private-" + str(int(time.time())), "--change-set-type", "CREATE",
                "--template-body", "file://" + str(template), "--parameters", json.dumps(parameters(config)),
                "--capabilities", "CAPABILITY_IAM", "--description", "Private SSM-only migration host; application stopped")
    print(json.dumps({"change_set_id": reply["Id"], "stack_id": reply["StackId"],
                      "execution": "NOT_EXECUTED", "application": "NOT_STARTED"}, ensure_ascii=False))


if __name__ == "__main__":
    safe_main(main)
