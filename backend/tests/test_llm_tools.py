"""Tests the tool executor against the live seeded DB (same requirement as
test_recommendation.py). No running LLM is needed, since the executor is
the deterministic code a tool call invokes.
"""

import pytest

from app.database.session import SessionLocal
from app.llm.tools import ToolExecutionError, ToolExecutor
from app.models.product import Product


@pytest.fixture(scope="module")
def db():
    session = SessionLocal()
    if session.query(Product).count() == 0:
        pytest.skip("Seed data not loaded. Run `python -m app.ingestion.seed` first.")
    yield session
    session.close()


@pytest.fixture(scope="module")
def executor(db):
    return ToolExecutor(db)


@pytest.fixture(scope="module")
def sample_ids(db):
    def first_id(category):
        return str(db.query(Product).filter(Product.category == category).first().id)

    return {c: first_id(c) for c in ["cpu", "gpu", "motherboard", "ram", "storage", "psu", "cooler", "case"]}


def test_filter_parts_respects_price_cap(executor):
    results = executor.execute("filter_parts", {"category": "gpu", "max_price_php": 20000})
    assert len(results) > 0
    assert all(float(r["price_php"]) <= 20000 for r in results)


def test_get_part_details_returns_full_spec(executor, sample_ids):
    result = executor.execute("get_part_details", {"product_id": sample_ids["cpu"]})
    assert "socket" in result
    assert "manufacturer" in result


def test_get_part_details_raises_for_unknown_id(executor):
    with pytest.raises(ToolExecutionError):
        executor.execute("get_part_details", {"product_id": "00000000-0000-0000-0000-000000000000"})


def test_calculate_power_returns_reasonable_wattage(executor, sample_ids):
    result = executor.execute("calculate_power", {"cpu_id": sample_ids["cpu"], "gpu_id": sample_ids["gpu"]})
    assert result["recommended_psu_watts"] > 0


def test_check_compatibility_via_tool(executor, sample_ids):
    result = executor.execute("check_compatibility", {
        "cpu_id": sample_ids["cpu"], "motherboard_id": sample_ids["motherboard"],
        "ram_id": sample_ids["ram"], "psu_id": sample_ids["psu"],
        "cooler_id": sample_ids["cooler"], "case_id": sample_ids["case"],
        "storage_ids": [sample_ids["storage"]], "gpu_id": sample_ids["gpu"],
    })
    assert "compatible" in result
    assert isinstance(result["results"], list)


def test_compare_parts_returns_all_requested(executor, sample_ids):
    result = executor.execute("compare_parts", {"product_ids": [sample_ids["gpu"], sample_ids["cpu"]]})
    assert len(result) == 2


def test_generate_build_via_tool_returns_jsonable_result(executor):
    result = executor.execute("generate_build", {"budget_php": 70000, "use_case": ["gaming"]})
    assert "error" not in result
    assert result["primary"]["score"]["total_price_php"] <= 70000


def test_generate_build_via_tool_reports_impossible_budget(executor):
    result = executor.execute("generate_build", {"budget_php": 500})
    assert "error" in result
