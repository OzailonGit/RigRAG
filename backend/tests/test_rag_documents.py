import datetime
import decimal
import uuid

from app.rag.chunking.document_builder import build_product_document
from app.rag.chunking.payload_builder import build_payload
from app.rag.retrieval.hybrid import build_qdrant_filters
from app.models.product import Category

GPU_PRODUCT = {
    "id": uuid.uuid4(),
    "category": "gpu",
    "manufacturer": "AMD",
    "model": "Radeon RX 9060 XT",
    "price_php": decimal.Decimal("24500.00"),
    "price_type": "estimated",
    "price_updated_at": datetime.datetime(2026, 9, 18, tzinfo=datetime.timezone.utc),
    "source": "manual curation, public PH listings observed 2026-09-18",
    "performance_tier": "mid",
    "vram_gb": 16,
    "vram_type": "GDDR6",
    "tdp_w": 160,
    "recommended_psu_w": 550,
    "pcie_gen": "4.0",
    "length_mm": 240,
}

CPU_PRODUCT = {
    "id": uuid.uuid4(),
    "category": "cpu",
    "manufacturer": "AMD",
    "model": "Ryzen 5 7600",
    "price_php": decimal.Decimal("11500.00"),
    "price_type": "estimated",
    "price_updated_at": datetime.datetime(2026, 9, 18, tzinfo=datetime.timezone.utc),
    "source": "manual curation, public PH listings observed 2026-09-18",
    "performance_tier": "mid",
    "socket": "AM5",
    "cores": 6,
    "threads": 12,
    "base_clock_ghz": 3.8,
    "boost_clock_ghz": 5.1,
    "tdp_w": 65,
    "has_igpu": True,
    "architecture": "Zen 4",
    "generation": "Ryzen 7000",
}


def test_build_gpu_document_contains_key_specs():
    doc = build_product_document(GPU_PRODUCT)
    assert "AMD Radeon RX 9060 XT" in doc
    assert "16GB GDDR6" in doc
    assert "160W TDP" in doc
    assert "24,500" in doc
    assert "mid-tier" in doc


def test_build_cpu_document_mentions_igpu():
    doc = build_product_document(CPU_PRODUCT)
    assert "AM5" in doc
    assert "6 cores" in doc
    assert "Includes integrated graphics." in doc


def test_document_never_invents_fps_claims():
    doc = build_product_document(GPU_PRODUCT)
    assert "fps" not in doc.lower()
    assert "frames per second" not in doc.lower()


def test_payload_is_json_safe():
    payload = build_payload(GPU_PRODUCT)
    assert isinstance(payload["id"], str)
    assert isinstance(payload["price_php"], float)
    assert isinstance(payload["last_updated"], str)
    assert payload["currency"] == "PHP"
    assert payload["document_type"] == "specification"


def test_qdrant_filters_map_correctly():
    filters = build_qdrant_filters(category=Category.gpu, max_price_php=25000, manufacturer="AMD")
    assert filters["category"] == "gpu"
    assert filters["price_php"] == {"lte": 25000}
    assert filters["manufacturer"] == "AMD"


def test_qdrant_filters_empty_when_no_constraints():
    assert build_qdrant_filters() == {}
