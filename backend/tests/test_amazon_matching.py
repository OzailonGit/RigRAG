"""Tests matching Amazon listings to catalog parts. Every case is a real
mismatch (or a real match) found checking the matcher by hand."""

import pytest

from app.ingestion.amazon_reviews.review_notes import ReviewStats, match_strength, note_text


def _part(category: str, manufacturer: str, model: str, **specs) -> dict:
    return {"category": category, "manufacturer": manufacturer, "model": model, **specs}


@pytest.mark.parametrize("part, title", [
    (_part("gpu", "EVGA", "XC BLACK GAMING GeForce RTX 3060 12GB", chip="GeForce RTX 3060"),
     "EVGA GeForce RTX 3060 XC Black Gaming, 12G-P5-3655-KR, 12GB GDDR6"),
    (_part("cpu", "Intel", "Core i7-11700KF"), "Intel Core i7-11700KF Desktop Processor 8 Cores up to 5.0 GHz"),
    (_part("ram", "G.Skill", "Ripjaws S5 64GB (2x32) DDR5-6000 CL32", capacity_gb=64),
     "G.Skill RipJaws S5 Series (Intel XMP) 64GB (2 x 32GB) 288-Pin SDRAM DDR5 6000 CL32-38-38-96"),
    (_part("cooler", "Thermalright", "AXP90-X36"), "Thermalright AXP90 X36 Low Profile ITX CPU Cooler, 36mm Height"),
    (_part("storage", "Samsung", "850 Evo 500GB", capacity_gb=500),
     "SAMSUNG 850 EVO 500GB 2.5-Inch SATA III Internal SSD (MZ-75E500B/AM)"),
])
def test_real_matches_are_accepted(part, title):
    assert match_strength(part, title) > 0


@pytest.mark.parametrize("part, title", [
    (_part("motherboard", "Gigabyte", "Q570M D3H"), "Gigabyte X570 Gaming X (AMD Ryzen 3000/X570/ATX/PCIe4.0/DDR4)"),
    (_part("motherboard", "Asus", "ROG STRIX X370-I GAMING"),
     "ASUS ROG Maximus X Hero LGA1151 (Intel 8th Gen) DDR4 DP HDMI M.2 Z370 ATX Gaming Motherboard"),
    (_part("motherboard", "Gigabyte", "Z690 UD DDR4 V2"),
     "Gigabyte Z690 UD ATX Motherboard - Supports 12th Gen Intel Core Processors (LGA 1700), DDR5-6000, PCIe 4.0, USB 3.2"),
    (_part("motherboard", "Asus", "PRIME B550M-A WIFI II"),
     "AMD Ryzen 7 5700G 8-Core, 16-Thread Unlocked Desktop Processor with Radeon Graphics ASUS Prime B550M-A WiFi II"),
    (_part("cooler", "Antec", "A400i"), "Antec CPU Cooler, A400 RGB, 4-Pin Connector PWM Silent Fan"),
    (_part("storage", "Seagate", "BarraCuda Q5 500GB", capacity_gb=500),
     "Internal HDD Seagate BarraCuda 3.5'' 500GB SATA3 7200RPM 16MB"),
    (_part("psu", "Solid Gear", "SDGR-650E 650W"), "Solid Gear SDGR-650BR 650W - 80 PLUS Bronze ATX 12V Power Supply"),
    (_part("ram", "V-Color", "SKYWALKER PRISM RGB 16GB (2x8) DDR4-3200", capacity_gb=16),
     "V-Color Skywalker II 16GB (2 x 8GB) DDR4 3200MHz (PC4-25600) CL16 1.35V Desktop Memory"),
    (_part("cpu", "AMD", "Ryzen 5 2600"), "AMD Ryzen 5 2600X Processor with Wraith Spire Cooler"),
    (_part("cooler", "Noctua", "NH-U12A chromax.black"), "Noctua NA-HC8 chromax.Black, Heatsink Cover for NH-U12A (Black)"),
    (_part("case", "Cooler Master", "Storm Scout"), "Cooler Master Storm Scout II Advance, SGC-2100-KWN3"),
    (_part("gpu", "EVGA", "FTW3 ULTRA GAMING LHR GeForce RTX 3080 10GB", chip="GeForce RTX 3080"),
     "EVGA GeForce RTX 3080 FTW3 Ultra Hybrid Gaming, 10G-P5-3898-KL, 10GB GDDR6X, ARGB LED"),
    (_part("case", "Fractal Design", "Pop Mini Silent"), "Fractal Design Core 500 - Mini Tower Computer Case - Mini ITX - Silent"),
])
def test_near_misses_are_rejected(part, title):
    assert match_strength(part, title) == 0


def test_note_text_states_counts_and_says_they_arent_measurements():
    stats = ReviewStats(reviews=200, rating_total=880.0, low_ratings=16, verified=180, first="2019-01-02",
                        last="2023-08-01")
    stats.themes.update({"mention coil whine": 12, "call it loud or noisy": 2})
    text = note_text("EVGA RTX 3060 XC", stats)
    assert "200 reviews, averaging 4.4 of 5 stars" in text
    assert "8.0% rating it 1 or 2 stars" in text
    assert "Of these reviews, 6.0% (12) mention coil whine." in text
    assert "loud" not in text  # 2 reviews: too few to report
    assert "keyword counts in owner reviews, not measurements" in text


def test_noise_praise_and_complaints_are_counted_apart():
    from app.ingestion.amazon_reviews.review_notes import themes_for

    themes = themes_for({"category": "cooler", "type": "air"})
    assert "mention leaks or pump failure" not in themes  # an air cooler can't leak
    praise = "Super quiet, I can't hear any noise at all."
    assert themes["call it quiet or silent"].search(praise)
    assert not themes["call it loud or noisy"].search(praise)
    assert themes["call it loud or noisy"].search("The fan is way too loud under load.")
    assert "mention leaks or pump failure" in themes_for({"category": "cooler", "type": "aio"})
