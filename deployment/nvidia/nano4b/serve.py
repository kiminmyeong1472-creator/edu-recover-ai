"""Launch only the pinned, unquantized Nano 4B; keep credentials out of argv."""
import os
import re

from verify_parser import MODEL, REVISION


def command(environment: dict[str, str]) -> list[str]:
    key = environment.get("EDURECOVER_NEMOTRON_API_KEY", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", key) or "change" in key.lower():
        raise ValueError("A dedicated random EDURECOVER_NEMOTRON_API_KEY is required")
    context = environment.get("NEMOTRON_MAX_MODEL_LEN", "262144")
    if not context.isascii() or not context.isdigit() or not 1 <= int(context) <= 262144:
        raise ValueError("NEMOTRON_MAX_MODEL_LEN must be 1..262144")
    return [
        "vllm", "serve", MODEL,
        "--served-model-name", MODEL,
        "--revision", REVISION,
        "--code-revision", REVISION,
        "--tokenizer-revision", REVISION,
        "--dtype", "bfloat16",
        "--load-format", "safetensors",
        "--trust-remote-code",
        "--host", "0.0.0.0", "--port", "8000",
        "--max-model-len", context,
        "--max-num-seqs", "8", "--tensor-parallel-size", "1",
        "--mamba-ssm-cache-dtype", "float32",
        "--enable-auto-tool-choice", "--tool-call-parser", "qwen3_coder",
        "--reasoning-parser-plugin", "/opt/nemotron/nano_v3_reasoning_parser.py",
        "--reasoning-parser", "nano_v3",
        "--no-enable-log-requests", "--no-enable-log-outputs",
        "--no-enable-log-deltas", "--disable-uvicorn-access-log",
        "--disable-fastapi-docs", "--no-trust-request-chat-template",
    ]


if __name__ == "__main__":
    arguments = command(dict(os.environ))
    os.environ["VLLM_API_KEY"] = os.environ.pop("EDURECOVER_NEMOTRON_API_KEY")
    os.execvp(arguments[0], arguments)
