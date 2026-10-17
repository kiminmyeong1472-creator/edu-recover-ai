"""Explicit, opt-in model transports for the owner's selected model names.

EDURECOVER_OPEN_MODEL_BINDINGS_JSON maps exact catalogue names to either:
  {"transport": "bedrock", "region": "...", "model_id": "..."}
  {"transport": "sagemaker", "region": "...", "endpoint_name": "..."}
  {"transport": "openai_compatible", "endpoint_url": "https://.../v1/chat/completions",
   "model_id": "...", "api_key_env": "PROVIDER_API_KEY", "request_format": "chat"}
For a pretrained text-completion deployment, request_format is "completion"
and endpoint_url ends with /completions rather than /chat/completions.
SageMaker bindings may additionally specify a remote "model_id" when the
container requires one. A SageMaker container MUST implement the OpenAI chat
completions JSON contract; InvokeEndpoint itself does not define that format.

As an alternative to one JSON binding, each of QWEN, DEEPSEEK, and NOVA can use
EDURECOVER_OPEN_<FAMILY>_MODEL_KEY, _MODEL_ID, and _REGION. All three variables
must be set together, the key must belong to that family, and the same key must
not also occur in JSON. These conveniences configure Bedrock Converse only.

EDURECOVER_GPT6_LUNA_AZURE_ENABLED=true explicitly opts into the fixed
GPT-6-Luna preset: OpenRouter chat, openai/gpt-6-luna, OPENROUTER_API_KEY,
Azure only, no provider fallbacks, and ZDR with data collection denied. It is
disabled by default and must not duplicate a JSON binding for the same name.
Any explicit JSON binding for GPT-6-Luna must match that fixed preset exactly.

EDURECOVER_VERIFIED_MODEL_ROUTES_ENABLED=true adds the dated, verified routes
in open_model_presets only where no explicit binding exists. It never adds a
DeepSeek OpenRouter route and every OpenRouter request still requires ZDR.

Other than these explicitly enabled presets, no endpoint, model identifier,
region, provider, or fallback is inferred. The ordinary boto3 credential chain
is used without reading/returning credentials.
Nothing invokes AWS until call_model is called and the new feature is enabled.

API references:
https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_Converse.html
https://docs.aws.amazon.com/sagemaker/latest/APIReference/API_runtime_InvokeEndpoint.html
https://openrouter.ai/docs/guides/features/zdr
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import os
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from app.services.open_model_catalog import MODEL_CATALOG, model_family
from app.services.open_model_presets import OPENROUTER_MODELS, verified_routes_enabled
from app.services.open_model_http import OpenModelHTTPError, post_json, validate_endpoint_url


MAX_RESPONSE_BYTES = 32 * 1024 * 1024
MAX_INPUT_BYTES = 4 * 1024 * 1024
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGES = 4
CONNECT_TIMEOUT_SECONDS = 5
READ_TIMEOUT_SECONDS = 600
OPENROUTER_CHAT_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
GPT6_LUNA_MODEL_KEY = "GPT-6-Luna"
GPT6_LUNA_MODEL_ID = "openai/gpt-6-luna"
_GPT6_LUNA_AZURE_BINDING = {
    "transport": "openai_compatible",
    "endpoint_url": OPENROUTER_CHAT_ENDPOINT,
    "model_id": GPT6_LUNA_MODEL_ID,
    "api_key_env": "OPENROUTER_API_KEY",
    "request_format": "chat",
}
DEEPSEEK_CHAT_ENDPOINT = "https://api.deepseek.com/chat/completions"
_DEEPSEEK_DIRECT_BINDINGS = {
    key: {
        "transport": "openai_compatible", "endpoint_url": DEEPSEEK_CHAT_ENDPOINT,
        "model_id": model_id, "api_key_env": "DEEPSEEK_API_KEY", "request_format": "chat",
    }
    for key, model_id in (
        ("DeepSeek-V4.1-Flash", "deepseek-flash"),
        ("DeepSeek-V4-Pro", "deepseek-v4-pro"),
    )
}


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
    if code == "OPEN_MODEL_OUTPUT_LIMIT_REACHED":
        return OpenModelError(
            "모델 제공자의 답변 길이 또는 문맥 한도에 도달해 답변이 중단됐습니다. "
            "답변 길이를 직접 지정했다면 제한을 해제하거나 요청을 나누어야 합니다.", code, 502,
        )
    if code == "OPEN_MODEL_INCOMPLETE_RESPONSE":
        return OpenModelError("모델이 답변을 정상적으로 끝내지 못했습니다.", code, 502)
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
    endpoint_url: str | None = None
    api_key_env: str | None = None
    request_format: str = "chat"


def _supports_images(binding: _Binding) -> bool:
    """Only the documented DeepSeek Flash vision route accepts raw images."""
    if binding.transport != "openai_compatible" or binding.request_format != "chat":
        return False
    if (binding.endpoint_url == DEEPSEEK_CHAT_ENDPOINT
            and binding.api_key_env == "DEEPSEEK_API_KEY"
            and binding.model_id == _DEEPSEEK_DIRECT_BINDINGS["DeepSeek-V4.1-Flash"]["model_id"]):
        return True
    return False


def _validated_images(images: Any) -> list[dict[str, str]]:
    """Accept bounded canonical base64 only; never fetch a URL or local path."""
    if images is None:
        return []
    invalid = OpenModelError(
        "사진 첨부 형식을 확인해야 합니다. JPG·PNG·WEBP 사진을 사용할 수 있습니다.",
        "OPEN_MODEL_IMAGE_INVALID", 400,
    )
    if not isinstance(images, list) or len(images) > MAX_IMAGES:
        raise invalid
    result = []
    for image in images:
        if not isinstance(image, dict) or set(image) != {"mime_type", "data"}:
            raise invalid
        mime, data = image["mime_type"], image["data"]
        if (not isinstance(mime, str) or mime not in {"image/jpeg", "image/png", "image/webp"}
                or not isinstance(data, str) or not data
                or len(data) > 4 * ((MAX_IMAGE_BYTES + 2) // 3)):
            raise invalid
        try:
            raw = base64.b64decode(data, validate=True)
        except (ValueError, binascii.Error):
            raise invalid from None
        if (not raw or len(raw) > MAX_IMAGE_BYTES
                or base64.b64encode(raw).decode("ascii") != data):
            raise invalid
        if not (
            (mime == "image/jpeg" and raw.startswith(b"\xff\xd8\xff") and raw.endswith(b"\xff\xd9"))
            or (mime == "image/png" and raw.startswith(b"\x89PNG\r\n\x1a\n"))
            or (mime == "image/webp" and raw.startswith(b"RIFF") and raw[8:12] == b"WEBP")
        ):
            raise invalid
        result.append({"mime_type": mime, "data": data})
    return result


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
        deepseek_direct = os.getenv("EDURECOVER_DEEPSEEK_DIRECT_ENABLED", "false").strip().lower()
        if deepseek_direct not in {"true", "false"}:
            raise ValueError("Invalid DeepSeek direct flag.")
        if deepseek_direct == "true":
            # Migrate only DeepSeek entries: no old OpenRouter route remains as
            # an automatic fallback, and unrelated model bindings are preserved.
            source = {
                key: entry for key, entry in source.items()
                if not (model_family(key) == "deepseek" and isinstance(entry, dict)
                        and (urlsplit(entry.get("endpoint_url", "")).hostname or "").lower()
                        in {"openrouter.ai", "www.openrouter.ai"})
            }
            source.update({key: dict(entry) for key, entry in _DEEPSEEK_DIRECT_BINDINGS.items()})
        luna_enabled = os.getenv("EDURECOVER_GPT6_LUNA_AZURE_ENABLED", "false").strip().lower()
        if luna_enabled not in {"true", "false"}:
            raise ValueError("Invalid Luna preset flag.")
        if luna_enabled == "true":
            if GPT6_LUNA_MODEL_KEY in source:
                raise ValueError("Conflicting Luna preset binding.")
            source[GPT6_LUNA_MODEL_KEY] = dict(_GPT6_LUNA_AZURE_BINDING)
        for family in ("qwen", "deepseek", "nova"):
            if family == "deepseek" and deepseek_direct == "true":
                # The explicit direct preset takes precedence over the former
                # one-model Bedrock convenience settings for this family.
                continue
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
        if verified_routes_enabled():
            for key, model_id in OPENROUTER_MODELS.items():
                source.setdefault(key, {
                    "transport": "openai_compatible",
                    "endpoint_url": OPENROUTER_CHAT_ENDPOINT,
                    "model_id": model_id, "api_key_env": "OPENROUTER_API_KEY",
                    "request_format": "chat",
                })
        result: dict[str, _Binding] = {}
        for key, entry in source.items():
            model_family(key)
            if not isinstance(entry, dict):
                raise ValueError("Expected binding object.")
            if key == GPT6_LUNA_MODEL_KEY and entry != _GPT6_LUNA_AZURE_BINDING:
                raise ValueError("Luna requires the fixed Azure-only OpenRouter binding.")
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
            elif transport == "openai_compatible":
                if set(entry) != {"transport", "endpoint_url", "model_id", "api_key_env", "request_format"}:
                    raise ValueError("Unexpected or missing field.")
                model_id = _required_string(entry["model_id"], 2048)
                key_env = _required_string(entry["api_key_env"], 128)
                if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key_env):
                    raise ValueError("Invalid credential reference.")
                request_format = entry["request_format"]
                if request_format not in {"chat", "completion"}:
                    raise ValueError("Invalid request format.")
                url = validate_endpoint_url(_required_string(entry["endpoint_url"], 2048))
                if (not url.endswith("/completions")
                        or (url.endswith("/chat/completions") != (request_format == "chat"))):
                    raise ValueError("Endpoint and format do not match.")
                result[key] = _Binding(transport, "", model_id, None, url, key_env, request_format)
                continue
            else:
                raise ValueError("Unknown transport.")
            region = _required_string(entry["region"], 64)
            if not re.fullmatch(r"[a-z]{2}(?:-[a-z0-9]+)+-[0-9]+", region):
                raise ValueError("Invalid region.")
            result[key] = _Binding(transport, region, model_id, endpoint)
        return result
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OpenModelHTTPError):
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
    rows = []
    for entry in MODEL_CATALOG:
        binding = bindings.get(entry["model_key"])
        credentials_ready = binding is not None and (
            binding.transport != "openai_compatible" or _http_token(binding) is not None
        )
        state = ("not_configured" if binding is None else
                 "missing_credentials" if not credentials_ready else
                 "disabled" if not enabled else "configured_not_verified")
        row = dict(entry, configured=bool(credentials_ready),
                   enabled=enabled and bool(credentials_ready), readiness_state=state)
        if binding is not None and _supports_images(binding):
            row["supports_images"] = True
        if binding is not None and binding.endpoint_url == OPENROUTER_CHAT_ENDPOINT:
            row["route_label"] = (
                "OpenRouter · Azure 전용 · ZDR 필수" if entry["model_key"] == GPT6_LUNA_MODEL_KEY
                else "OpenRouter · ZDR 필수"
            )
        preset = _DEEPSEEK_DIRECT_BINDINGS.get(entry["model_key"])
        if preset is not None and binding is not None and (
            binding.transport == "openai_compatible"
            and binding.endpoint_url == preset["endpoint_url"]
            and binding.model_id == preset["model_id"]
            and binding.api_key_env == preset["api_key_env"]
        ):
            row.update(route_label="DeepSeek 직접 API · OpenRouter 미사용",
                       required_api_key_env="DEEPSEEK_API_KEY")
        rows.append(row)
    return rows


def _http_token(binding: _Binding) -> str | None:
    value = os.getenv(binding.api_key_env or "", "")
    if not re.fullmatch(r"[A-Za-z0-9._~+/=-]{1,8192}", value):
        return None
    return value


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
    if response.get("stopReason") in {"max_tokens", "model_context_window_exceeded"}:
        raise _response_error("OPEN_MODEL_OUTPUT_LIMIT_REACHED")
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
        return _openai_text(result, "chat")
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise _response_error() from None
    finally:
        _close(body)


def _openai_text(result: Any, request_format: str) -> str:
    _check_serialized_size(result)
    if not isinstance(result, dict):
        raise _response_error()
    choices = result.get("choices")
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        raise _response_error()
    choice = choices[0]
    finish_reason = choice.get("finish_reason")
    if isinstance(finish_reason, str) and finish_reason in {"length", "max_tokens", "model_context_window_exceeded"}:
        raise _response_error("OPEN_MODEL_OUTPUT_LIMIT_REACHED")
    if choice.get("finish_reason") != "stop":
        raise _response_error("OPEN_MODEL_INCOMPLETE_RESPONSE")
    for value in (result, choice):
        stop_reason = value.get("stop_reason")
        if stop_reason is not None and not isinstance(stop_reason, (str, int)):
            raise _response_error()
        if stop_reason in {"length", "max_tokens", "model_context_window_exceeded"}:
            raise _response_error("OPEN_MODEL_OUTPUT_LIMIT_REACHED")
        if stop_reason in {"tool_calls", "function_call"}:
            raise _response_error("OPEN_MODEL_INCOMPLETE_RESPONSE")
    if request_format == "completion":
        text = choice.get("text")
        if choice.get("message") or choice.get("tool_calls") or choice.get("function_call"):
            raise _response_error()
    else:
        message = choice.get("message")
        if (not isinstance(message, dict) or message.get("role") != "assistant"
                or message.get("tool_calls") or message.get("function_call")):
            raise _response_error()
        text = message.get("content")
    if not isinstance(text, str) or not text.strip():
        raise _response_error()
    return text.strip()


def _invoke_http(
    binding: _Binding, instructions: str, prompt: str, max_tokens: int | None,
    images: list[dict[str, str]] | None = None,
) -> str:
    token = _http_token(binding)
    if token is None:
        raise OpenModelError("선택한 모델의 인증 키 설정을 확인해야 합니다.",
                             "OPEN_MODEL_CREDENTIALS_UNAVAILABLE", 503)
    payload: dict[str, Any] = {"model": binding.model_id, "stream": False}
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    if binding.request_format == "completion":
        payload["prompt"] = instructions.rstrip() + "\n\n" + prompt
    else:
        user_content: Any = prompt
        if images:
            user_content = [{"type": "text", "text": prompt}] + [
                {"type": "image_url", "image_url": {
                    "url": "data:" + image["mime_type"] + ";base64," + image["data"],
                }} for image in images
            ]
        payload["messages"] = [{"role": "system", "content": instructions},
                               {"role": "user", "content": user_content}]
    if binding.endpoint_url == OPENROUTER_CHAT_ENDPOINT:
        # Apply to every request, independent of account defaults. No non-ZDR
        # route is permitted if the selected model has no eligible endpoint.
        payload["provider"] = {"zdr": True, "data_collection": "deny"}
        if binding.model_id == GPT6_LUNA_MODEL_ID:
            payload["provider"].update({"only": ["azure"], "allow_fallbacks": False})
    try:
        return _openai_text(post_json(binding.endpoint_url, payload, token), binding.request_format)
    except OpenModelHTTPError as exc:
        codes = {
            "OPEN_MODEL_ACCESS_DENIED": ("선택한 모델의 접근 권한을 확인해야 합니다.", 403),
            "OPEN_MODEL_AUTHENTICATION_FAILED": ("선택한 모델의 인증 설정을 확인해야 합니다.", 403),
            "OPEN_MODEL_INSUFFICIENT_BALANCE": ("모델 제공자가 잔액 부족으로 요청을 거부했습니다. 해당 제공자의 잔액과 결제 반영 상태를 확인하십시오.", 402),
            "OPEN_MODEL_QUOTA_EXCEEDED": ("선택한 모델의 사용 한도와 호출 간격을 확인해야 합니다.", 429),
            "OPEN_MODEL_TIMEOUT": ("선택한 모델의 응답 대기 시간이 초과됐습니다.", 504),
            "OPEN_MODEL_RESOURCE_NOT_FOUND": ("선택한 모델의 배포 연결을 찾지 못했습니다.", 502),
            "OPEN_MODEL_REQUEST_REJECTED": ("모델 서버가 요청을 거부했습니다.", 400),
            "OPEN_MODEL_INPUT_INVALID": ("모델에 전달할 요청 형식을 확인해야 합니다.", 400),
            "OPEN_MODEL_INPUT_TOO_LARGE": ("모델 요청이 허용 크기를 초과했습니다.", 413),
            "OPEN_MODEL_CONFIG_INVALID": ("모델 연결 설정을 확인해야 합니다.", 503),
            "OPEN_MODEL_RESPONSE_TOO_LARGE": ("모델 응답이 허용 크기를 초과했습니다.", 502),
            "OPEN_MODEL_RESPONSE_INVALID": ("모델 응답 형식을 확인해야 합니다.", 502),
            "OPEN_MODEL_SERVICE_UNAVAILABLE": ("모델 서버에 연결하지 못했습니다.", 503),
        }
        code = exc.code if exc.code in codes else "OPEN_MODEL_SERVICE_UNAVAILABLE"
        message, status = codes[code]
        raise OpenModelError(message, code, status) from None


def _invoke(
    binding: _Binding, instructions: str, prompt: str, max_tokens: int | None,
    images: list[dict[str, str]] | None = None,
) -> str:
    if binding.transport == "openai_compatible":
        return _invoke_http(binding, instructions, prompt, max_tokens, images)
    client = _new_client(binding)
    try:
        if binding.transport == "bedrock":
            request: dict[str, Any] = {
                "modelId": binding.model_id,
                "system": [{"text": instructions}],
                "messages": [{"role": "user", "content": [{"text": prompt}]}],
            }
            if max_tokens is not None:
                request["inferenceConfig"] = {"maxTokens": max_tokens}
            response = client.converse(**request)
            return _bedrock_text(response)
        payload: dict[str, Any] = {
            "messages": [
                {"role": "system", "content": instructions},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
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
    model_key: str, instructions: str, prompt: str, *, max_tokens: int | None = 2048,
    images: list[dict[str, str]] | None = None,
) -> str:
    """Make exactly one configured inference attempt, with no automatic fallback."""
    try:
        model_family(model_key)
    except ValueError:
        raise OpenModelError("등록되지 않은 모델 이름입니다.", "OPEN_MODEL_UNKNOWN", 400) from None
    if (not isinstance(instructions, str) or not instructions.strip()
            or not isinstance(prompt, str) or not prompt.strip()
            or (max_tokens is not None and (type(max_tokens) is not int or max_tokens < 1))):
        raise OpenModelError("모델에 전달할 요청 내용이 올바르지 않습니다.", "OPEN_MODEL_INPUT_INVALID", 400)
    try:
        if len((instructions + prompt).encode("utf-8")) > MAX_INPUT_BYTES:
            raise OpenModelError("모델에 전달할 요청 내용이 크기 제한을 초과했습니다.", "OPEN_MODEL_INPUT_INVALID", 400)
    except UnicodeError:
        raise OpenModelError("모델에 전달할 요청 내용이 올바르지 않습니다.", "OPEN_MODEL_INPUT_INVALID", 400) from None
    bindings = _bindings()
    if not _enabled():
        raise OpenModelError("선택 모델의 호출 기능이 활성화되지 않았습니다.", "OPEN_MODEL_DISABLED", 503)
    binding = bindings.get(model_key)
    if binding is None:
        raise OpenModelError(
            "선택한 모델의 배포 연결이 설정되지 않았습니다.", "OPEN_MODEL_NOT_CONFIGURED", 503,
        )
    validated_images = _validated_images(images)
    if validated_images and not _supports_images(binding):
        raise OpenModelError(
            "선택한 모델의 사진 입력 지원이 확인되지 않았습니다. 사진을 지원하는 모델을 선택해야 합니다.",
            "OPEN_MODEL_IMAGE_UNSUPPORTED", 400,
        )
    return await asyncio.to_thread(_invoke, binding, instructions, prompt, max_tokens, validated_images)
