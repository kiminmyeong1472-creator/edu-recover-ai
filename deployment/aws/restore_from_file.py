"""Offline restore inside the AWS image with a private mounted passphrase file.

No application startup, network request, original key generation, or secret log.
"""
from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

# Support both the documented module invocation and an explicit script path.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def read_private_passphrase(path: Path) -> str:
    if path.is_symlink():
        raise ValueError("PASSPHRASE_SYMLINK_FORBIDDEN")
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o077 or info.st_size > 64 * 1024:
        raise ValueError("PRIVATE_PASSPHRASE_FILE_REQUIRED")
    value = path.read_text(encoding="utf-8")
    if not value.strip() or "\x00" in value:
        raise ValueError("PASSPHRASE_REQUIRED")
    # Preserve exact bytes represented by UTF-8; do not strip an original value.
    return value


def main() -> int:
    try:
        path = Path("/run/restore-passphrase")
        os.environ["EDURECOVER_MIGRATION_PASSPHRASE"] = read_private_passphrase(path)
        from scripts.aws_migration_snapshot import restore_snapshot
        result = restore_snapshot("/transfer/state.edurmig", "/restore/state", logical_id="DREAMKEEPER-001")
        # Counts and verification path only: no record or passphrase contents.
        import json
        print(json.dumps({"ok": True, **result}, sort_keys=True))
        return 0
    except Exception as exc:
        print("PRIVATE_RESTORE_FAILED error_type=" + type(exc).__name__, file=sys.stderr)
        return 1
    finally:
        os.environ.pop("EDURECOVER_MIGRATION_PASSPHRASE", None)


if __name__ == "__main__":
    raise SystemExit(main())
