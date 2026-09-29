from fastapi import APIRouter

router = APIRouter(prefix="/api/life", tags=["life"])


@router.get("/health")
def life_health() -> dict[str, str]:
    return {"status": "ok", "domain": "life", "wired_to_production": "false"}
