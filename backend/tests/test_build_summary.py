"""Verifies the LLM-facing build summary is correct and actually compact
(see build_summary.py for why size matters).
"""

import json

import pytest

from app.database.session import SessionLocal
from app.llm.build_summary import summarize_build_result
from app.models.product import Product
from app.recommendation.engine import RecommendationEngine
from app.schemas import BuildRequest, UseCase


@pytest.fixture(scope="module")
def db():
    session = SessionLocal()
    if session.query(Product).count() == 0:
        pytest.skip("Seed data not loaded. Run `python -m app.ingestion.seed` first.")
    yield session
    session.close()


@pytest.fixture(scope="module")
def result(db):
    request = BuildRequest(budget_php=70000, use_case=[UseCase.gaming], wifi_required=True, cpu_brand="AMD")
    r = RecommendationEngine.recommend(db, request)
    assert r is not None
    return r


def test_summary_omits_noise_fields(result):
    summary = summarize_build_result(result)
    text = json.dumps(summary)
    # id and timestamps are noise for an explanation and inflate the payload.
    assert "created_at" not in text
    assert "updated_at" not in text
    assert "price_updated_at" not in text


def test_summary_drops_info_level_compatibility_notes(result):
    summary = summarize_build_result(result)
    # A fully compatible build's rules are all "info" severity; none of
    # them are useful to an LLM explaining the build, only to a UI checklist.
    assert summary["primary"]["compatibility_notes"] == []
    assert summary["primary"]["fully_compatible"] is True


def test_summary_includes_readable_part_descriptions(result):
    summary = summarize_build_result(result)
    cpu_text = summary["primary"]["parts"]["cpu"]
    assert isinstance(cpu_text, str)
    assert "socket" in cpu_text.lower()


def test_summary_is_meaningfully_smaller_than_raw_dump(result):
    raw_size = len(json.dumps(result.model_dump(mode="json"), default=str))
    summary_size = len(json.dumps(summarize_build_result(result)))
    assert summary_size < raw_size * 0.6
