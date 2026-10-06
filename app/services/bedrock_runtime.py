"""Server-only Amazon Bedrock adapters for EduRecover.

AWS credentials stay in the deployment environment. Prompts and responses are
never written to logs by this module. Claude uses the Bedrock Runtime Converse
API. GPT-5.6 Luna uses a SigV4-signed Bedrock Mantle Responses API request
because Luna is exposed on that endpoint.
"""
from __future__ import annotations

import asyncio
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

from app.core.config import settings


class BedrockRequestError(RuntimeError):
    """Safe Bedrock failure with bounded status metadata only."""

    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class BedrockTextResult:
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0


def bedrock_credentials_configured() -> bool:
    """Return configuration presence without exposing credential values."""
    return bool(
        os.getenv("AWS_ACCESS_KEY_ID", "").strip()
        and os.getenv("AWS_SECRET_ACCESS_KEY", "").strip()
    )


def _region() -> str:
    region = (settings.aws_region or "ap-northeast-2").strip()
    if not region:
        raise BedrockRequestError("AWS region is not configured.")
    return region


def _session_credentials():
    credentials = boto3.Session(region_name=_region()).get_credentials()
    if credentials is None:
        raise BedrockRequestError("AWS credentials are not configured.")
    return credentials.get_frozen_credentials()


def _extract_response_text(payload: dict[str, Any]) -> str:
    direct = payload.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    parts: list[str] = []
    for item in payload.get("output", []) if isinstance(payload.get("output"), list) else []:
        if not isinstance(item, dict):
            continue
        content = item.get("content", [])
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            text = block.get("text")
            if block.get("type") in {"output_text", "text"} and isinstance(text, str):
                parts.append(text)
    return "".join(parts).strip()


def _signed_json_post(url: str, *, service: str, payload: dict[str, Any], timeout: int = 180) -> dict[str, Any]:
    """Send a SigV4 request without logging headers, body, or provider errors."""
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    request = AWSRequest(
        method="POST",
        url=url,
        data=body,
        headers={"Content-Type": "application/json"},
    )
    SigV4Auth(_session_credentials(), service, _region()).add_auth(request)
    prepared = request.prepare()
    http_request = urllib.request.Request(
        url,
        data=body,
        headers=dict(prepared.headers.items()),
        method="POST",
    )
    try:
        with urllib.request.urlopen(http_request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        # Never include provider response bodies: they may echo source text.
        raise BedrockRequestError(
            "Amazon Bedrock request was rejected.",
            status_code=int(getattr(exc, "code", 0) or 0) or None,
        ) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise BedrockRequestError("Amazon Bedrock request failed.") from exc
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError, TypeError) as exc:
        raise BedrockRequestError("Amazon Bedrock returned an invalid JSON response.") from exc
    if not isinstance(value, dict):
        raise BedrockRequestError("Amazon Bedrock returned an invalid response object.")
    return value


def _claude_sync(
    *,
    prompt: str,
    system_instruction: str,
    model: str,
    max_tokens: int,
) -> BedrockTextResult:
    client = boto3.client("bedrock-runtime", region_name=_region())
    try:
        response = client.converse(
            modelId=model,
            system=[{"text": system_instruction}],
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": max_tokens},
        )
    except Exception as exc:
        status = None
        response_meta = getattr(exc, "response", None)
        if isinstance(response_meta, dict):
            status = (
                response_meta.get("ResponseMetadata", {}).get("HTTPStatusCode")
                if isinstance(response_meta.get("ResponseMetadata"), dict)
                else None
            )
        raise BedrockRequestError(
            "Amazon Bedrock Claude request failed.",
            status_code=status if isinstance(status, int) else None,
        ) from exc

    output = response.get("output", {})
    message = output.get("message", {}) if isinstance(output, dict) else {}
    blocks = message.get("content", []) if isinstance(message, dict) else []
    text = "".join(
        block.get("text", "")
        for block in blocks
        if isinstance(block, dict) and isinstance(block.get("text"), str)
    ).strip()
    if not text:
        raise BedrockRequestError("Amazon Bedrock Claude returned no text.")
    usage = response.get("usage", {})
    return BedrockTextResult(
        text=text,
        model=model,
        input_tokens=int(usage.get("inputTokens", 0) or 0) if isinstance(usage, dict) else 0,
        output_tokens=int(usage.get("outputTokens", 0) or 0) if isinstance(usage, dict) else 0,
    )


async def generate_claude(
    *,
    prompt: str,
    system_instruction: str,
    model: str,
    max_tokens: int,
) -> BedrockTextResult:
    return await asyncio.to_thread(
        _claude_sync,
        prompt=prompt,
        system_instruction=system_instruction,
        model=model,
        max_tokens=max_tokens,
    )


def _gpt_luna_sync(
    *,
    prompt: str,
    system_instruction: str,
    model: str,
) -> BedrockTextResult:
    region = _region()
    url = f"https://bedrock-mantle.{region}.api.aws/openai/v1/responses"
    value = _signed_json_post(
        url,
        service="bedrock-mantle",
        payload={
            "model": model,
            "instructions": system_instruction,
            "input": prompt,
            "store": False,
        },
    )
    text = _extract_response_text(value)
    if not text:
        raise BedrockRequestError("Amazon Bedrock GPT returned no text.")
    usage = value.get("usage", {})
    return BedrockTextResult(
        text=text,
        model=model,
        input_tokens=int(usage.get("input_tokens", 0) or 0) if isinstance(usage, dict) else 0,
        output_tokens=int(usage.get("output_tokens", 0) or 0) if isinstance(usage, dict) else 0,
    )


async def generate_gpt_luna(
    *,
    prompt: str,
    system_instruction: str,
    model: str,
) -> BedrockTextResult:
    return await asyncio.to_thread(
        _gpt_luna_sync,
        prompt=prompt,
        system_instruction=system_instruction,
        model=model,
    )
