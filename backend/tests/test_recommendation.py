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
