"""Lazada Open Platform price source (affiliate product search).

Request signing follows the Lazada Open Platform scheme used by every
endpoint: sort all parameters by key, concatenate key+value pairs, prefix
the API path, HMAC-SHA256 with the app secret, upper-case hex.

NOTE: Lazada's affiliate endpoint documentation is only visible to approved
affiliate apps. The search path (LAZADA_PRODUCT_SEARCH_PATH), its parameter
names, and the response field names below are unconfirmed and must be checked
against the docs at https://open.lazada.com once the app is approved. The
response parser accepts several common field spellings so only
`SEARCH_PARAM_NAMES` and `_FIELD_CANDIDATES` should need adjusting.
"""

import hashlib
import hmac
import time
from decimal import Decimal, InvalidOperation

import httpx

from app.config import get_settings
from app.ingestion.prices.base import Listing, PriceSourceError
from app.models.price_observation import Retailer

SEARCH_PARAM_NAMES = {"keyword": "keyword", "page": "page", "limit": "limit"}

_FIELD_CANDIDATES = {
    "id": ("productId", "product_id", "itemId", "item_id", "skuId"),
    "title": ("productName", "product_name", "name", "title"),
    "price": ("discountPrice", "discount_price", "salePrice", "sale_price", "price"),
    "url": ("productUrl", "product_url", "trackingLink", "url", "link"),
    "shop": ("sellerName", "seller_name", "shopName", "shop_name", "brandName"),
}
_OFFICIAL_FLAGS = ("isLazMall", "lazMall", "is_official", "officialStore")


def sign(api_path: str, params: dict[str, str], secret: str) -> str:
    message = api_path + "".join(f"{key}{params[key]}" for key in sorted(params))
    return hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest().upper()


def _first(item: dict, field: str):
    for key in _FIELD_CANDIDATES[field]:
        if item.get(key) not in (None, ""):
            return item[key]
    return None


def _find_items(data) -> list[dict]:
    """Returns the first list of dicts found in the response payload."""
    if isinstance(data, list):
        return [d for d in data if isinstance(d, dict)]
    if isinstance(data, dict):
        for value in data.values():
            items = _find_items(value)
            if items:
                return items
    return []


def parse_products(body: dict) -> list[Listing]:
    if str(body.get("code", "0")) != "0":
        raise PriceSourceError(f"Lazada API error {body.get('code')}: {body.get('message', '')}")

    listings = []
    for item in _find_items(body.get("data") or body.get("result") or {}):
        title, raw_price, item_id = _first(item, "title"), _first(item, "price"), _first(item, "id")
        if not (title and raw_price is not None and item_id is not None):
            continue
        try:
            price = Decimal(str(raw_price).replace(",", ""))
        except InvalidOperation:
            continue
        listings.append(Listing(
            retailer=Retailer.lazada,
            listing_id=str(item_id),
            title=str(title),
            price_php=price,
            url=_first(item, "url"),
            shop_name=_first(item, "shop"),
            is_official_store=any(bool(item.get(flag)) for flag in _OFFICIAL_FLAGS),
        ))
    return listings


class LazadaAffiliateSource:
    retailer = Retailer.lazada

    def __init__(
        self,
        app_key: str | None = None,
        app_secret: str | None = None,
        access_token: str | None = None,
        base_url: str | None = None,
        search_path: str | None = None,
        http: httpx.Client | None = None,
    ):
        settings = get_settings()
        self.app_key = app_key or settings.lazada_app_key
        self.app_secret = app_secret or settings.lazada_app_secret
        self.access_token = access_token if access_token is not None else settings.lazada_access_token
        self.base_url = (base_url or settings.lazada_api_url).rstrip("/")
        self.search_path = search_path or settings.lazada_product_search_path
        if not (self.app_key and self.app_secret):
            raise PriceSourceError("LAZADA_APP_KEY and LAZADA_APP_SECRET are not set.")
        self.http = http or httpx.Client(timeout=20)

    def _signed_params(self, params: dict[str, str]) -> dict[str, str]:
        signed = {
            **params,
            "app_key": self.app_key,
            "timestamp": str(int(time.time() * 1000)),
            "sign_method": "sha256",
        }
        if self.access_token:
            signed["access_token"] = self.access_token
        signed["sign"] = sign(self.search_path, signed, self.app_secret)
        return signed

    def search(self, query: str, limit: int = 20) -> list[Listing]:
        params = {
            SEARCH_PARAM_NAMES["keyword"]: query,
            SEARCH_PARAM_NAMES["page"]: "1",
            SEARCH_PARAM_NAMES["limit"]: str(limit),
        }
        try:
            response = self.http.get(self.base_url + self.search_path, params=self._signed_params(params))
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise PriceSourceError(f"Lazada request failed: {exc}") from exc
        return parse_products(body)
