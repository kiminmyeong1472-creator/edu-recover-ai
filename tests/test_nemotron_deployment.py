"""Safety checks run without a GPU, Docker, network, or model execution."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deployment/nvidia/nano4b"


@pytest.fixture
def launch(monkeypatch):
    monkeypatch.syspath_prepend(str(DEPLOY))
    spec = importlib.util.spec_from_file_location("nemotron_launch", DEPLOY / "serve.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_server_refuses_missing_placeholder_short_or_whitespace_token(launch):
    for key in (None, "", "short", "change-me" * 8, "x" * 32 + "\n"):
        with pytest.raises(ValueError, match="dedicated random"):
            launch.command({} if key is None else {"EDURECOVER_NEMOTRON_API_KEY": key})


def test_launch_preserves_exact_bf16_and_versions_and_never_exposes_token(launch):
    key = "a" * 64
    args = launch.command({"EDURECOVER_NEMOTRON_API_KEY": key})
    model = "nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16"
    revision = "dfaf35de3e30f1867dd8dbc38a7fc9fb52d3914f"
    assert args[:3] == ["vllm", "serve", model]
    assert args[args.index("--served-model-name") + 1] == model
    for flag in ("--revision", "--code-revision", "--tokenizer-revision"):
        assert args[args.index(flag) + 1] == revision
    assert args[args.index("--dtype") + 1] == "bfloat16"
    assert args[args.index("--max-model-len") + 1] == "262144"
    assert key not in " ".join(args)
    assert "--api-key" not in args
    assert "--no-enable-log-requests" in args
    assert "--no-enable-log-outputs" in args
    assert "--disable-uvicorn-access-log" in args
    assert not any(arg in args for arg in ("--max-tokens", "--max-new-tokens", "--quantization"))


def test_context_changes_are_explicit_validated_and_never_silently_clamped(launch):
    env = {"EDURECOVER_NEMOTRON_API_KEY": "a" * 64}
    args = launch.command(dict(env, NEMOTRON_MAX_MODEL_LEN="8192"))
    assert args[args.index("--max-model-len") + 1] == "8192"
    for value in ("0", "262145", "auto", "-1", "8192 --api-key unsafe", "８１９２"):
        with pytest.raises(ValueError, match="NEMOTRON_MAX_MODEL_LEN"):
            launch.command(dict(env, NEMOTRON_MAX_MODEL_LEN=value))


def test_downloaded_parser_fails_closed_on_any_changed_bytes(monkeypatch):
    monkeypatch.syspath_prepend(str(DEPLOY))
    import verify_parser
    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return None
        def read(self, limit):
            return b"changed upstream code"
    calls = []
    monkeypatch.setattr(verify_parser, "urlopen", lambda url, timeout: (calls.append(url), Response())[1])
    class Destination:
        def write_bytes(self, data):
            pytest.fail("Unverified parser must never be saved")
    with pytest.raises(ValueError, match="checksum mismatch"):
        verify_parser.download(Destination())
    assert calls == [
        "https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16/resolve/"
        "dfaf35de3e30f1867dd8dbc38a7fc9fb52d3914f/nano_v3_reasoning_parser.py"
    ]


def test_only_tls_proxy_ports_are_published_and_no_private_env_enters_build():
    compose = (DEPLOY / "compose.yaml").read_text()
    model, proxy = compose.split("  proxy:", 1)
    assert "ports:" not in model
    assert '"80:80"' in proxy and '"443:443"' in proxy
    assert "8000:8000" not in compose
    assert "${EDURECOVER_NEMOTRON_API_KEY:?" in model
    assert "${NEMOTRON_DOMAIN:?" in proxy and "${ACME_EMAIL:?" in proxy
    assert compose.count("driver: none") == 2
    dockerfile = (DEPLOY / "Dockerfile").read_text()
    assert "vllm/vllm-openai:v0.15.1" in dockerfile
    assert "COPY . " not in dockerfile
    assert ".env" in (DEPLOY / ".dockerignore").read_text().splitlines()
    caddy = (DEPLOY / "Caddyfile").read_text()
    assert "admin off" in caddy
    assert "path /v1/chat/completions" in caddy and "path /v1/models" in caddy
    assert "respond 404" in caddy
    assert "path /v1/*" not in caddy
