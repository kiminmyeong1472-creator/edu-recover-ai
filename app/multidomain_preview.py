from fastapi import FastAPI

from app.domains.life.preview_api import router as life_preview_router
from app.domains.medical.preview_api import router as medical_preview_router
from app.domains.legal.preview_api import router as legal_preview_router


preview_app = FastAPI(
    title="EduRecover Multidomain Preview",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

preview_app.include_router(life_preview_router)
preview_app.include_router(medical_preview_router)
preview_app.include_router(legal_preview_router)


@preview_app.get("/preview/health")
def preview_health() -> dict[str, object]:
    return {
        "status": "ok",
        "production_wired": False,
        "domains": ["life", "medical", "legal"],
    }
