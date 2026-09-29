from fastapi import APIRouter

router = APIRouter(prefix="/api/medical", tags=["medical"])


@router.get("/health")
def medical_health() -> dict[str, str]:
    return {"status": "ok", "domain": "medical", "wired_to_production": "false"}
