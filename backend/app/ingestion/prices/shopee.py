"""Shopee Affiliate Open API price source (GraphQL `productOfferV2`).

Requires an approved Shopee Affiliate account with Open API access; the
App ID and secret come from the affiliate dashboard. Each request is signed:

    Authorization: SHA256 Credential={app_id}, Timestamp={ts}, Signature={sig}
    sig = sha256(app_id + ts + request_body + secret)

The signature covers the exact body bytes, so the body is serialized once
and sent as-is.
"""

import hashlib
import json
import time
from decimal import Decimal, InvalidOperation

import httpx

from app.config import get_settings
from app.ingestion.prices.base import Listing, PriceSourceError
from app.models.price_observation import Retailer

# Arguments are inlined (JSON string escaping is valid GraphQL string
# escaping); this matches the request shape Shopee's examples use.
PRODUCT_OFFER_QUERY = (
    "{ productOfferV2(keyword: %s, page: 1, limit: %d) {"
    " nodes { itemId shopId productName priceMin priceMax shopName shopType productLink offerLink }"
    " pageInfo { hasNextPage } } }"
)

# shopType values that mark an official (Mall) store.
_OFFICIAL_SHOP_TYPES = {1}


def sign(app_id: str, secret: str, timestamp: int, body: str) -> str:
    return hashlib.sha256(f"{app_id}{timestamp}{body}{secret}".encode("utf-8")).hexdigest()


def authorization_header(app_id: str, secret: str, timestamp: int, body: str) -> str:
    signature = sign(app_id, secret, timestamp, body)
    return f"SHA256 Credential={app_id}, Timestamp={timestamp}, Signature={signature}"


def parse_offers(body: dict) -> list[Listing]:
    if body.get("errors"):
        raise PriceSourceError(f"Shopee API error: {body['errors'][0].get('message', body['errors'])}")
    try:
        nodes = body["data"]["productOfferV2"]["nodes"] or []
    except (KeyError, TypeError):
        raise PriceSourceError(f"Unexpected Shopee response shape: {str(body)[:200]}")

    listings = []
    for node in nodes:
        try:
            price = Decimal(str(node["priceMin"]))
        except (KeyError, InvalidOperation):
            continue
        shop_types = set(node.get("shopType") or [])
        listings.append(Listing(
            retailer=Retailer.shopee,
            listing_id=f"{node.get('shopId')}:{node.get('itemId')}",
            title=node.get("productName") or "",
            price_php=price,
            url=node.get("offerLink") or node.get("productLink"),
            shop_name=node.get("shopName"),
            is_official_store=bool(shop_types & _OFFICIAL_SHOP_TYPES),
        ))
    return listings


class ShopeeAffiliateSource:
    retailer = Retailer.shopee

    def __init__(self, app_id: str | None = None, secret: str | None = None, url: str | None = None, http: httpx.Client | None = None):
        settings = get_settings()
        self.app_id = app_id or settings.shopee_app_id
        self.secret = secret or settings.shopee_secret
        self.url = url or settings.shopee_api_url
        if not (self.app_id and self.secret):
            raise PriceSourceError("SHOPEE_APP_ID and SHOPEE_SECRET are not set.")
        self.http = http or httpx.Client(timeout=20)

    def search(self, query: str, limit: int = 20) -> list[Listing]:
        graphql = PRODUCT_OFFER_QUERY % (json.dumps(query), int(limit))
        body = json.dumps({"query": graphql}, separators=(",", ":"))
        timestamp = int(time.time())
        headers = {
            "Authorization": authorization_header(self.app_id, self.secret, timestamp, body),
            "Content-Type": "application/json",
        }
        try:
            response = self.http.post(self.url, content=body.encode("utf-8"), headers=headers)
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise PriceSourceError(f"Shopee request failed: {exc}") from exc
        return parse_offers(payload)
