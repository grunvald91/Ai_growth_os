from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/version")
def version() -> dict[str, str]:
    from app.core.config import get_settings
    return {"release": get_settings().release_sha}

