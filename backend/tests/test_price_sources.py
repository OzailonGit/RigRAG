"""Shopee and Lazada request signing and response parsing (no network)."""

import hashlib
import hmac
import json
from decimal import Decimal

import httpx
import pytest

from app.ingestion.prices.base import PriceSourceError
from app.ingestion.prices.lazada import LazadaAffiliateSource, parse_products, sign as lazada_sign
from app.ingestion.prices.shopee import ShopeeAffiliateSource, authorization_header, parse_offers

SHOPEE_RESPONSE = {"data": {"productOfferV2": {"nodes": [
    {"itemId": 11, "shopId": 22, "productName": "ASUS Dual RTX 4060 OC 8GB", "priceMin": "18499.00",
     "priceMax": "18999.00", "shopName": "ASUS Official Store", "shopType": [1], "offerLink": "https://s.shopee.ph/a"},
    {"itemId": 12, "shopId": 23, "productName": "ASUS Dual RTX 4060", "priceMin": "17950", "shopType": [],
     "productLink": "https://shopee.ph/b"},
], "pageInfo": {"hasNextPage": False}}}}


def test_shopee_signature_covers_app_id_timestamp_body_and_secret():
    body = '{"query":"{}"}'
    expected = hashlib.sha256(f"app1700000000{body}secret".encode()).hexdigest()
    assert authorization_header("app", "secret", 1700000000, body) == (
        f"SHA256 Credential=app, Timestamp=1700000000, Signature={expected}"
    )


def test_shopee_parse_offers():
    listings = parse_offers(SHOPEE_RESPONSE)
    assert [l.price_php for l in listings] == [Decimal("18499.00"), Decimal("17950")]
    assert listings[0].is_official_store is True
    assert listings[0].listing_id == "22:11"
    assert listings[1].url == "https://shopee.ph/b"


def test_shopee_errors_raise():
    with pytest.raises(PriceSourceError, match="Invalid Signature"):
        parse_offers({"errors": [{"message": "Invalid Signature"}]})


def test_shopee_search_signs_the_exact_body_it_sends():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content.decode()
        captured["auth"] = request.headers["Authorization"]
        return httpx.Response(200, json=SHOPEE_RESPONSE)

    source = ShopeeAffiliateSource(app_id="app", secret="secret", url="https://example.test/graphql",
                                   http=httpx.Client(transport=httpx.MockTransport(handler)))
    assert len(source.search('ASUS "Dual" RTX 4060', limit=5)) == 2

    timestamp = captured["auth"].split("Timestamp=")[1].split(",")[0]
    assert captured["auth"] == authorization_header("app", "secret", int(timestamp), captured["body"])
    query = json.loads(captured["body"])["query"]
    assert 'keyword: "ASUS \\"Dual\\" RTX 4060"' in query
    assert "limit: 5" in query


def test_lazada_signature_scheme():
    params = {"timestamp": "1700000000000", "app_key": "123", "sign_method": "sha256", "keyword": "rtx 4060"}
    message = "/marketing/product/feed" + "app_key123keywordrtx 4060sign_methodsha256timestamp1700000000000"
    expected = hmac.new(b"secret", message.encode(), hashlib.sha256).hexdigest().upper()
    assert lazada_sign("/marketing/product/feed", params, "secret") == expected


def test_lazada_parser_finds_items_and_tolerates_field_spellings():
    body = {"code": "0", "data": {"result": {"products": [
        {"productId": 1, "productName": "ASUS Dual RTX 4060 OC", "discountPrice": "18,250.00", "isLazMall": True},
        {"item_id": 2, "name": "ASUS Dual RTX 4060", "price": 18900, "url": "https://lazada.com.ph/x"},
        {"productId": 3, "productName": "No price"},
    ]}}}
    listings = parse_products(body)
    assert [(l.listing_id, l.price_php) for l in listings] == [("1", Decimal("18250.00")), ("2", Decimal("18900"))]
    assert listings[0].is_official_store is True


def test_lazada_error_code_raises():
    with pytest.raises(PriceSourceError, match="IllegalAccessToken"):
        parse_products({"code": "IllegalAccessToken", "message": "The specified access token is invalid"})


def test_lazada_search_sends_signed_params():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json={"code": "0", "data": []})

    source = LazadaAffiliateSource(app_key="123", app_secret="secret", access_token="tok",
                                   base_url="https://example.test/rest", search_path="/marketing/product/feed",
                                   http=httpx.Client(transport=httpx.MockTransport(handler)))
    source.search("rtx 4060")

    params = captured["params"]
    signature = params.pop("sign")
    assert params["access_token"] == "tok"
    assert signature == lazada_sign("/marketing/product/feed", params, "secret")


def test_sources_require_credentials():
    with pytest.raises(PriceSourceError):
        ShopeeAffiliateSource(app_id="", secret="")
    with pytest.raises(PriceSourceError):
        LazadaAffiliateSource(app_key="", app_secret="")
