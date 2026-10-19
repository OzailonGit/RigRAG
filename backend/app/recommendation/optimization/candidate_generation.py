from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.gpu import Gpu
from app.models.motherboard import Motherboard
from app.models.product import Category, Product
from app.schemas import BuildRequest
from app.services.parts_service import to_full_dict

MAX_CANDIDATES_PER_CATEGORY = 6


def fetch_candidates(
    db: Session,
    category: str,
    band: tuple[float, float],
    request: BuildRequest,
) -> list[dict]:
    """Structured-DB candidate pool for one category (the SQL half of hybrid
    retrieval).

    Only two hard SQL filters apply: brand and Wi-Fi preferences the user
    asked for, and the total budget as a ceiling (no single part can cost more
    than the whole build). Products with no price yet are never candidates.
    GPU brand preferences match the chip vendor, so an ASUS card built on an
    NVIDIA chip counts as "NVIDIA". The band from budget_allocator.py is a soft target:
    it affects ranking (closeness to the band midpoint), never exclusion, so a
    slightly pricier motherboard is still considered if the overall build fits.
    """
    band_min, band_max = band
    band_mid = (band_min + band_max) / 2

    stmt = select(Product).where(
        Product.category == Category(category),
        Product.price_php.is_not(None),
        Product.price_php <= request.budget_php,
    )

    if category == "cpu" and request.cpu_brand:
        stmt = stmt.where(Product.manufacturer.ilike(request.cpu_brand))
    elif category == "gpu" and request.gpu_brand:
        stmt = stmt.join(Gpu, Gpu.product_id == Product.id).where(Gpu.chip_vendor.ilike(request.gpu_brand))

    if category == "motherboard" and request.wifi_required:
        stmt = stmt.join(Motherboard, Motherboard.product_id == Product.id).where(Motherboard.wifi.is_(True))

    # Rank by closeness to the band's target spend, not raw price; this is
    # what makes the band a preference rather than a filter. Done in SQL so
    # only the top candidates are loaded, however large the catalog is.
    stmt = stmt.order_by(func.abs(Product.price_php - band_mid), Product.id).limit(MAX_CANDIDATES_PER_CATEGORY)

    return [to_full_dict(db, p) for p in db.scalars(stmt).all()]
