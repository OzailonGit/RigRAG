"""Icecat datasheet -> spec row mapping, using trimmed real Open Icecat
datasheets from tests/fixtures/icecat."""

import copy
import json
from pathlib import Path

import pytest

from app.ingestion.normalization.icecat import MappingError, map_icecat_product, normalize_socket, normalize_sockets
from app.ingestion.normalization.overrides import apply_overrides
from app.ingestion.validation.specs import validate_spec
from app.models.product import Category

FIXTURES = Path(__file__).parent / "fixtures" / "icecat"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def set_feature(data: dict, feature_id: int, value: str) -> dict:
    data = copy.deepcopy(data)
    for group in data["FeaturesGroups"]:
        for feature in group["Features"]:
            if int(feature["Feature"]["ID"]) == feature_id:
                feature["Value"] = value
    return data


@pytest.mark.parametrize("fixture", sorted(p.name for p in FIXTURES.glob("*.json")))
def test_every_fixture_maps_to_a_valid_spec(fixture):
    mapped = map_icecat_product(load(fixture))
    apply_overrides(mapped.product["mpn"], mapped.spec)
    assert validate_spec(mapped.category, mapped.spec) == []
    assert mapped.product["source"].startswith("Specs Icecat")


def test_gpu_maps_board_partner_card_onto_chip_reference():
    mapped = map_icecat_product(load("gpu_gigabyte_eagle_rtx4060.json"))
    assert mapped.category is Category.gpu
    assert mapped.product["manufacturer"] == "Gigabyte"
    assert mapped.spec["chip"] == "GeForce RTX 4060"
    assert mapped.spec["chip_vendor"] == "NVIDIA"
    assert mapped.spec["tdp_w"] == 115
    assert mapped.spec["length_mm"] == 272
    assert mapped.spec["pcie_gen"] == "4.0"
    assert mapped.product["performance_tier"] == "mid"


def test_manufacturer_override_corrects_icecat_gpu_length():
    mapped = map_icecat_product(load("gpu_asus_dual_rtx4060.json"))
    assert mapped.spec["length_mm"] == 200
    assert apply_overrides(mapped.product["mpn"], mapped.spec) == ["length_mm"]
    assert mapped.spec["length_mm"] == 228


def test_unknown_gpu_chip_is_rejected_rather_than_guessed():
    data = set_feature(load("gpu_asus_dual_rtx4060.json"), 1654, "GeForce RTX 9999")
    with pytest.raises(MappingError, match="unknown GPU chip"):
        map_icecat_product(data)


def test_motherboard_fields():
    spec = map_icecat_product(load("motherboard_asus_b650a.json")).spec
    assert spec["socket"] == "AM5"
    assert spec["chipset"] == "B650"
    assert spec["form_factor"] == "ATX"
    assert spec["ram_type"] == "DDR5"
    assert spec["m2_slots"] == 3
    assert spec["wifi"] is True
    assert spec["pcie_slots"] == {"x1": 2, "x16": 2}


def test_unbuffered_ram_is_accepted_and_sticks_parsed():
    spec = map_icecat_product(load("ram_kingston_fury_beast.json")).spec
    assert spec == {"capacity_gb": 32, "sticks": 2, "ddr_gen": "DDR5", "speed_mhz": 5200, "cas_latency": 40, "voltage": 1.25}


def test_laptop_and_server_ram_are_rejected():
    ram = load("ram_kingston_fury_beast.json")
    with pytest.raises(MappingError, match="SO-DIMM"):
        map_icecat_product(set_feature(ram, 7696, "262-pin SO-DIMM"))
    with pytest.raises(MappingError, match="registered"):
        map_icecat_product(set_feature(ram, 38209, "Registered (buffered)"))


def test_storage_names_include_capacity_and_hdds_have_no_speed():
    ssd = map_icecat_product(load("ssd_samsung_990pro.json"))
    assert ssd.product["model"] == "990 PRO 1TB"
    assert ssd.spec["type"] == "SSD_NVME"
    assert ssd.spec["read_mb_s"] == 7450

    hdd = map_icecat_product(load("hdd_seagate_barracuda.json"))
    assert hdd.product["model"] == "Barracuda ST2000DM008 2TB"
    assert hdd.spec["type"] == "HDD"
    assert hdd.spec["read_mb_s"] is None


def test_psu_efficiency_and_connectors():
    spec = map_icecat_product(load("psu_asus_tuf750.json")).spec
    assert spec["wattage"] == 750
    assert spec["efficiency_rating"] == "80+ Gold"
    assert spec["atx_version"] == "ATX 3.1"
    assert spec["pcie_connectors"] == {"pcie_8pin": 3}
    assert spec["supports_12vhpwr"] is True


def test_cooler_sockets_and_non_cpu_coolers_rejected():
    cooler = load("cooler_bequiet_pure_rock_slim2.json")
    spec = map_icecat_product(cooler).spec
    assert spec["type"] == "air"
    assert {"AM4", "AM5", "LGA1700"} <= set(spec["socket_support"])
    assert spec["height_mm"] == 135

    with pytest.raises(MappingError, match="not a CPU cooler"):
        map_icecat_product(set_feature(cooler, 8173, "Computer case"))


def test_case_clearances_and_radiators():
    spec = map_icecat_product(load("case_asus_ap201.json")).spec
    assert spec["form_factor_support"] == ["Micro-ATX", "Mini-ITX"]
    assert spec["max_gpu_length_mm"] == 338
    assert spec["max_cooler_height_mm"] == 170
    assert spec["radiator_support"] == {"rear": 120, "top": 360}


def test_unsupported_category_is_rejected():
    data = copy.deepcopy(load("case_asus_ap201.json"))
    data["GeneralInfo"]["Category"]["CategoryID"] = "151"
    with pytest.raises(MappingError, match="not a supported part category"):
        map_icecat_product(data)


@pytest.mark.parametrize("raw,expected", [
    ("Socket AM5", "AM5"),
    ("LGA 1700 (Socket V)", "LGA1700"),
    ("LGA 1851", "LGA1851"),
])
def test_normalize_socket(raw, expected):
    assert normalize_socket(raw) == expected


def test_normalize_sockets_ignores_commas_inside_parentheses():
    assert normalize_sockets("LGA 1200 (Socket H5), Socket AM4") == ["AM4", "LGA1200"]


def test_validation_reports_missing_and_implausible_values():
    spec = map_icecat_product(load("gpu_gigabyte_eagle_rtx4060.json")).spec
    spec["length_mm"] = None
    spec["tdp_w"] = 5000
    problems = validate_spec(Category.gpu, spec)
    assert "missing length_mm" in problems
    assert any(p.startswith("tdp_w=5000 outside") for p in problems)


def test_validation_rejects_values_outside_enum():
    spec = map_icecat_product(load("gpu_gigabyte_eagle_rtx4060.json")).spec
    spec["vram_type"] = "GDDR5"
    assert any("vram_type" in p for p in validate_spec(Category.gpu, spec))
