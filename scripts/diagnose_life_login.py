"""Read-only login diagnostics for the service console; no credentials printed."""
from __future__ import annotations

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.platform_core.life_auth import login_readiness


if __name__ == "__main__":
    result = login_readiness()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["ready"] else 2)
