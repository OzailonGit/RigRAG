from sqlalchemy import and_, exists, func, literal, or_, select
from sqlalchemy.orm import Session, aliased

from app.models.case import Case
from app.models.cooler import CpuCooler
from app.models.gpu import Gpu
from app.models.motherboard import Motherboard
from app.models.product import Category, Product
from app.models.psu import Psu
from app.models.ram import RamKit
from app.models.storage import StorageDevice, StorageType
from app.recommendation.optimization.sizing import ram_capacity_range, storage_capacity_range
from app.recommendation.scoring.performance import speed_column, speed_tier, tier_columns
from app.recommendation.scoring.tiers import tier_ordinal
from app.schemas import BuildRequest
from app.services.parts_service import SPEC_MODEL_BY_CATEGORY, to_full_dict

MAX_CANDIDATES_PER_CATEGORY = 8

# The second axis a part is compared on besides tier. A cheaper part only
# replaces a pricier one if it matches both: a 16GB kit is no substitute for
# a 64GB kit of the same tier. CPUs, boards, coolers, and cases compare on
# tier alone.
CAPACITY_COLUMN = {
    "gpu": Gpu.vram_gb,
    "ram": RamKit.capacity_gb,
    "storage": StorageDevice.capacity_gb,
    "psu": Psu.wattage,
}

# Whether a part's compatibility specs are known (see "unverified" in
# compatibility/rules.py). Verified parts get their own candidate slots, so
# a cheaper unverified case or cooler never crowds them out of the pool.
VERIFIED_EXPR = {
    "case": Case.max_gpu_length_mm.is_not(None) & Case.max_cooler_height_mm.is_not(None),
    "cooler": func.jsonb_array_length(CpuCooler.socket_support) > 0,
}


def _chip_key(chip_column):
    """"GeForce RTX 4060 Ti" and the seed's "RTX 4060 Ti 16GB" both become
    "rtx 4060 ti"; VRAM is compared separately."""
    return func.regexp_replace(func.lower(chip_column), r"^geforce |\s*\d+\s*gb$", "", "g")


def _generic_gpu_with_real_cards():
    """A seed GPU named after its chip ("NVIDIA RTX 4060") stands for no
    card in particular. Once the catalog has priced cards on that chip and
    memory size, those are recommended instead, so a build names a card the
    user can buy."""
    real_gpu, real_product = aliased(Gpu), aliased(Product)
    has_real_cards = exists().where(
        real_gpu.product_id == real_product.id,
        real_product.price_php.is_not(None),
        or_(real_product.dataset_key.is_not(None), real_product.icecat_id.is_not(None)),
        _chip_key(real_gpu.chip) == _chip_key(Gpu.chip),
        real_gpu.vram_gb == Gpu.vram_gb,
    )
    return and_(
        Product.dataset_key.is_(None),
        Product.icecat_id.is_(None),
        func.lower(Product.manufacturer) == func.lower(Gpu.chip_vendor),
        has_real_cards,
    )


def _filters(category: str, request: BuildRequest, constraints: dict) -> list:
    filters = [
        Product.category == Category(category),
        Product.price_php.is_not(None),
        Product.price_php <= request.budget_php,
    ]
    if category == "cpu" and request.cpu_brand:
        filters.append(Product.manufacturer.ilike(request.cpu_brand))
    elif category == "gpu" and request.gpu_brand:
        filters.append(Gpu.chip_vendor.ilike(request.gpu_brand))
    if category == "gpu":
        filters.append(~_generic_gpu_with_real_cards())
    if category == "motherboard" and request.wifi_required:
        filters.append(Motherboard.wifi.is_(True))

    if category == "ram":
        low, high = ram_capacity_range(request)
        # Single sticks halve memory bandwidth, so only dual-channel kits.
        filters += [RamKit.capacity_gb.between(low, high), RamKit.sticks >= 2]
    if category == "storage":
        low, high = storage_capacity_range(request)
        # The build's only drive is the boot drive, so it is an SSD.
        filters += [StorageDevice.capacity_gb.between(low, high), StorageDevice.type != StorageType.hdd]

    # SQL equivalents of the compatibility rules whose inputs are already chosen.
    if category == "motherboard" and "socket" in constraints:
        filters.append(Motherboard.socket == constraints["socket"])
    if category == "cooler" and "socket" in constraints:
        filters.append(or_(
            CpuCooler.socket_support.contains([constraints["socket"]]),
            func.jsonb_array_length(CpuCooler.socket_support) == 0,
        ))
    if category == "ram" and "ram_type" in constraints:
        filters.append(RamKit.ddr_gen == constraints["ram_type"])
    if category == "case" and "form_factor" in constraints:
        filters.append(Case.form_factor_support.contains([constraints["form_factor"]]))
    if category == "psu" and "wattage" in constraints:
        filters.append(Psu.wattage.between(*constraints["wattage"]))
    return filters


def _frontier(rows: list[tuple]) -> list[tuple]:
    """Drops dominated parts: those where a cheaper part is at least as good
    on performance, capacity, and verified specs. Without this, a large
    catalog's overpriced discontinued parts win whenever their price sits
    near the band target.

    Rows are (id, price, performance, capacity, verified, ...) sorted by
    price, where performance is the benchmark score for CPUs and GPUs and
    the tier for everything else. Checking only kept rows is enough, since
    dominance is transitive.
    """
    kept: list[tuple] = []
    for row in rows:
        _, _, performance, capacity, verified = row[:5]
        if not any(k[2] >= performance and k[3] >= capacity and k[4] >= verified for k in kept):
            kept.append(row)
    return kept


def fetch_candidates(
    db: Session,
    category: str,
    band: tuple[float, float],
    request: BuildRequest,
    constraints: dict | None = None,
) -> list[dict]:
    """Structured-DB candidate pool for one category (the SQL half of hybrid
    retrieval).

    Hard filters: brand and Wi-Fi preferences the user asked for, the total
    budget as a ceiling (no single part can cost more than the whole build),
    and `constraints` from parts already in the build (CPU socket, board RAM
    type and form factor, PSU wattage range), so every candidate pairs
    with at least one partial build. Products with no price yet are never
    candidates. GPU brand preferences match the chip vendor, so an ASUS card
    built on an NVIDIA chip counts as "NVIDIA".

    Among parts that aren't dominated (see _frontier), each tier contributes
    the part closest to the band's target spend and its cheapest part, once
    for verified parts and once for unverified ones. The
    band is a soft target: it affects ranking, never exclusion, so a
    slightly pricier motherboard is still considered if the overall build fits.
    """
    band_min, band_max = band
    band_mid = (band_min + band_max) / 2
    spec_model = SPEC_MODEL_BY_CATEGORY[Category(category)]
    capacity = CAPACITY_COLUMN.get(category)

    speed = speed_column(category, request)
    columns = [
        Product.id,
        Product.price_php,
        Product.performance_tier,
        capacity if capacity is not None else literal(0),
        VERIFIED_EXPR.get(category, literal(True)),
        speed if speed is not None else literal(None),
    ]
    scored_fields = tier_columns(category, request)
    columns += list(scored_fields.values())
    stmt = (
        select(*columns)
        .join(spec_model, spec_model.product_id == Product.id)
        .where(*_filters(category, request, constraints or {}))
        .order_by(Product.price_php, Product.id)
    )
    rows = []
    for r in db.execute(stmt):
        # The tier the benchmark scores give for this request, as scoring
        # uses; the stored tier on a catalog without benchmarks.
        scores = dict(zip(scored_fields, r[6:]))
        tier = speed_tier(scores, category, request) or tier_ordinal(r[2].value if r[2] else None)
        performance = float(r[5]) if r[5] is not None else tier * 20.0
        rows.append((r[0], float(r[1]), performance, r[3] or 0, bool(r[4]), tier))

    frontier = _frontier(rows)
    by_tier: dict[tuple, list[tuple]] = {}
    for row in frontier:
        by_tier.setdefault((row[5], row[4]), []).append(row)
    chosen = {}
    for tier_rows in by_tier.values():
        closest = min(tier_rows, key=lambda r: abs(r[1] - band_mid))
        chosen[tier_rows[0][0]] = tier_rows[0]
        chosen[closest[0]] = closest

    ranked = sorted(chosen.values(), key=lambda r: (abs(r[1] - band_mid), r[1]))[:MAX_CANDIDATES_PER_CATEGORY]
    # The fastest part the band can pay for always gets a slot, so a large
    # budget has something to spend on.
    affordable = [r for r in frontier if r[1] <= band_max]
    if speed is not None and affordable:
        fastest = max(affordable, key=lambda r: (r[2], -r[1]))
        if fastest not in ranked:
            ranked = [*ranked[: MAX_CANDIDATES_PER_CATEGORY - 1], fastest]
    products = {p.id: p for p in db.scalars(select(Product).where(Product.id.in_([r[0] for r in ranked])))}
    return [to_full_dict(db, products[r[0]]) for r in ranked]
