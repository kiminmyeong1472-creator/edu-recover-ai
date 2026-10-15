"""Explicit, opt-in AWS transport for the owner's selected model names.

EDURECOVER_OPEN_MODEL_BINDINGS_JSON maps exact catalogue names to either:
  {"transport": "bedrock", "region": "...", "model_id": "..."}
  {"transport": "sagemaker", "region": "...", "endpoint_name": "..."}
SageMaker bindings may additionally specify a remote "model_id" when the
container requires one. A SageMaker container MUST implement the OpenAI chat
completions JSON contract; InvokeEndpoint itself does not define that format.

As an alternative to one JSON binding, each of QWEN, DEEPSEEK, and NOVA can use
EDURECOVER_OPEN_<FAMILY>_MODEL_KEY, _MODEL_ID, and _REGION. All three variables
must be set together, the key must belong to that family, and the same key must
not also occur in JSON. These conveniences configure Bedrock Converse only.

No endpoint, model identifier, region, provider, or fallback is inferred. The
ordinary boto3 credential chain is used without reading/returning credentials.
Nothing invokes AWS until call_model is called and the new feature is enabled.

API references:
https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_Converse.html
https://docs.aws.amazon.com/sagemaker/latest/APIReference/API_runtime_InvokeEndpoint.html
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass
from typing import Any

from app.services.open_model_catalog import MODEL_CATALOG, model_family


MAX_RESPONSE_BYTES = 256 * 1024
MAX_INPUT_BYTES = 256 * 1024
CONNECT_TIMEOUT_SECONDS = 5
READ_TIMEOUT_SECONDS = 120


class OpenModelError(RuntimeError):
    """A stable public error which never embeds raw SDK messages or identifiers."""

    def __init__(self, msg: str, code: str, http_status: int) -> None:
        super().__init__(msg)
        self.code = code
        self.http_status = http_status


def _config_error() -> OpenModelError:
    return OpenModelError(
        "AI 모델의 연결 설정이 올바르지 않습니다.", "OPEN_MODEL_CONFIG_INVALID", 503,
    )


def _response_error(code: str = "OPEN_MODEL_RESPONSE_INVALID") -> OpenModelError:
    return OpenModelError("모델 응답을 사용할 수 없습니다.", code, 502)


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    obj: dict[str, Any] = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError("Duplicate key.")
        obj[key] = value
    return obj


def _reject_constant(_: str) -> None:
    raise ValueError("Non-JSON constant.")


@dataclass(frozen=True)
class _Binding:
    transport: str
    region: str
    model_id: str | None = None
    endpoint_name: str | None = None


def _required_string(value: Any, limit: int) -> str:
    if (not isinstance(value, str) or not value or value != value.strip()
            or len(value) > limit or any(ord(ch) < 32 for ch in value)):
        raise ValueError("Invalid string.")
    return value


def _bindings() -> dict[str, _Binding]:
    raw = os.getenv("EDURECOVER_OPEN_MODEL_BINDINGS_JSON", "")
    try:
        if len(raw.encode("utf-8")) > MAX_INPUT_BYTES:
            raise ValueError("Configuration too large.")
        source = json.loads(
            raw if raw.strip() else "{}", object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_constant,
        )
        if not isinstance(source, dict):
            raise ValueError("Expected object.")
        for family in ("qwen", "deepseek", "nova"):
            prefix = "EDURECOVER_OPEN_" + family.upper()
            fields = [prefix + suffix for suffix in ("_MODEL_KEY", "_MODEL_ID", "_REGION")]
            if not any(field in os.environ for field in fields):
                continue
            if not all(field in os.environ for field in fields):
                raise ValueError("Incomplete family binding.")
            key, model_id, region = (
                _required_string(os.environ[field], limit)
                for field, limit in zip(fields, (256, 2048, 64))
            )
            if model_family(key) != family or key in source:
                raise ValueError("Conflicting family binding.")
            source[key] = {"transport": "bedrock", "model_id": model_id, "region": region}
        result: dict[str, _Binding] = {}
        for key, entry in source.items():
            model_family(key)
            if not isinstance(entry, dict):
                raise ValueError("Expected binding object.")
            transport = entry.get("transport")
            if transport == "bedrock":
                if set(entry) != {"transport", "region", "model_id"}:
                    raise ValueError("Unexpected or missing field.")
                model_id = _required_string(entry["model_id"], 2048)
                endpoint = None
            elif transport == "sagemaker":
                if (not {"transport", "region", "endpoint_name"} <= set(entry)
                        or set(entry) - {"transport", "region", "endpoint_name", "model_id"}):
                    raise ValueError("Unexpected or missing field.")
                endpoint = _required_string(entry["endpoint_name"], 63)
                if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?", endpoint):
                    raise ValueError("Invalid endpoint name.")
                model_id = (
                    _required_string(entry["model_id"], 2048)
                    if "model_id" in entry else None
                )
            else:
                raise ValueError("Unknown transport.")
            region = _required_string(entry["region"], 64)
            if not re.fullmatch(r"[a-z]{2}(?:-[a-z0-9]+)+-[0-9]+", region):
                raise ValueError("Invalid region.")
            result[key] = _Binding(transport, region, model_id, endpoint)
        return result
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        raise _config_error() from None


def _enabled() -> bool:
    raw = os.getenv("EDURECOVER_OPEN_MODELS_ENABLED", "false").strip().lower()
    if raw not in {"true", "false"}:
        raise _config_error()
    return raw == "true"


def catalog_status() -> list[dict[str, Any]]:
    """Return availability flags, never deployment names or account information."""
    bindings = _bindings()
    enabled = _enabled()
    return [
        dict(entry, configured=entry["model_key"] in bindings,
             enabled=enabled and entry["model_key"] in bindings)
        for entry in MODEL_CATALOG
    ]


def _new_client(binding: _Binding) -> Any:
    try:
        import boto3
        from botocore.config import Config
    except ImportError:
        raise OpenModelError(
            "AWS 모델 호출에 필요한 프로그램이 설치되지 않았습니다.", "OPEN_MODEL_SDK_UNAVAILABLE", 503,
        ) from None
    if "ignore_configured_endpoint_urls" not in getattr(Config, "OPTION_DEFAULTS", {}):
        raise OpenModelError(
            "AWS 모델 호출 프로그램을 지원 버전으로 업데이트해야 합니다.",
            "OPEN_MODEL_SDK_UNAVAILABLE", 503,
        )
    try:
        return boto3.client(
            "bedrock-runtime" if binding.transport == "bedrock" else "sagemaker-runtime",
            region_name=binding.region,
            config=Config(
                connect_timeout=CONNECT_TIMEOUT_SECONDS,
                read_timeout=READ_TIMEOUT_SECONDS,
                retries={"mode": "standard", "total_max_attempts": 1},
                # Do not allow a custom AWS_ENDPOINT_URL to reroute credentials
                # and prompts to an arbitrary non-AWS server.
                ignore_configured_endpoint_urls=True,
            ),
        )
    except Exception as exc:
        raise _runtime_error(exc, initializing=True) from None


def _runtime_error(exc: Exception, *, initializing: bool = False) -> OpenModelError:
    """Map only known SDK codes; never return the raw message or response."""
    error_codes = {
        "AccessDeniedException": (
            "이 AWS 모델을 호출할 권한이 없습니다. 모델 사용 권한과 역할 설정을 확인해야 합니다.",
            "OPEN_MODEL_ACCESS_DENIED", 403,
        ),
        "ResourceNotFoundException": (
            "선택한 AWS 모델 또는 배포 연결을 찾을 수 없습니다. 모델 식별자와 리전을 확인해야 합니다.",
            "OPEN_MODEL_RESOURCE_NOT_FOUND", 502,
        ),
        "ValidationException": (
            "AWS에서 모델 요청을 거부했습니다. 모델 식별자·리전·지원 입력 형식을 확인해야 합니다.",
            "OPEN_MODEL_REQUEST_REJECTED", 400,
        ),
        "ModelNotReadyException": (
            "선택한 AWS 모델이 아직 호출 가능한 상태가 아닙니다.",
            "OPEN_MODEL_NOT_READY", 503,
        ),
    }
    response = getattr(exc, "response", None)
    error = response.get("Error") if isinstance(response, dict) else None
    code = error.get("Code") if isinstance(error, dict) else None
    if not isinstance(code, str):
        code = ""
    if code in error_codes:
        return OpenModelError(*error_codes[code])
    name = type(exc).__name__
    if name in {
        "NoCredentialsError", "PartialCredentialsError", "CredentialRetrievalError",
        "TokenRetrievalError", "SSOTokenLoadError", "UnauthorizedSSOTokenError",
    }:
        return OpenModelError(
            "AWS 자격증명을 사용할 수 없습니다. 서버에 연결한 AWS 역할 또는 로그인 상태를 확인해야 합니다.",
            "OPEN_MODEL_CREDENTIALS_UNAVAILABLE", 503,
        )
    if code in {"ThrottlingException", "TooManyRequestsException", "ServiceQuotaExceededException", "LimitExceededException"}:
        return OpenModelError(
            "AWS 모델 호출량 한도에 도달했습니다. 사용 한도와 호출 간격을 확인해야 합니다.",
            "OPEN_MODEL_QUOTA_EXCEEDED", 429,
        )
    if (name in {"ReadTimeoutError", "ConnectTimeoutError"}
            or code in {"ModelTimeoutException", "RequestTimeout", "RequestTimeoutException"}):
        return OpenModelError(
            "AWS 모델 응답 대기 시간이 초과되었습니다.", "OPEN_MODEL_TIMEOUT", 504,
        )
    if code in {"UnrecognizedClientException", "InvalidClientTokenId", "ExpiredTokenException", "SignatureDoesNotMatch", "InvalidSignatureException"}:
        return OpenModelError(
            "AWS 로그인 또는 요청 서명을 확인하지 못했습니다. 서버의 AWS 인증 상태를 확인해야 합니다.",
            "OPEN_MODEL_AUTHENTICATION_FAILED", 403,
        )
    if (name in {"EndpointConnectionError", "ConnectionClosedError"}
            or code in {"ServiceUnavailableException", "InternalServerException", "InternalFailure"}):
        return OpenModelError(
            "AWS 모델 서비스에 연결하지 못했습니다.", "OPEN_MODEL_SERVICE_UNAVAILABLE", 503,
        )
    return OpenModelError(
        "AWS 모델 호출을 준비하지 못했습니다." if initializing else "설정된 AWS 모델 호출에 실패했습니다.",
        "OPEN_MODEL_RUNTIME_UNAVAILABLE" if initializing else "OPEN_MODEL_INVOCATION_FAILED",
        503 if initializing else 502,
    )


def _close(resource: Any) -> None:
    close = getattr(resource, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _check_serialized_size(value: Any) -> None:
    def binary_size_placeholder(binary: Any) -> str:
        if not isinstance(binary, (bytes, bytearray)):
            raise TypeError("Unsupported response type.")
        # Botocore decodes binary union fields (e.g. redacted reasoning) to
        # bytes. Account for their JSON/base64 size without encoding, returning,
        # or logging the private content, and without allocating over the cap.
        encoded_size = 4 * ((len(binary) + 2) // 3)
        if encoded_size > MAX_RESPONSE_BYTES:
            raise _response_error("OPEN_MODEL_RESPONSE_TOO_LARGE")
        return "0" * encoded_size

    try:
        size = len(json.dumps(
            value, ensure_ascii=False, allow_nan=False,
            default=binary_size_placeholder,
        ).encode("utf-8"))
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise _response_error() from None
    if size > MAX_RESPONSE_BYTES:
        raise _response_error("OPEN_MODEL_RESPONSE_TOO_LARGE")


def _bedrock_text(response: Any) -> str:
    if not isinstance(response, dict):
        raise _response_error()
    _check_serialized_size(response)
    if response.get("stopReason") not in {"end_turn", "stop_sequence"}:
        raise _response_error("OPEN_MODEL_INCOMPLETE_RESPONSE")
    output = response.get("output")
    message = output.get("message") if isinstance(output, dict) else None
    if not isinstance(message, dict) or message.get("role") != "assistant":
        raise _response_error()
    content = message.get("content")
    if not isinstance(content, list) or not content:
        raise _response_error()
    texts = []
    for block in content:
        # Reasoning content is not the final answer: never return or log it.
        # Reject a mixed block so it cannot disguise tool use or another field.
        if isinstance(block, dict) and set(block) == {"reasoningContent"}:
            continue
        if not isinstance(block, dict) or set(block) != {"text"}:
            raise _response_error()
        if not isinstance(block["text"], str):
            raise _response_error()
        texts.append(block["text"])
    text = "".join(texts).strip()
    if not text:
        raise _response_error()
    return text


def _sagemaker_text(response: Any) -> str:
    body = response.get("Body") if isinstance(response, dict) else None
    try:
        if body is None or not callable(getattr(body, "read", None)):
            raise _response_error()
        chunks = []
        size = 0
        while True:
            chunk = body.read(MAX_RESPONSE_BYTES + 1 - size)
            if not isinstance(chunk, bytes):
                raise _response_error()
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_RESPONSE_BYTES:
                raise _response_error("OPEN_MODEL_RESPONSE_TOO_LARGE")
            chunks.append(chunk)
        raw = b"".join(chunks)
        result = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_constant,
        )
        if not isinstance(result, dict):
            raise _response_error()
        choices = result.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise _response_error()
        choice = choices[0]
        if choice.get("finish_reason") != "stop":
            raise _response_error("OPEN_MODEL_INCOMPLETE_RESPONSE")
        if any(value.get("stop_reason") in {
            "length", "max_tokens", "model_context_window_exceeded",
            "tool_calls", "function_call",
        } for value in (result, choice)):
            raise _response_error("OPEN_MODEL_INCOMPLETE_RESPONSE")
        message = choice.get("message")
        if (not isinstance(message, dict) or message.get("role") != "assistant"
                or message.get("tool_calls") or message.get("function_call")
                or not isinstance(message.get("content"), str)
                or not message["content"].strip()):
            raise _response_error()
        return message["content"].strip()
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise _response_error() from None
    finally:
        _close(body)


def _invoke(binding: _Binding, instructions: str, prompt: str, max_tokens: int) -> str:
    client = _new_client(binding)
    try:
        if binding.transport == "bedrock":
            response = client.converse(
                modelId=binding.model_id,
                system=[{"text": instructions}],
                messages=[{"role": "user", "content": [{"text": prompt}]}],
                inferenceConfig={"maxTokens": max_tokens},
            )
            return _bedrock_text(response)
        payload: dict[str, Any] = {
            "messages": [
                {"role": "system", "content": instructions},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": max_tokens,
            "stream": False,
        }
        if binding.model_id is not None:
            payload["model"] = binding.model_id
        response = client.invoke_endpoint(
            EndpointName=binding.endpoint_name,
            ContentType="application/json",
            Accept="application/json",
            Body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        )
        return _sagemaker_text(response)
    except OpenModelError:
        raise
    except Exception as exc:
        raise _runtime_error(exc) from None
    finally:
        _close(client)


async def call_model(
    model_key: str, instructions: str, prompt: str, *, max_tokens: int = 2048,
) -> str:
    """Make exactly one configured inference attempt, with no automatic fallback."""
    try:
        model_family(model_key)
    except ValueError:
        raise OpenModelError("등록되지 않은 모델 이름입니다.", "OPEN_MODEL_UNKNOWN", 400) from None
    if (not isinstance(instructions, str) or not instructions.strip()
            or not isinstance(prompt, str) or not prompt.strip()
            or type(max_tokens) is not int or not 1 <= max_tokens <= 32768):
        raise OpenModelError("모델에 전달할 요청 내용이 올바르지 않습니다.", "OPEN_MODEL_INPUT_INVALID", 400)
    try:
        if len((instructions + prompt).encode("utf-8")) > MAX_INPUT_BYTES:
            raise OpenModelError("모델에 전달할 요청 내용이 크기 제한을 초과했습니다.", "OPEN_MODEL_INPUT_INVALID", 400)
    except UnicodeError:
        raise OpenModelError("모델에 전달할 요청 내용이 올바르지 않습니다.", "OPEN_MODEL_INPUT_INVALID", 400) from None
    bindings = _bindings()
    if not _enabled():
        raise OpenModelError("선택 모델의 AWS 호출 기능이 활성화되지 않았습니다.", "OPEN_MODEL_DISABLED", 503)
    binding = bindings.get(model_key)
    if binding is None:
        raise OpenModelError(
            "선택한 모델의 배포 연결이 설정되지 않았습니다.", "OPEN_MODEL_NOT_CONFIGURED", 503,
        )
    return await asyncio.to_thread(_invoke, binding, instructions, prompt, max_tokens)
