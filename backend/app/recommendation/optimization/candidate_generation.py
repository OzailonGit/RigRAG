from sqlalchemy import select
from sqlalchemy.orm import Session

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
    than the whole build). The band from budget_allocator.py is a soft target:
    it affects ranking (closeness to the band midpoint), never exclusion, so a
    slightly pricier motherboard is still considered if the overall build fits.
    """
    band_min, band_max = band
    band_mid = (band_min + band_max) / 2

    stmt = select(Product).where(
        Product.category == Category(category),
        Product.price_php <= request.budget_php,
    )

    brand_pref = None
    if category == "cpu" and request.cpu_brand:
        brand_pref = request.cpu_brand
    elif category == "gpu" and request.gpu_brand:
        brand_pref = request.gpu_brand
    if brand_pref:
        stmt = stmt.where(Product.manufacturer.ilike(brand_pref))

    if category == "motherboard" and request.wifi_required:
        stmt = stmt.join(Motherboard, Motherboard.product_id == Product.id).where(Motherboard.wifi.is_(True))

    products = db.scalars(stmt).all()
    candidates = [to_full_dict(db, p) for p in products]

    # Rank by closeness to the band's target spend, not raw price; this is
    # what makes the band a preference rather than a filter.
    candidates.sort(key=lambda c: abs(float(c["price_php"]) - band_mid))

    return candidates[:MAX_CANDIDATES_PER_CATEGORY]
