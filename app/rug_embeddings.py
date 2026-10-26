"""Optional offline sentence embeddings; never download during app requests."""
from __future__ import annotations

import hashlib
import math
import os
from functools import lru_cache
from pathlib import Path


class LocalEncoder:
    def __init__(self, path: Path):
        from sentence_transformers import SentenceTransformer
        self.model = SentenceTransformer(str(path), local_files_only=True, trust_remote_code=False, device='cpu')
        stamp = '|'.join(f'{p.relative_to(path)}:{p.stat().st_size}:{p.stat().st_mtime_ns}'
                         for p in sorted(path.rglob('*')) if p.is_file())
        self.key = hashlib.sha256(stamp.encode()).hexdigest()
        self.name = path.name

    def encode(self, texts: list[str]) -> list[list[float]]:
        return self.model.encode(texts, normalize_embeddings=True, show_progress_bar=False).tolist()


@lru_cache(maxsize=2)
def _load(path: str):
    folder = Path(path)
    if not folder.is_dir():
        return None, 'local_model_directory_missing'
    try:
        return LocalEncoder(folder.resolve()), ''
    except (ImportError, OSError, ValueError, RuntimeError, TypeError):
        return None, 'local_embedding_model_unavailable'


def configured_encoder():
    path = os.getenv('RUG_EMBEDDING_MODEL_PATH', '').strip()
    if not path:
        return None, 'disabled'
    return _load(path)


def vector(value) -> list[float]:
    result = [float(x) for x in value]
    if not result or len(result) > 8192 or not all(math.isfinite(x) for x in result):
        raise ValueError('invalid_embedding_vector')
    length = math.sqrt(sum(x*x for x in result))
    if not length:
        raise ValueError('zero_embedding_vector')
    return [x/length for x in result]


def cosine(a, b) -> float:
    if len(a) != len(b):
        return -1.0
    return sum(x*y for x,y in zip(a,b))
