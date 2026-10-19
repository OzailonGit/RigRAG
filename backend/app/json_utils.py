import datetime
import decimal
import enum
import uuid
from typing import Any


def to_jsonable(value: Any) -> Any:
    """Recursively converts a value tree into plain JSON-safe types.

    UUIDs, Decimals, datetimes, and str enums must be converted before
    json.dumps or an SDK serializer will accept them. Used for LLM tool
    results (app/llm/tools.py); payload_builder.py has a narrower
    single-level version for Qdrant payloads.
    """
    if isinstance(value, dict):
        return {k: to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(v) for v in value]
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()
    if isinstance(value, enum.Enum):
        return value.value
    return value
