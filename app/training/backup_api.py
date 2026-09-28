"""Owner-only backup operations. Status never implies that a backup ran."""
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app.services.owner_basic_auth import require_owner
from app.training.backup_service import get_backup_service_status, run_configured_backup_once

backup_router = APIRouter(prefix="/owner", tags=["owner"])


@backup_router.get("/backup-status")
async def backup_status(_: None = Depends(require_owner)):
    return JSONResponse(get_backup_service_status(), headers={"Cache-Control": "no-store"})


@backup_router.post("/backup-run")
async def backup_run(_: None = Depends(require_owner)):
    result = await run_in_threadpool(run_configured_backup_once)
    status = result.get("status")
    status_code = 200 if status in {"verified", "not_due"} else 409 if status == "busy" else 503
    # Detailed asset pointers remain in the private receipt store.
    summary = {k: v for k, v in result.items() if k != "assets"}
    return JSONResponse(summary, status_code=status_code, headers={"Cache-Control": "no-store"})
