"""Tool definitions and executor for LLM function calling.

Every tool is a thin wrapper around existing, tested code (parts_service,
hybrid_search, CompatibilityChecker, RecommendationEngine). The LLM decides
when to call them and how to phrase the answer; specs, prices, and
compatibility verdicts only ever reach it as tool results.
"""

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.json_utils import to_jsonable
from app.models.product import Category
from app.rag.embeddings.factory import get_embedding_provider
from app.rag.retrieval.hybrid import hybrid_search
from app.rag.vectorstore.qdrant_store import QdrantVectorStore
from app.recommendation.compatibility.build_components import BuildComponents
from app.recommendation.compatibility.checker import CompatibilityChecker
from app.recommendation.compatibility.power import recommended_psu_watts
from app.recommendation.engine import RecommendationEngine
from app.schemas import BuildRequest
from app.services import parts_service

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_parts",
            "description": "Semantic search for PC parts matching a natural-language description (e.g. 'quiet 1440p GPU'), optionally filtered by category/price/manufacturer.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "category": {"type": "string", "enum": [c.value for c in Category]},
                    "max_price_php": {"type": "number"},
                    "manufacturer": {"type": "string"},
                    "limit": {"type": "integer", "default": 5},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_part_details",
            "description": "Get full specifications for one specific part by its product ID.",
            "parameters": {
                "type": "object",
                "properties": {"product_id": {"type": "string"}},
                "required": ["product_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "filter_parts",
            "description": "List parts in a category matching hard structured filters (price, manufacturer). Use this instead of search_parts when there's no fuzzy/semantic aspect to the query.",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "enum": [c.value for c in Category]},
                    "max_price_php": {"type": "number"},
                    "manufacturer": {"type": "string"},
                    "limit": {"type": "integer", "default": 10},
                },
                "required": ["category"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_compatibility",
            "description": "Run the deterministic compatibility checker against a specific set of part IDs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "cpu_id": {"type": "string"},
                    "motherboard_id": {"type": "string"},
                    "ram_id": {"type": "string"},
                    "psu_id": {"type": "string"},
                    "cooler_id": {"type": "string"},
                    "case_id": {"type": "string"},
                    "storage_ids": {"type": "array", "items": {"type": "string"}},
                    "gpu_id": {"type": "string"},
                },
                "required": ["cpu_id", "motherboard_id", "ram_id", "psu_id", "cooler_id", "case_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate_power",
            "description": "Estimate required system wattage (with headroom) for a CPU + optional GPU.",
            "parameters": {
                "type": "object",
                "properties": {
                    "cpu_id": {"type": "string"},
                    "gpu_id": {"type": "string"},
                },
                "required": ["cpu_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compare_parts",
            "description": "Fetch full specs for several parts side by side, for comparison.",
            "parameters": {
                "type": "object",
                "properties": {"product_ids": {"type": "array", "items": {"type": "string"}}},
                "required": ["product_ids"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_build",
            "description": "Run the full recommendation engine: given a budget and requirements, return a ranked, compatibility-checked PC build with alternatives.",
            "parameters": {
                "type": "object",
                "properties": {
                    "budget_php": {"type": "number"},
                    "use_case": {"type": "array", "items": {"type": "string"}},
                    "resolution": {"type": "string", "enum": ["1080p", "1440p", "4K"]},
                    "target_fps": {"type": "integer"},
                    "games": {"type": "array", "items": {"type": "string"}},
                    "cpu_brand": {"type": "string"},
                    "gpu_brand": {"type": "string"},
                    "wifi_required": {"type": "boolean"},
                    "small_form_factor": {"type": "boolean"},
                    "quiet": {"type": "boolean"},
                    "low_power": {"type": "boolean"},
                    "prioritize_upgradeability": {"type": "boolean"},
                },
                "required": ["budget_php"],
            },
        },
    },
]


class ToolExecutionError(Exception):
    pass


def _find_parts(value: Any, found: dict[str, dict]) -> None:
    """Collects every catalog part (a dict with an id, category, and model)
    nested anywhere in a tool result."""
    if isinstance(value, dict):
        if {"id", "category", "manufacturer", "model"} <= value.keys():
            found.setdefault(str(value["id"]), value)
        for item in value.values():
            _find_parts(item, found)
    elif isinstance(value, list):
        for item in value:
            _find_parts(item, found)


class ToolExecutor:
    def __init__(self, db: Session):
        self._db = db
        self._embedder = None
        self._store = None
        # Every part a tool returned during this executor's lifetime, by ID,
        # so the caller can tell which parts an answer was based on.
        self.seen_parts: dict[str, dict] = {}

    def _lazy_rag(self):
        if self._embedder is None:
            self._embedder = get_embedding_provider()
            self._store = QdrantVectorStore()
        return self._embedder, self._store

    def execute(self, name: str, arguments: dict[str, Any]) -> Any:
        handler = getattr(self, f"_tool_{name}", None)
        if handler is None:
            raise ToolExecutionError(f"Unknown tool: {name}")
        result = to_jsonable(handler(**arguments))
        _find_parts(result, self.seen_parts)
        return result

    def _tool_search_parts(self, query: str, category: str | None = None, max_price_php: float | None = None, manufacturer: str | None = None, limit: int = 5):
        embedder, store = self._lazy_rag()
        return hybrid_search(
            self._db, embedder, store, query=query,
            category=Category(category) if category else None,
            max_price_php=max_price_php, manufacturer=manufacturer, limit=limit,
        )

    def _tool_get_part_details(self, product_id: str):
        part = parts_service.get_part(self._db, uuid.UUID(product_id))
        if part is None:
            raise ToolExecutionError(f"No part found with id {product_id}")
        return part

    def _tool_filter_parts(self, category: str, max_price_php: float | None = None, manufacturer: str | None = None, limit: int = 10):
        return parts_service.list_parts(self._db, category=Category(category), max_price_php=max_price_php, manufacturer=manufacturer, limit=limit)

    def _tool_check_compatibility(self, cpu_id, motherboard_id, ram_id, psu_id, cooler_id, case_id, storage_ids=None, gpu_id=None):
        def load(pid, label):
            part = parts_service.get_part(self._db, uuid.UUID(pid))
            if part is None:
                raise ToolExecutionError(f"{label} not found: {pid}")
            return part

        build = BuildComponents(
            cpu=load(cpu_id, "CPU"), motherboard=load(motherboard_id, "Motherboard"),
            ram=load(ram_id, "RAM"), psu=load(psu_id, "PSU"), cooler=load(cooler_id, "Cooler"),
            case=load(case_id, "Case"),
            storage=[load(sid, "Storage") for sid in (storage_ids or [])],
            gpu=load(gpu_id, "GPU") if gpu_id else None,
        )
        results = CompatibilityChecker.run(build)
        return {
            "compatible": not CompatibilityChecker.has_blocking_issue(results),
            "results": [r.model_dump() for r in results],
        }

    def _tool_calculate_power(self, cpu_id: str, gpu_id: str | None = None):
        cpu = parts_service.get_part(self._db, uuid.UUID(cpu_id))
        if cpu is None:
            raise ToolExecutionError(f"CPU not found: {cpu_id}")
        gpu = parts_service.get_part(self._db, uuid.UUID(gpu_id)) if gpu_id else None
        return {"recommended_psu_watts": recommended_psu_watts(cpu, gpu)}

    def _tool_compare_parts(self, product_ids: list[str]):
        return [self._tool_get_part_details(pid) for pid in product_ids]

    def _tool_generate_build(self, **kwargs):
        request = BuildRequest(**kwargs)
        result = RecommendationEngine.recommend(self._db, request)
        if result is None:
            return {"error": "No compatible build found under these constraints. Try increasing the budget or relaxing a preference."}
        return result.model_dump()
