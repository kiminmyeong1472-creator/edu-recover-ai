from fastapi import APIRouter

router = APIRouter(prefix="/api/legal", tags=["legal"])


@router.get("/health")
def legal_health() -> dict[str, str]:
    return {"status": "ok", "domain": "legal", "wired_to_production": "false"}
