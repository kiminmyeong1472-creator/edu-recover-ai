import asyncio
import os
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.training.backup_api import backup_router
from app.training import backup_scheduler


def test_backup_api_requires_owner_and_reports_blocked_as_failure():
    app = FastAPI()
    app.include_router(backup_router)
    with patch.dict(os.environ, {"EDURECOVER_OWNER_USER": "test-owner",
                                "EDURECOVER_OWNER_PASSWORD": "test-password"}), TestClient(app) as client:
        with patch("app.training.backup_api.run_configured_backup_once") as worker:
            assert client.post("/owner/backup-run").status_code == 401
            worker.assert_not_called()
        with patch("app.training.backup_api.run_configured_backup_once", return_value={
            "status": "blocked", "receipt_persisted": False, "blockers": ["NO_VOLUME"],
            "assets": [{"storage_ref": "private-location"}],
        }):
            result = client.post("/owner/backup-run", auth=("test-owner", "test-password"))
            assert result.status_code == 503
            assert "assets" not in result.json()
            assert result.json()["receipt_persisted"] is False


def test_scheduler_never_starts_for_missing_storage_configuration():
    async def check():
        with patch.dict(os.environ, {"EDURECOVER_BACKUP_SCHEDULER_ENABLED": "true"}), \
             patch.object(backup_scheduler, "get_backup_service_status", return_value={"configuration_ready": False}), \
             patch.object(backup_scheduler, "_backup_loop", new_callable=AsyncMock) as loop:
            await backup_scheduler.start_backup_scheduler()
            assert backup_scheduler._task is None
            loop.assert_not_called()
    asyncio.run(check())


def test_configured_scheduler_starts_only_one_worker_and_stops_cleanly():
    async def check():
        called = asyncio.Event()
        async def worker():
            called.set()
            await asyncio.Event().wait()
        with patch.dict(os.environ, {"EDURECOVER_BACKUP_SCHEDULER_ENABLED": "true"}), \
             patch.object(backup_scheduler, "get_backup_service_status", return_value={"configuration_ready": True}), \
             patch.object(backup_scheduler, "_backup_loop", new=worker):
            await backup_scheduler.start_backup_scheduler()
            first = backup_scheduler._task
            await backup_scheduler.start_backup_scheduler()
            assert backup_scheduler._task is first
            await called.wait()
            await backup_scheduler.stop_backup_scheduler()
            assert backup_scheduler._task is None
    asyncio.run(check())
