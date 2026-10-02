"""Initialize only app-owned storage beneath the existing classroom volume."""
import json
import logging
import os
from pathlib import Path

from app.services.secret_vault import SecretVault, import_legacy_assets, state_root

logger = logging.getLogger("uvicorn.error")


def prepare_runtime_storage():
    root = state_root()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.is_symlink():
        raise RuntimeError("CLASSROOM_STATE_SYMLINK_FORBIDDEN")
    root.chmod(0o700)
    # Create classroom schema before registering it for consistent online snapshots.
    from app.classroom_tutor import db
    with db():
        pass
    vault = SecretVault()
    count = import_legacy_assets(vault)
    logger.info("secret_vault_ready=true imported_assets=%s", count)
    for name in ("backups", "backup_state", "oauth_private"):
        p = root / name
        if p.is_symlink():
            raise RuntimeError("PRIVATE_STORAGE_SYMLINK_FORBIDDEN")
        p.mkdir(mode=0o700, exist_ok=True)
        p.chmod(0o700)
    from app.training.backup_service import atomic_json
    manifest = root / "backup_manifest.json"
    if not manifest.exists():
        atomic_json(manifest, [
            {"asset_id": "classroom-state", "source": "classroom_tutor.sqlite3", "kind": "TRAINING_RECORD",
             "contains_personal_data": True, "provenance_complete": True, "sqlite_snapshot": True},
            {"asset_id": "tutor-learning-evidence", "source": "ai_tutor_training.sqlite3", "kind": "TRAINING_RECORD",
             "contains_personal_data": True, "provenance_complete": True, "sqlite_snapshot": True},
            {"asset_id": "private-teaching-assets", "source": "secret_vault.sqlite3", "kind": "PRODUCT_DESIGN",
             "owner_marked_trade_secret": True, "provenance_complete": True, "sqlite_snapshot": True},
        ])
    for key, value in {
        "EDURECOVER_BACKUP_MANIFEST": str(manifest.resolve()),
        "EDURECOVER_BACKUP_SOURCE_ROOT": str(root.resolve()),
        "EDURECOVER_BACKUP_SERVER_ROOT": str((root / "backups").resolve()),
        "EDURECOVER_BACKUP_STATE_DIR": str((root / "backup_state").resolve()),
    }.items():
        os.environ.setdefault(key, value)
    # Activation is opt-in; the Drive consent callback enables the existing scheduler.
    activation = root / "backup_activation.json"
    try:
        enabled = not activation.is_symlink() and json.loads(activation.read_text()).get("enabled") is True
    except (OSError, ValueError):
        enabled = False
    if enabled:
        os.environ.setdefault("EDURECOVER_BACKUP_ENABLED", "true")
        os.environ.setdefault("EDURECOVER_BACKUP_SCHEDULER_ENABLED", "true")


def log_backup_readiness():
    """Report bounded configuration flags after storage preparation, never values."""
    from app.training.backup_service import get_backup_service_status
    try:
        status = get_backup_service_status()
        public = {name: status.get(name) is True for name in (
            "enabled", "configuration_ready", "persistent_volume_verified",
            "cloud_access_verified",
        )}
        public["blockers"] = [code for code in status.get("blockers", [])
                              if isinstance(code, str) and len(code) <= 100
                              and code.replace("_", "").isalnum() and code.isupper()][:32]
        logger.info("backup_readiness=%s", json.dumps(public, sort_keys=True))
    except Exception as exc:
        logger.warning("backup_readiness=UNAVAILABLE error_type=%s", type(exc).__name__)
