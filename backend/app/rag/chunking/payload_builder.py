import datetime
import decimal
import uuid


def _jsonable(value):
    if isinstance(value, (uuid.UUID,)):
        return str(value)
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, datetime.datetime):
        return value.isoformat()
    return value


def build_payload(product: dict) -> dict:
    """Flatten a product dict into Qdrant-safe metadata for payload filtering."""
    payload = {k: _jsonable(v) for k, v in product.items()}
    payload["currency"] = "PHP"
    payload["document_type"] = "specification"
    payload["last_updated"] = payload.pop("price_updated_at", None) or payload.pop("updated_at", None)
    return payload
