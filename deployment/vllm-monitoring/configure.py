"""Prepare monitoring configuration and a persistent Grafana login at startup."""

import json
import os
from pathlib import Path
import secrets
from urllib.parse import urlsplit


def make_prometheus_config(url: str) -> dict:
    if any(character.isspace() for character in url):
        raise ValueError("VLLM_METRICS_URL must not contain whitespace")
    endpoint = urlsplit(url)
    # Accessing port also rejects malformed or out-of-range port numbers.
    port = endpoint.port
    if (endpoint.scheme not in {"http", "https"} or not endpoint.hostname
            or endpoint.username is not None or endpoint.password is not None
            or endpoint.query or endpoint.fragment):
        raise ValueError("VLLM_METRICS_URL must be an HTTP(S) metrics URL without credentials, query or fragment")
    host = endpoint.hostname
    if ":" in host:
        host = f"[{host}]"
    target = f"{host}:{port or (443 if endpoint.scheme == 'https' else 80)}"
    return {
        "global": {"scrape_interval": "15s", "evaluation_interval": "15s"},
        "scrape_configs": [{
            "job_name": "vllm",
            "scheme": endpoint.scheme,
            "metrics_path": endpoint.path or "/metrics",
            "static_configs": [{"targets": [target]}],
        }],
    }


def configure() -> None:
    config = make_prometheus_config(os.environ.get(
        "VLLM_METRICS_URL", "http://host.docker.internal:8000/metrics"))
    output = Path("/generated")
    output.mkdir(parents=True, exist_ok=True)
    temporary = output / "prometheus.yml.tmp"
    # JSON is valid YAML; no third-party package or shell interpolation is needed.
    temporary.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    temporary.chmod(0o644)
    temporary.replace(output / "prometheus.yml")

    credentials = Path("/credentials")
    credentials.mkdir(parents=True, exist_ok=True)
    password = credentials / "grafana_admin_password"
    try:
        fd = os.open(password, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        if not password.is_file() or not password.read_text(encoding="utf-8").strip():
            raise RuntimeError("The existing Grafana password file is empty or invalid; restore it before restarting")
    else:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(secrets.token_urlsafe(32) + "\n")
    # Grafana's configured container user can read this file, other users cannot.
    os.chown(password, 472, 472)
    password.chmod(0o400)
    print("Monitoring configuration prepared. Existing Grafana credentials were preserved.")


if __name__ == "__main__":
    configure()
