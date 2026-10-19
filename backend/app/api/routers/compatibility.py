import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.recommendation.compatibility.build_components import BuildComponents
from app.recommendation.compatibility.checker import CompatibilityChecker
from app.recommendation.compatibility.results import CompatibilityResult
from app.services import parts_service

router = APIRouter()


class CompatibilityRequest(BaseModel):
    cpu_id: uuid.UUID
    motherboard_id: uuid.UUID
    ram_id: uuid.UUID
    psu_id: uuid.UUID
    cooler_id: uuid.UUID
    case_id: uuid.UUID
    storage_ids: list[uuid.UUID] = []
    gpu_id: uuid.UUID | None = None


class CompatibilityResponse(BaseModel):
    compatible: bool
    results: list[CompatibilityResult]


def _load_or_404(db: Session, product_id: uuid.UUID, label: str) -> dict:
    part = parts_service.get_part(db, product_id)
    if part is None:
        raise HTTPException(status_code=404, detail=f"{label} not found: {product_id}")
    return part


@router.post("", response_model=CompatibilityResponse)
def check_compatibility(payload: CompatibilityRequest, db: Session = Depends(get_db)) -> CompatibilityResponse:
    build = BuildComponents(
        cpu=_load_or_404(db, payload.cpu_id, "CPU"),
        motherboard=_load_or_404(db, payload.motherboard_id, "Motherboard"),
        ram=_load_or_404(db, payload.ram_id, "RAM"),
        psu=_load_or_404(db, payload.psu_id, "PSU"),
        cooler=_load_or_404(db, payload.cooler_id, "Cooler"),
        case=_load_or_404(db, payload.case_id, "Case"),
        storage=[_load_or_404(db, sid, "Storage") for sid in payload.storage_ids],
        gpu=_load_or_404(db, payload.gpu_id, "GPU") if payload.gpu_id else None,
    )
    results = CompatibilityChecker.run(build)
    return CompatibilityResponse(
        compatible=not CompatibilityChecker.has_blocking_issue(results),
        results=results,
    )
