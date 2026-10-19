import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.models.product import Category
from app.services import parts_service

router = APIRouter()


@router.get("")
def list_parts(
    category: Category | None = None,
    max_price_php: float | None = Query(default=None, ge=0),
    manufacturer: str | None = None,
    limit: int = Query(default=100, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> list[dict]:
    return parts_service.list_parts(
        db,
        category=category,
        max_price_php=max_price_php,
        manufacturer=manufacturer,
        limit=limit,
        offset=offset,
    )


@router.get("/{product_id}")
def get_part(product_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    part = parts_service.get_part(db, product_id)
    if part is None:
        raise HTTPException(status_code=404, detail="Part not found")
    return part
