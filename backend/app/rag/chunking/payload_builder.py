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
    # Lowercase brand names to filter on, since the catalog spells them
    # inconsistently ("ASUS", "Asus"). A GPU also matches its chip vendor, so
    # "NVIDIA" finds partner cards, as parts_service.list_parts does.
    brands = {str(product.get("manufacturer") or "").lower(), str(product.get("chip_vendor") or "").lower()}
    payload["brand_keys"] = sorted(b for b in brands if b)
    return payload
