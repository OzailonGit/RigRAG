"""API-level tests against the live seeded dev database (same requirement
as test_recommendation.py)."""

import pytest
from fastapi.testclient import TestClient

from app.database.session import SessionLocal
from app.main import app
from app.models.product import Product

client = TestClient(app)


@pytest.fixture(scope="module", autouse=True)
def _require_seed_data():
    db = SessionLocal()
    count = db.query(Product).count()
    db.close()
    if count == 0:
        pytest.skip("Seed data not loaded. Run `python -m app.ingestion.seed` first.")


def test_health():
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_list_parts_filters_by_category_and_price():
    response = client.get("/api/parts", params={"category": "gpu", "max_price_php": 25000})
    assert response.status_code == 200
    parts = response.json()
    assert len(parts) > 0
    assert all(p["category"] == "gpu" for p in parts)
    assert all(float(p["price_php"]) <= 25000 for p in parts)


def test_get_single_part():
    listing = client.get("/api/parts", params={"category": "cpu", "limit": 1}).json()
    part_id = listing[0]["id"]

    response = client.get(f"/api/parts/{part_id}")
    assert response.status_code == 200
    assert response.json()["id"] == part_id


def test_get_part_404_for_unknown_id():
    response = client.get("/api/parts/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404


def test_build_endpoint_returns_compatible_build_within_budget():
    response = client.post(
        "/api/build",
        json={
            "budget_php": 70000,
            "use_case": ["gaming"],
            "resolution": "1440p",
            "games": ["Cyberpunk 2077", "Valorant"],
            "cpu_brand": "AMD",
            "wifi_required": True,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["primary"]["score"]["total_price_php"] <= 70000
    assert all(r["compatible"] or r["severity"] != "blocking" for r in body["primary"]["compatibility"])


def test_build_endpoint_422_when_impossible():
    response = client.post("/api/build", json={"budget_php": 500, "use_case": ["gaming"]})
    assert response.status_code == 422


def test_compatibility_endpoint_detects_socket_mismatch():
    cpu = client.get("/api/parts", params={"category": "cpu"}).json()
    mobo = client.get("/api/parts", params={"category": "motherboard"}).json()
    ram = client.get("/api/parts", params={"category": "ram"}).json()
    psu = client.get("/api/parts", params={"category": "psu"}).json()
    cooler = client.get("/api/parts", params={"category": "cooler"}).json()
    case = client.get("/api/parts", params={"category": "case"}).json()
    storage = client.get("/api/parts", params={"category": "storage"}).json()

    am5_cpu = next(c for c in cpu if c["socket"] == "AM5")
    lga1700_mobo = next(m for m in mobo if m["socket"] == "LGA1700")

    response = client.post(
        "/api/compatibility",
        json={
            "cpu_id": am5_cpu["id"],
            "motherboard_id": lga1700_mobo["id"],
            "ram_id": ram[0]["id"],
            "psu_id": psu[0]["id"],
            "cooler_id": cooler[0]["id"],
            "case_id": case[0]["id"],
            "storage_ids": [storage[0]["id"]],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["compatible"] is False
    socket_result = next(r for r in body["results"] if r["rule"] == "cpu_motherboard_socket")
    assert socket_result["compatible"] is False


def test_compare_endpoint_returns_full_specs_for_each_id():
    gpus = client.get("/api/parts", params={"category": "gpu", "limit": 2}).json()
    ids = [g["id"] for g in gpus]

    response = client.post("/api/compare", json={"product_ids": ids})
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2
    assert {p["id"] for p in body} == set(ids)
    assert all("vram_gb" in p for p in body)


def test_compare_endpoint_requires_at_least_two_ids():
    gpu = client.get("/api/parts", params={"category": "gpu", "limit": 1}).json()
    response = client.post("/api/compare", json={"product_ids": [gpu[0]["id"]]})
    assert response.status_code == 422


def test_compare_endpoint_404_for_unknown_id():
    gpu = client.get("/api/parts", params={"category": "gpu", "limit": 1}).json()
    response = client.post(
        "/api/compare",
        json={"product_ids": [gpu[0]["id"], "00000000-0000-0000-0000-000000000000"]},
    )
    assert response.status_code == 404


def test_chat_reports_a_rate_limited_llm_as_503(monkeypatch):
    import httpx

    from app.api.routers import chat as chat_router

    def rate_limited(*args, **kwargs):
        request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
        raise httpx.HTTPStatusError("429", request=request, response=httpx.Response(429, request=request))

    monkeypatch.setattr(chat_router, "handle_chat_message", rate_limited)
    response = client.post("/api/chat", json={"session_id": "rate-limit-test", "message": "Why this GPU?"})
    assert response.status_code == 503
    assert "rate-limited" in response.json()["detail"]


def test_chat_returns_cited_sources_under_the_reply(monkeypatch):
    from app.api.routers import chat as chat_router
    from app.llm.chat_orchestrator import ChatTurnResult
    from app.schemas import BuildRequest

    cited = [{"ref": 1, "note_id": "arc-a-series-resizable-bar", "title": "Arc needs Resizable BAR",
              "checked": "2026-10-20", "sources": [{"publisher": "Intel", "title": "Support article",
                                                    "url": "https://www.intel.com/x", "kind": "manufacturer"}]}]

    def answered(*args, **kwargs):
        return ChatTurnResult(reply="Turn on Resizable BAR [1].", state=BuildRequest(budget_php=40000),
                              build=None, sources=cited)

    monkeypatch.setattr(chat_router, "handle_chat_message", answered)
    monkeypatch.setattr(chat_router, "save_session", lambda *args: None)
    body = client.post("/api/chat", json={"session_id": "sources-test", "message": "Anything to know?"}).json()
    assert body["reply"].startswith("Turn on Resizable BAR [1].\n\n**Sources**")
    assert "[Intel](https://www.intel.com/x)" in body["reply"]
    assert body["sources"][0]["note_id"] == "arc-a-series-resizable-bar"
