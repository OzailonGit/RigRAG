"""Retailer listing -> catalog product matching and price aggregation."""

from decimal import Decimal

import pytest

from app.ingestion.prices.base import Listing
from app.ingestion.prices.matching import aggregate_price, drop_outliers, match_score, representative_listing
from app.models.price_observation import Retailer

ASUS_4060 = {
    "category": "gpu", "manufacturer": "ASUS", "model": "DUAL-RTX4060-O8G",
    "mpn": "90YV0JC0-M0NA00", "chip": "GeForce RTX 4060",
}
SAMSUNG_990 = {"category": "storage", "manufacturer": "Samsung", "model": "990 PRO 1TB", "mpn": "MZ-V9P1T0BW", "capacity_gb": 1000}
ASUS_TUF_750 = {"category": "psu", "manufacturer": "ASUS", "model": "TUF Gaming 750W Gold", "mpn": "90YE00S3-B0NA00"}


def listing(price: str, official: bool = False, title: str = "x") -> Listing:
    return Listing(retailer=Retailer.shopee, listing_id=price, title=title, price_php=Decimal(price), is_official_store=official)


@pytest.mark.parametrize("title", [
    "ASUS Dual GeForce RTX 4060 OC Edition 8GB GDDR6 Graphics Card",
    "ASUS DUAL RTX4060 O8G 8GB GDDR6",
    "[Official] ASUS 90YV0JC0-M0NA00 Dual RTX 4060 OC",
])
def test_matching_titles_are_accepted(title):
    assert match_score(ASUS_4060, title) > 0


@pytest.mark.parametrize("title,why", [
    ("ASUS Dual GeForce RTX 4060 Ti 8GB GDDR6", "different chip variant"),
    ("MSI Ventus 2X GeForce RTX 4060 8GB", "different brand"),
    ("ASUS Dual RTX 4070 12GB", "different model number"),
    ("USED ASUS Dual RTX 4060 OC 8GB", "used item"),
    ("ASUS Dual RTX 4060 + Ryzen 5 7600 PC Build bundle", "bundle"),
])
def test_wrong_listings_are_rejected(title, why):
    assert match_score(ASUS_4060, title) == 0, why


def test_gpu_variant_must_be_present_when_expected():
    ti = {**ASUS_4060, "model": "DUAL-RTX4060TI-O8G", "mpn": None, "chip": "GeForce RTX 4060 Ti"}
    assert match_score(ti, "ASUS Dual RTX 4060 Ti OC 8GB") > 0
    assert match_score(ti, "ASUS Dual RTX 4060 OC 8GB") == 0


def test_storage_capacity_must_agree():
    assert match_score(SAMSUNG_990, "Samsung 990 PRO 1TB NVMe M.2 SSD") > 0
    assert match_score(SAMSUNG_990, "Samsung 990 PRO 2TB NVMe M.2 SSD") == 0


def test_psu_titles_mentioning_cables_are_not_rejected():
    assert match_score(ASUS_TUF_750, "ASUS TUF Gaming 750W Gold Fully Modular PSU with sleeved cables") > 0


def test_part_number_match_scores_full_marks():
    assert match_score(SAMSUNG_990, "MZ-V9P1T0BW Samsung 1TB") == 1.0


def test_outliers_are_dropped():
    kept = drop_outliers([listing("18000"), listing("18500"), listing("19000"), listing("2500"), listing("60000")])
    assert sorted(str(l.price_php) for l in kept) == ["18000", "18500", "19000"]


def test_outlier_filter_needs_three_listings():
    pair = [listing("18000"), listing("2500")]
    assert drop_outliers(pair) == pair


def test_aggregate_prefers_official_stores():
    prices = [listing("17000"), listing("17500"), listing("19000", official=True), listing("19400", official=True)]
    assert aggregate_price(prices) == Decimal("19200.00")
    assert aggregate_price([listing("17000"), listing("18000"), listing("20000")]) == Decimal("18000.00")
    assert aggregate_price([]) is None


def test_link_goes_to_the_listing_nearest_the_quoted_price():
    listings = [
        Listing(retailer=Retailer.shopee, listing_id=price, title="x", price_php=Decimal(price), url=f"https://shopee.ph/{price}")
        for price in ("9000", "10000", "15000")
    ]
    assert representative_listing(listings, aggregate_price(listings)).url == "https://shopee.ph/10000"


def test_link_prefers_official_stores_and_skips_listings_without_a_url():
    listings = [
        Listing(retailer=Retailer.shopee, listing_id="a", title="x", price_php=Decimal("10000"), url="https://shopee.ph/a"),
        Listing(retailer=Retailer.lazada, listing_id="b", title="x", price_php=Decimal("12000"),
                is_official_store=True, url="https://lazada.com.ph/b"),
        Listing(retailer=Retailer.lazada, listing_id="c", title="x", price_php=Decimal("12000"), is_official_store=True),
    ]
    assert representative_listing(listings, Decimal("12000")).url == "https://lazada.com.ph/b"
    assert representative_listing(listings[2:], Decimal("12000")) is None
