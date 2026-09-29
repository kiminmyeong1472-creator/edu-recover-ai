"""Opt-in backup loop in the process that owns the runtime data volume."""
import asyncio
import logging
import os

from starlette.concurrency import run_in_threadpool

from app.training.backup_service import get_backup_service_status, run_configured_backup_once

logger = logging.getLogger(__name__)
_task: asyncio.Task | None = None


async def _backup_loop():
    while True:
        try:
            result = await run_in_threadpool(run_configured_backup_once)
            logger.info("backup_cycle_status=%s receipt_persisted=%s", result.get("status"),
                        bool(result.get("receipt_persisted")))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("backup_cycle_status=failed error_type=%s", type(exc).__name__)
        await asyncio.sleep(30 * 60)


async def start_backup_scheduler():
    global _task
    if os.getenv("EDURECOVER_BACKUP_SCHEDULER_ENABLED", "").strip().lower() not in {"1", "true", "yes"}:
        return
    if not get_backup_service_status()["configuration_ready"]:
        logger.warning("backup_scheduler_not_started=configuration_blocked")
        return
    if _task is None or _task.done():
        _task = asyncio.create_task(_backup_loop(), name="edurecover-backup")


async def stop_backup_scheduler():
    global _task
    if _task is not None:
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
        _task = None
