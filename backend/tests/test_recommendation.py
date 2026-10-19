"""Integration tests for the recommendation engine against the seeded dev
database (Postgres via docker/docker-compose.yml, populated by
`python -m app.ingestion.seed`).

Unlike test_compatibility.py, these are not pure unit tests: the engine's
job is to search real structured data, and that is what needs verifying.
"""

import pytest

from app.database.session import SessionLocal
from app.models.product import Product
from app.recommendation.compatibility.checker import CompatibilityChecker
from app.recommendation.engine import RecommendationEngine
from app.schemas import BuildRequest, Resolution, UseCase


@pytest.fixture(scope="module")
def db():
    session = SessionLocal()
    if session.query(Product).count() == 0:
        pytest.skip("Seed data not loaded. Run `python -m app.ingestion.seed` first.")
    yield session
    session.close()


def test_gaming_build_is_fully_compatible_and_in_budget(db):
    request = BuildRequest(
        budget_php=70000,
        use_case=[UseCase.gaming],
        resolution=Resolution.r1440p,
        games=["Cyberpunk 2077", "Valorant"],
        cpu_brand="AMD",
        wifi_required=True,
    )
    result = RecommendationEngine.recommend(db, request)

    assert result is not None
    assert result.primary.score.total_price_php <= request.budget_php
    assert CompatibilityChecker.has_blocking_issue(result.primary.compatibility) is False
    assert result.primary.components.cpu["socket"] == result.primary.components.motherboard["socket"]
    assert result.primary.components.cpu["manufacturer"] == "AMD"
    assert result.primary.components.motherboard["wifi"] is True


def test_ram_matches_motherboard_ram_type(db):
    request = BuildRequest(budget_php=50000, use_case=[UseCase.gaming], resolution=Resolution.r1080p)
    result = RecommendationEngine.recommend(db, request)

    assert result is not None
    assert result.primary.components.ram["ddr_gen"] == result.primary.components.motherboard["ram_type"]


def test_impossible_budget_returns_none(db):
    request = BuildRequest(budget_php=500, use_case=[UseCase.gaming])
    result = RecommendationEngine.recommend(db, request)
    assert result is None


def test_existing_parts_are_reused_not_repurchased(db):
    owned_case = db.query(Product).filter(Product.category == "case").first()
    request = BuildRequest(
        budget_php=60000,
        use_case=[UseCase.gaming],
        existing_parts={"case": owned_case.id},
    )
    result = RecommendationEngine.recommend(db, request)

    assert result is not None
    assert str(result.primary.components.case["id"]) == str(owned_case.id)


def test_pinned_part_is_used_and_its_price_counted(db):
    from app.models.product import Category

    cpu = (db.query(Product)
           .filter(Product.category == Category.cpu, Product.price_php.is_not(None))
           .order_by(Product.price_php).first())
    request = BuildRequest(budget_php=70000, use_case=[UseCase.gaming], pinned_parts={"cpu": cpu.id})
    result = RecommendationEngine.recommend(db, request)

    assert result is not None
    components = result.primary.components
    assert str(components.cpu["id"]) == str(cpu.id)
    parts = [components.cpu, components.motherboard, components.ram, components.psu,
             components.cooler, components.case, *components.storage]
    if components.gpu:
        parts.append(components.gpu)
    assert result.primary.score.total_price_php == pytest.approx(sum(float(p["price_php"]) for p in parts))
    assert result.primary.score.total_price_php <= request.budget_php


def test_pin_for_a_missing_part_returns_none(db):
    import uuid

    request = BuildRequest(budget_php=70000, use_case=[UseCase.gaming], pinned_parts={"cpu": uuid.uuid4()})
    assert RecommendationEngine.recommend(db, request) is None


@pytest.mark.parametrize("budget", [50000, 120000, 600000])
def test_budget_is_spent_or_the_reason_it_isnt_is_given(db, budget):
    request = BuildRequest(budget_php=budget, use_case=[UseCase.gaming])
    result = RecommendationEngine.recommend(db, request)

    assert result is not None
    total = result.primary.score.total_price_php
    assert total <= budget
    assert total >= 0.85 * budget or result.unspent_reason in ("top_tier", "next_tier_over_budget")


def test_bigger_budget_buys_at_least_as_fast_a_build(db):
    from app.recommendation.scoring.tiers import tier_ordinal

    def tiers(budget):
        components = RecommendationEngine.recommend(db, BuildRequest(budget_php=budget, use_case=[UseCase.gaming])).primary.components
        return tier_ordinal(components.cpu["performance_tier"]), tier_ordinal(components.gpu["performance_tier"])

    small, large = tiers(50000), tiers(300000)
    assert large[0] >= small[0] and large[1] >= small[1]


@pytest.mark.parametrize("budget", [40000, 70000, 120000])
def test_alternatives_differ_meaningfully_from_the_primary(db, budget):
    from app.recommendation.engine import MIN_SAVING_PHP, MIN_SAVING_SHARE

    result = RecommendationEngine.recommend(db, BuildRequest(budget_php=budget, use_case=[UseCase.gaming], resolution=Resolution.r1440p))
    price = result.primary.score.total_price_php
    warnings = sum(1 for r in result.primary.compatibility if r.severity == "warning")
    for alt in result.alternatives:
        alt_price = alt.score.total_price_php
        if alt.label == "Lower cost":
            assert price - alt_price >= max(MIN_SAVING_PHP, price * MIN_SAVING_SHARE)
            assert sum(1 for r in alt.compatibility if r.severity == "warning") <= warnings
        elif alt.label == "Higher performance":
            assert alt_price > price


def test_owned_parts_are_not_counted_in_the_total(db):
    owned_case = (db.query(Product).filter(Product.category == "case", Product.price_php.is_not(None))
                  .order_by(Product.price_php.desc()).first())
    request = BuildRequest(budget_php=60000, use_case=[UseCase.gaming], existing_parts={"case": owned_case.id})
    result = RecommendationEngine.recommend(db, request)

    components = result.primary.components
    assert components.case.get("owned") is True
    paid = [components.cpu, components.motherboard, components.ram, components.psu, components.cooler, *components.storage]
    if components.gpu:
        paid.append(components.gpu)
    assert result.primary.score.total_price_php == pytest.approx(sum(float(p["price_php"]) for p in paid))


def test_generic_seed_gpus_give_way_to_real_cards(db):
    from app.models.gpu import Gpu

    if db.query(Product).filter(Product.category == "gpu", Product.dataset_key.is_not(None)).count() == 0:
        pytest.skip("Only the seed catalog is loaded")
    for budget in (40000, 70000, 120000):
        gpu = RecommendationEngine.recommend(db, BuildRequest(budget_php=budget, use_case=[UseCase.gaming])).primary.components.gpu
        product = db.get(Product, gpu["id"])
        chip_vendor = db.get(Gpu, gpu["id"]).chip_vendor
        assert not (product.dataset_key is None and product.icecat_id is None
                    and product.manufacturer.lower() == (chip_vendor or "").lower())


def test_bigger_budget_buys_a_measurably_faster_build(db):
    def speed(budget):
        return RecommendationEngine.recommend(
            db, BuildRequest(budget_php=budget, use_case=[UseCase.gaming], resolution=Resolution.r1440p)
        ).primary.score.speed

    speeds = [speed(b) for b in (50000, 80000, 120000, 200000)]
    assert speeds == sorted(speeds)
    assert speeds[-1] > speeds[0]
