"""Unit tests for mapping combined dataset rows onto catalog rows, plus the
price conversion and dominance filter the larger catalog relies on."""

from decimal import Decimal

import pytest

from app.ingestion.import_datasets import ImportReport, collect, dataset_key, estimate_price_php, product_link
from app.ingestion.normalization import datasets as m
from app.ingestion.validation.specs import validate_spec
from app.models.product import Category
from app.recommendation.optimization.candidate_generation import _frontier

CPU_ROW = {
    "name": "AMD Ryzen 5 7600", "manufacturer": "AMD", "socket": "AM5", "cores": 6, "threads": 12,
    "base_clock_ghz": 3.8, "boost_clock_ghz": 5.1, "tdp_w": 65, "integrated_graphics": "Radeon",
    "microarchitecture": "Zen 4",
}
BOARD_ROW = {
    "name": "MSI MAG B650 TOMAHAWK WIFI", "manufacturer": "MSI", "socket": "AM5", "chipset": None,
    "form_factor": "ATX", "ram_type": "DDR5", "max_ram_gb": 192, "ram_slots": 4, "m2_slots": None,
}
GPU_ROW = {
    "full_name": "MSI Ventus 2X GeForce RTX 5060 Ti", "manufacturer": "MSI", "chip": "GeForce RTX 5060 Ti",
    "vram_gb": 16.0, "length_mm": 230, "tdp_w": 180,
}
CHIP_ROWS = [
    {"name": "GeForce RTX 5060 Ti 16 GB", "vram_gb": 16, "memory_type": "GDDR7", "bus_interface": "PCIe 5.0 x8"},
    {"name": "GeForce RTX 5060 Ti 8 GB", "vram_gb": 8, "memory_type": "GDDR7", "bus_interface": "PCIe 5.0 x8"},
]


def test_cpu_maps_with_generation_and_tier():
    mapped = m.map_cpu(CPU_ROW)
    assert mapped.model == "Ryzen 5 7600"
    assert mapped.spec["generation"] == "Ryzen 7000"
    assert mapped.spec["has_igpu"] is True
    assert mapped.performance_tier == "mid"
    assert validate_spec(Category.cpu, mapped.spec) == []


@pytest.mark.parametrize("model,arch,tier", [
    ("Ryzen 5 5600", "Zen 3", "entry"),
    ("Core i5-12400", "Alder Lake", "entry"),
    ("Ryzen 7 7800X3D", "Zen 4", "high"),
    ("Core i9-10900K", "Comet Lake", "mid"),
    ("Core Ultra 9 285K", "Arrow Lake", "enthusiast"),
    ("Athlon 3000G", "Zen", "entry"),
])
def test_cpu_tiers_match_seed_conventions(model, arch, tier):
    assert m.cpu_tier(model, arch) == tier


@pytest.mark.parametrize("model,generation", [
    ("Core i5-13400F", "Core 13th Gen"),
    ("Core i7-9700K", "Core 9th Gen"),
    ("Core Ultra 7 265K", "Core Ultra 200S"),
])
def test_intel_generation_from_model_number(model, generation):
    assert m.cpu_generation(model, "fallback") == generation


def test_old_socket_and_server_cpus_are_skipped():
    with pytest.raises(m.MappingError, match="socket"):
        m.map_cpu({**CPU_ROW, "socket": "LGA1151"})
    with pytest.raises(m.MappingError, match="server"):
        m.map_cpu({**CPU_ROW, "name": "AMD EPYC 4464P"})


def test_cpu_without_boost_uses_base_clock():
    mapped = m.map_cpu({**CPU_ROW, "boost_clock_ghz": 0.0})
    assert mapped.spec["boost_clock_ghz"] == 3.8


def test_board_derives_chipset_and_wifi_and_records_assumptions():
    mapped = m.map_motherboard(BOARD_ROW)
    assert mapped.spec["chipset"] == "B650"
    assert mapped.spec["wifi"] is True and mapped.spec["bluetooth"] is True
    assert mapped.spec["m2_slots"] == m.ASSUMED_M2_SLOTS
    assert mapped.assumed == ["m2_slots", "sata_ports"]
    assert mapped.performance_tier == "mid"
    assert validate_spec(Category.motherboard, mapped.spec) == []


def test_board_keeps_listed_m2_count():
    mapped = m.map_motherboard({**BOARD_ROW, "m2_slots": 3})
    assert mapped.spec["m2_slots"] == 3
    assert mapped.assumed == ["sata_ports"]


def test_gpu_takes_memory_and_bus_from_matching_chip_variant():
    mapped = m.map_gpu(GPU_ROW, m.load_chip_table(CHIP_ROWS))
    assert mapped.spec["vram_type"] == "GDDR7"
    assert mapped.spec["pcie_gen"] == "5.0"
    assert mapped.spec["chip_vendor"] == "NVIDIA"
    # Memory variants share a card name, so VRAM is appended to keep them apart.
    assert mapped.model == "Ventus 2X GeForce RTX 5060 Ti 16GB"
    assert validate_spec(Category.gpu, mapped.spec) == []


def test_gpu_on_unknown_chip_is_skipped():
    with pytest.raises(m.MappingError, match="gpu_chips.json"):
        m.map_gpu({**GPU_ROW, "chip": "GeForce GTX 1070"}, m.load_chip_table(CHIP_ROWS))


def test_server_and_laptop_memory_is_skipped():
    row = {"name": "Kingston KSM48R40BS4TMM-32HMR 32 GB", "manufacturer": "Kingston", "ddr_gen": "DDR5",
           "speed_mts": 4800, "modules": 1, "module_gb": 32, "capacity_gb": 32, "cas_latency": 40}
    with pytest.raises(m.MappingError, match="server or laptop"):
        m.map_ram(row)


def test_ram_model_names_layout_and_speed():
    row = {"name": "Corsair Vengeance 32 GB", "manufacturer": "Corsair", "ddr_gen": "DDR5",
           "speed_mts": 6000, "modules": 2, "module_gb": 16, "capacity_gb": 32, "cas_latency": 30}
    mapped = m.map_ram(row)
    assert mapped.model == "Vengeance 32GB (2x16) DDR5-6000 CL30"
    assert mapped.performance_tier == "high"


def test_m2_sata_drives_are_skipped():
    row = {"name": "Crucial MX500", "manufacturer": "Crucial", "type": "SSD", "capacity_gb": 1000,
           "interface": "M.2 SATA", "form_factor": "M.2-2280"}
    with pytest.raises(m.MappingError, match="not supported"):
        m.map_storage(row)


def test_case_in_reference_file_gets_verified_clearances():
    row = {"name": "Lian Li LANCOOL 216", "manufacturer": "Lian Li", "type": "ATX Mid Tower"}
    mapped = m.map_case(row)
    assert mapped.spec["max_gpu_length_mm"] == 392
    assert mapped.spec["max_cooler_height_mm"] == 180
    assert mapped.spec["radiator_support"]["front"] == 360
    assert mapped.unverified == []
    assert mapped.spec_source.startswith("https://lian-li.com/")
    assert validate_spec(Category.case, mapped.spec) == []


def test_reference_aliases_match_variants():
    row = {"name": "Corsair iCUE 4000D RGB AIRFLOW", "manufacturer": "Corsair", "type": "ATX Mid Tower"}
    assert m.map_case(row).spec["max_gpu_length_mm"] == 340


def test_case_row_with_its_own_clearances_uses_them():
    row = {"name": "be quiet! Pure Base 501 Black (BG073)", "manufacturer": "be quiet!", "type": "ATX",
           "supported_form_factors": "ATX, Micro-ATX, ITX", "max_gpu_length_mm": 368, "max_cooler_height_mm": 178}
    mapped = m.map_case(row)
    assert mapped.spec["form_factor_support"] == ["ATX", "Micro-ATX", "Mini-ITX"]
    assert mapped.spec["max_gpu_length_mm"] == 368
    assert mapped.unverified == ["radiator_support"]


def test_unlisted_tower_case_is_imported_unverified():
    row = {"name": "Acme Example Tower", "manufacturer": "Acme", "type": "ATX Mid Tower"}
    mapped = m.map_case(row)
    assert mapped.spec["form_factor_support"] == ["ATX", "Micro-ATX", "Mini-ITX"]
    assert mapped.spec["max_gpu_length_mm"] is None and mapped.spec["max_cooler_height_mm"] is None
    assert "max_gpu_length_mm" in mapped.unverified
    assert validate_spec(Category.case, mapped.spec) == []


@pytest.mark.parametrize("case_type", ["Mini ITX Desktop", "HTPC", "Mini ITX Tower"])
def test_unlisted_small_or_desktop_case_is_skipped(case_type):
    with pytest.raises(m.MappingError, match="PSU size unknown|not supported"):
        m.map_case({"name": "Acme Box", "manufacturer": "Acme", "type": case_type})


def test_cooler_in_reference_file_gets_sockets_and_height():
    row = {"name": "Thermalright Peerless Assassin 120 SE", "manufacturer": "Thermalright", "type": "air"}
    mapped = m.map_cooler(row)
    assert mapped.spec["socket_support"] == ["AM4", "AM5", "LGA1200", "LGA1700", "LGA1851"]
    assert mapped.spec["height_mm"] == 155
    assert mapped.unverified == []


def test_aio_takes_radiator_size_from_dataset_row():
    row = {"name": "ARCTIC Liquid Freezer III 360", "manufacturer": "ARCTIC", "type": "aio", "radiator_mm": 360}
    mapped = m.map_cooler(row)
    assert mapped.spec["radiator_mm"] == 360
    assert "AM5" in mapped.spec["socket_support"]
    sized = m.map_cooler({"name": "Thermalright Aqua Elite V3", "manufacturer": "Thermalright", "type": "aio", "radiator_mm": 240})
    assert sized.model == "Aqua Elite V3 240mm"


def test_unlisted_cooler_is_imported_unverified():
    mapped = m.map_cooler({"name": "Acme Frost 120", "manufacturer": "Acme", "type": "air"})
    assert mapped.spec["socket_support"] == []
    assert mapped.unverified == ["socket_support", "height_mm"]


def test_lga1700_mounting_implies_lga1851():
    assert m.cooler_sockets(["AM4", "LGA1700", "LGA1151"]) == ["AM4", "LGA1700", "LGA1851"]


@pytest.mark.parametrize("filename,key,category", [
    ("case_specs.json", "cases", Category.case),
    ("cooler_specs.json", "coolers", Category.cooler),
])
def test_reference_entries_are_sourced_and_plausible(filename, key, category):
    import json

    entries = json.loads((m.REFERENCE_DIR / filename).read_text())[key]
    assert entries
    for entry in entries:
        assert entry["source"].startswith("https://")
        assert entry["source_kind"] in ("manufacturer", "retailer")
        spec = {k: v for k, v in entry.items() if k not in ("manufacturer", "model", "aliases", "source", "source_kind")}
        if category is Category.case:
            assert set(spec["form_factor_support"]) <= {"E-ATX", "ATX", "Micro-ATX", "Mini-ITX"}
        else:
            assert spec["socket_support"] and (spec["type"] == "aio" or spec["height_mm"])
        assert validate_spec(category, spec) == [], entry["model"]


def test_eur_price_swaps_french_vat_for_philippine_vat():
    price, note = estimate_price_php({"price_eur": 120.0, "price_usd": 999.0}, eur_php=66.0, usd_php=57.0)
    assert price == Decimal("7390")  # 120 * 66 / 1.20 * 1.12 = 7392
    assert "EUR" in note


def test_listing_far_above_msrp_gets_no_price():
    price, _ = estimate_price_php({"price_usd": 704.86, "msrp_usd": 340}, eur_php=66.0, usd_php=57.0)
    assert price is None


def test_dataset_key_ignores_case_and_punctuation():
    a = m.MappedRow(Category.cpu, "AMD", "Ryzen 5 7600", "mid", {}, [])
    b = m.MappedRow(Category.cpu, "amd", "Ryzen-5 7600", "mid", {}, [])
    assert dataset_key(a) == dataset_key(b) == "cpu:amd:ryzen57600"


def test_frontier_drops_parts_beaten_on_price_tier_and_capacity():
    rows = [  # (id, price, tier, capacity, verified), sorted by price
        ("cheap-high-16", 3000.0, 3, 16, True),
        ("pricey-entry-16", 9000.0, 1, 16, True),   # dominated: cheaper, higher tier, same capacity
        ("pricier-high-64", 12000.0, 3, 64, True),  # kept: more capacity
        ("priciest-mid-32", 15000.0, 2, 32, True),  # dominated by the 64GB high kit
    ]
    assert [r[0] for r in _frontier(rows)] == ["cheap-high-16", "pricier-high-64"]


def test_frontier_keeps_verified_parts_beside_cheaper_unverified_ones():
    rows = [
        ("cheap-unverified", 2000.0, 2, 0, False),
        ("verified", 3000.0, 2, 0, True),
        ("pricier-unverified", 4000.0, 2, 0, False),  # dominated by the verified part
    ]
    assert [r[0] for r in _frontier(rows)] == ["cheap-unverified", "verified"]


def test_board_document_states_assumed_counts_as_minimums():
    from app.rag.chunking.document_builder import build_product_document

    mapped = m.map_motherboard(BOARD_ROW)
    product = {
        "category": "motherboard", "manufacturer": mapped.manufacturer, "model": mapped.model,
        "price_php": None, "source": "test", "performance_tier": mapped.performance_tier,
        "assumed_fields": mapped.assumed, **mapped.spec,
    }
    document = build_product_document(product)
    assert "at least 1 M.2 slot(s), and at least 2 SATA port(s)" in document


def test_cheapest_variant_keeps_a_siblings_product_link(tmp_path):
    import argparse
    import json

    rows = [
        {**CPU_ROW, "price_usd": 230, "pcpartpicker_url": "https://fr.pcpartpicker.com/product/abc/amd-ryzen-5-7600"},
        {**CPU_ROW, "price_usd": 200, "pcpartpicker_url": None},
    ]
    (tmp_path / "cpu.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
    args = argparse.Namespace(eur_php=66.0, usd_php=57.0, priced_only=False)

    (candidate,) = collect(Category.cpu, tmp_path, args, ImportReport(), {}).values()

    assert candidate.price_php == estimate_price_php(rows[1], 66.0, 57.0)[0]
    assert candidate.url == "https://pcpartpicker.com/product/abc/amd-ryzen-5-7600"


def test_regional_pcpartpicker_links_point_at_the_main_site():
    assert product_link("https://fr.pcpartpicker.com/product/ms4Zxr/x") == "https://pcpartpicker.com/product/ms4Zxr/x"
    assert product_link("https://pcpartpicker.com/product/ms4Zxr/x") == "https://pcpartpicker.com/product/ms4Zxr/x"
    assert product_link(None) is None
