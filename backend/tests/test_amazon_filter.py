"""Tests the Amazon Reviews 2023 filter: which category paths count as PC
parts, and how products and reviews are cleaned."""

import json

from app.ingestion.amazon_reviews.filter_components import (
    catalog_category, clean_review, clean_text, filter_products, filter_reviews,
)

BASE = ["Electronics", "Computers & Accessories"]


def test_desktop_part_paths_map_to_catalog_categories():
    assert catalog_category(BASE + ["Computer Components", "Internal Components", "CPU Processors"]) == "cpu"
    assert catalog_category(BASE + ["Computer Components", "Internal Components", "Fans & Cooling",
                                    "Water Cooling Systems"]) == "cooler"
    assert catalog_category(BASE + ["Data Storage", "Internal Solid State Drives"]) == "storage"


def test_non_desktop_parts_are_left_out():
    assert catalog_category(BASE + ["Computer Components", "Laptop Replacement Parts"]) is None
    assert catalog_category(BASE + ["Computer Components", "Internal Components", "Fans & Cooling",
                                    "Case Fans"]) is None
    assert catalog_category(BASE + ["Data Storage", "USB Flash Drives"]) is None
    assert catalog_category(BASE + ["Computer Components", "Internal Components"]) is None
    assert catalog_category(["Electronics", "Television & Video"]) is None


def test_clean_text_undoes_html_and_collapses_spaces():
    assert clean_text("Runs  cool.<br /><br />Fan is &quot;quiet&quot;") == 'Runs cool.\nFan is "quiet"'
    assert clean_text(None) == ""


def test_reviews_lose_the_reviewer_id_and_images():
    review = clean_review({"rating": 5.0, "title": "Great", "text": "Fast card.", "images": [{"x": 1}],
                           "asin": "B01", "parent_asin": "B00", "user_id": "AFKZ", "timestamp": 1658185117948,
                           "helpful_vote": 2, "verified_purchase": True})
    assert "user_id" not in review and "images" not in review
    assert review["date"] == "2022-07-18"
    assert clean_review({"text": " ", "parent_asin": "B00", "timestamp": 0}) is None


def test_filter_keeps_parts_and_their_reviews_only(tmp_path):
    meta = tmp_path / "meta.jsonl"
    meta.write_text("\n".join(json.dumps(r) for r in [
        {"parent_asin": "GPU1", "title": "MSI RTX 3060", "categories": BASE + ["Computer Components",
         "Internal Components", "Graphics Cards"], "details": {"Best Sellers Rank": {"x": 1}, "Brand": "MSI"}},
        {"parent_asin": "TV1", "title": "A TV", "categories": ["Electronics", "Television & Video"]},
    ]) + "\n")
    reviews = tmp_path / "reviews.jsonl"
    review = {"parent_asin": "GPU1", "text": "Good", "timestamp": 1658185117948}
    reviews.write_text("\n".join(json.dumps(r) for r in [
        review, review, {"parent_asin": "TV1", "text": "Fine TV", "timestamp": 1658185117948},
    ]) + "\n")

    products, _ = filter_products(meta, tmp_path / "products.jsonl")
    counts = filter_reviews(reviews, products, tmp_path / "out_reviews.jsonl")

    assert products == {"GPU1": "gpu"}
    kept_product = json.loads((tmp_path / "products.jsonl").read_text())
    assert kept_product["details"] == {"Brand": "MSI"}
    assert len((tmp_path / "out_reviews.jsonl").read_text().splitlines()) == 1
    assert counts["dropped: duplicate review"] == 1


def test_titles_naming_accessories_or_other_systems_parts_are_dropped():
    from app.ingestion.amazon_reviews.filter_components import _NOT_A_PART

    dropped = {
        "psu": "uxcell ATX 24 Pin Male to 24 Pin Female Internal PC PSU Power Extension Cable",
        "ram": "8GB 2X 4GB Memory for Apple iMac 21.5\" DDR3 SODIMM RAM",
        "cooler": "LotFancy New CPU Cooling Cooler DC 5V fan for Toshiba Satellite L645",
        "case": "Circotech 20 Inch Sliding Rails for 2U to 4U Rack Mount Cases",
        "cpu": "Immaculate Waters Lavander Bath and Shower Liquid Soap",
        "gpu": "Phanteks Universal Vertical GPU Bracket with 220mm Riser Cable",
        "storage": "Seagate Exos X18 18TB Enterprise Hard Drive (Renewed)",
    }
    kept = {
        "psu": "Silverstone ATX12V/EPS12V 600W Silver 80+ Bronze PFC Power Supply ST60F-P",
        "ram": "Corsair Vengeance LPX 16GB (2x8GB) DDR4 3200 Desktop Memory",
        "cooler": "Thermalright Peerless Assassin 120 SE CPU Air Cooler",
        "case": "Lian Li O11 Dynamic Mid Tower Case",
        "cpu": "AMD Ryzen 7 5700G 8-Core, 16-Thread Unlocked Desktop Processor with Radeon Graphics",
        "storage": "SAMSUNG 870 EVO 2TB 2.5 Inch SATA III Internal SSD (MZ-77E2T0B/AM)",
    }
    # Real parts that earlier word lists dropped.
    kept_too = [
        ("gpu", "MSI GAMING GeForce GT 710 2GB GDRR5 64-bit HDCP Support DirectX 12 OpenGL 4.5 Low Profile"),
        ("psu", "EVGA SuperNOVA 1000 G6, 80 Plus Gold 1000W, Fully Modular, Includes Power ON Self Tester"),
        ("psu", "Corsair CV Series, CV450, 450 Watt, 80+ Bronze Certified, Fixed Cable Power Supply"),
        ("case", "NZXT H510 - Compact ATX Mid-Tower PC Gaming Case - Tempered Glass - Cable Management"),
        ("ram", "Timetec 8GB DDR3 1600MHz PC3-12800 Non-ECC Unbuffered 1.35V 240 Pin UDIMM Desktop Memory"),
        ("storage", "SAMSUNG 870 QVO SATA III SSD 2TB 2.5\" Internal Solid State Drive, Upgrade Desktop PC or Laptop"),
        ("cpu", "Intel Chip 3.4 4 BX80646I74770K"),
        ("cpu", "CORE I5-4590 3.30GHZ"),
        ("gpu", "EVGA GeForce RTX 3060 XC Gaming, 12G-P5-3657-KR, 12GB GDDR6, Dual-Fan, Metal Backplate"),
        ("psu", "Corsair RM750x 80 Plus Gold Fully Modular ATX Power Supply, CP-9020179-EU"),
        ("psu", "Corsair RM750e Fully Modular Low-Noise ATX Power Supply - Dual EPS12V Connectors"),
        ("gpu", "VisionTek Radeon 7750 2GB GDDR5 SFF Graphics Card, 4k 2 DisplayPort 1.2, Low Profile Bracket"),
        ("case", "GIM ATX Mid-Tower Case Black Gaming PC Case 2 Tempered Glass Panels, Dust Filter"),
    ]
    for category, title in dropped.items():
        assert _NOT_A_PART[category].search(title), title
    for category, title in [*kept.items(), *kept_too]:
        assert not _NOT_A_PART[category].search(title), title


def test_power_supply_accessories_without_a_wattage_are_dropped():
    from app.ingestion.amazon_reviews.filter_components import _NOT_A_PART

    for title in ("CanaKit 3.5A Raspberry Pi 4 Power Supply (USB-C)",
                  "Computer PC Power Supply Tester, ATX/ITX/IDE/HDD/SATA Connectors, 1.8'' LCD",
                  "KOTTO Braided ATX Sleeved Cable Extension Kit for Power Supply"):
        assert _NOT_A_PART["psu"].search(title), title
