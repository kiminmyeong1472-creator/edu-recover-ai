"""Download a reviewed NVIDIA parser; never import or execute downloaded code."""
import hashlib
from pathlib import Path
from urllib.request import urlopen

MODEL = "nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16"
REVISION = "dfaf35de3e30f1867dd8dbc38a7fc9fb52d3914f"
PARSER_SHA256 = "aafb12208054504f619cbdd01837e1532a482ad937ed987bfe9a13fb812ae2b7"
PARSER_URL = f"https://huggingface.co/{MODEL}/resolve/{REVISION}/nano_v3_reasoning_parser.py"


def verified_parser_bytes(data: bytes) -> bytes:
    if hashlib.sha256(data).hexdigest() != PARSER_SHA256:
        raise ValueError("NVIDIA reasoning parser checksum mismatch")
    return data


def download(destination: Path) -> None:
    with urlopen(PARSER_URL, timeout=60) as response:
        data = response.read(1024 * 1024 + 1)
    if len(data) > 1024 * 1024:
        raise ValueError("Parser exceeds expected size")
    destination.write_bytes(verified_parser_bytes(data))


if __name__ == "__main__":
    download(Path("/opt/nemotron/nano_v3_reasoning_parser.py"))
