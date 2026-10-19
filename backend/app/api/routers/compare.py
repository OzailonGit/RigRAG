import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.services import parts_service

router = APIRouter()


class CompareRequest(BaseModel):
    product_ids: list[uuid.UUID]


@router.post("")
def compare(payload: CompareRequest, db: Session = Depends(get_db)) -> list[dict]:
    if len(payload.product_ids) < 2:
        raise HTTPException(status_code=422, detail="Provide at least two product IDs to compare.")

    results = []
    for product_id in payload.product_ids:
        part = parts_service.get_part(db, product_id)
        if part is None:
            raise HTTPException(status_code=404, detail=f"Part not found: {product_id}")
        results.append(part)
    return results
