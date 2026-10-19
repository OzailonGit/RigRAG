from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from app.models.price_observation import Retailer


class PriceSourceError(Exception):
    """A retailer API call failed (auth, rate limit, malformed response)."""


@dataclass
class Listing:
    retailer: Retailer
    listing_id: str
    title: str
    price_php: Decimal
    url: str | None = None
    shop_name: str | None = None
    is_official_store: bool = False


class PriceSource(Protocol):
    retailer: Retailer

    def search(self, query: str, limit: int = 20) -> list[Listing]:
        """Keyword search over the retailer's listings."""
        ...
