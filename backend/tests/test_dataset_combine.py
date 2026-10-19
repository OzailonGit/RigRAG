"""Matching, normalization, and merge rules for the combined public datasets."""

import pytest

from app.ingestion.datasets.loaders import (
    _pcpp_ram,
    bus_interface,
    capacity_gb,
    chip_vendor_name,
    efficiency,
    gpu_segment,
    infer_ram_type,
    number_max,
    socket,
)
from app.ingestion.datasets.merge import attach_chip_specs, cpu_key, gpu_chip_key, merge_category, merge_group


@pytest.mark.parametrize("name", [
    "Intel® Core™ i5-13600K Processor",
    "Intel Core i5-13600K",
    "Core i5-13600K",
    "Intel Core i5 13600K 3.5 GHz 14-Core LGA1700 OEM/Tray",
])
def test_cpu_names_from_every_source_share_a_key(name):
    assert cpu_key(name) == "corei513600k"


def test_cpu_key_keeps_suffix_variants_apart():
    assert cpu_key("AMD Ryzen 7 7800X3D") != cpu_key("AMD Ryzen 7 7800X")
    assert cpu_key("Intel Core i5-13600K") != cpu_key("Intel Core i5-13600KF")
    assert cpu_key("Athlon™ 5370 APU with Radeon™ R3 Series") == cpu_key("AMD Athlon 5370")


@pytest.mark.parametrize("a,b", [
    ("GeForce RTX 3070 LHR", "NVIDIA GeForce RTX 3070"),
    ("GeForce GTX 1650 G6", "GeForce GTX 1650"),
    ("Radeon RX 550 - 512", "Radeon RX 550"),
    ("Radeon HD 7770 GHz Edition", "Radeon HD 7770"),
    ("GeForce RTX 4060 Ti 16 GB", "GeForce RTX 4060 Ti"),
])
def test_gpu_chip_revisions_share_a_key(a, b):
    assert gpu_chip_key(a) == gpu_chip_key(b)


def test_gpu_chip_key_keeps_tiers_apart():
    assert gpu_chip_key("RTX 4060 Ti") != gpu_chip_key("RTX 4060")
    assert gpu_chip_key("Radeon RX 7900 XTX") != gpu_chip_key("Radeon RX 7900 XT")


def test_majority_beats_priority_and_shared_upstreams_vote_once():
    rows = merge_group("cpu", [
        ("iblessi", {"name": "Intel Core Ultra 7 265K", "tdp_w": 250}),
        ("bottleneckpc", {"name": "Intel Core Ultra 7 265K", "tdp_w": 125}),
        ("pcpartpicker_hf", {"name": "Intel Core Ultra 7 265K", "tdp_w": 125}),
    ])
    assert rows["tdp_w"] == 125
    assert "250@iblessi" in rows["conflicts"]

    # The two PCPartPicker dumps are one vote, so this is 1 vs 1 and priority decides.
    tie = merge_group("cpu", [
        ("iblessi", {"name": "X", "tdp_w": 250}),
        ("pcpartpicker_hf", {"name": "X", "tdp_w": 125}),
        ("pcpartpicker_docyx", {"name": "X", "tdp_w": 125}),
    ])
    assert tie["tdp_w"] == 250


def test_specific_lga1151_revision_wins():
    row = merge_group("cpu", [
        ("intel_ark", {"name": "Intel Core i5-9600K", "socket": "LGA1151"}),
        ("bottleneckpc", {"name": "Intel Core i5-9600K", "socket": "LGA1151"}),
        ("passmark", {"name": "Intel Core i5-9600K", "socket": "LGA1151-2"}),
    ])
    assert row["socket"] == "LGA1151-2"


def test_less_specific_strings_are_not_conflicts():
    row = merge_group("gpu_chip", [
        ("wikipedia_gpus", {"name": "GeForce 8800 GT", "bus_interface": "PCIe x16"}),
        ("techpowerup_gpus", {"name": "GeForce 8800 GT", "bus_interface": "PCIe 2.0 x16"}),
    ])
    assert row["conflicts"] is None


def test_chip_without_vram_joins_its_only_sized_variant():
    rows = merge_category("gpu_chip", [
        ("techpowerup_gpus", {"name": "GeForce RTX 4070", "vram_gb": 12, "tdp_w": 200}),
        ("iblessi", {"name": "RTX 4070", "tdp_w": 200}),
    ])
    assert len(rows) == 1
    assert rows[0]["sources"] == "iblessi, techpowerup_gpus"


def test_cards_get_tdp_from_their_chip():
    chips = [{"name": "GeForce RTX 3060", "vram_gb": 12, "tdp_w": 170, "vendor": "NVIDIA"},
             {"name": "GeForce RTX 3060", "vram_gb": 8, "tdp_w": 170, "vendor": "NVIDIA"}]
    cards = [{"name": "MSI Ventus 2X", "chip": "GeForce RTX 3060 LHR", "vram_gb": 6, "tdp_w": None, "chip_vendor": None}]
    attach_chip_specs(cards, chips)
    assert cards[0]["tdp_w"] == 170
    assert cards[0]["derived_fields"] == "tdp_w"
    assert cards[0]["chip_vendor"] == "NVIDIA"


@pytest.mark.parametrize("raw,expected", [
    ("Gen5 x16", "PCIe 5.0 x16"),
    ("PCIe ×16 2.1", "PCIe 2.1 x16"),
    ("PCIe 4.0 x8", "PCIe 4.0 x8"),
    ("AGP 8×", "AGP 8x"),
])
def test_bus_interface_normalization(raw, expected):
    assert bus_interface(raw) == expected


@pytest.mark.parametrize("raw,expected", [("FCLGA1700", "LGA1700"), ("sTR4", "TR4"), ("Socket AM4", "AM4")])
def test_socket_normalization(raw, expected):
    assert socket(raw) == expected


def test_pcpartpicker_ram_formats_agree():
    assert _pcpp_ram("DDR5-6000", "2 x 16GB") == _pcpp_ram([5, 6000], [2, 16]) == {
        "ddr_gen": "DDR5", "speed_mts": 6000, "modules": 2, "module_gb": 16, "capacity_gb": 32,
    }


def test_value_helpers():
    assert capacity_gb("2 TB") == 2000 and capacity_gb("800 GB") == 800
    assert number_max("500 - 3050 RPM") == 3050 and number_max([17, 28.5]) == 28.5
    assert efficiency("gold") == efficiency("80+ Gold") == "80+ Gold"
    assert chip_vendor_name("ATI") == "AMD"


@pytest.mark.parametrize("name,segment", [
    ("GeForce RTX 4060", "desktop"),
    ("Arc A770", "desktop"),
    ("GeForce RTX 4060 Mobile", "mobile"),
    ("Mobility Radeon HD 5430", "mobile"),
    ("Radeon 780M", "mobile"),
    ("Quadro RTX 4000", "workstation"),
    ("Tesla V100", "datacenter"),
])
def test_gpu_segments(name, segment):
    assert gpu_segment(name) == segment


def test_board_ram_type_inference():
    assert infer_ram_type("AM5", "MSI MAG B650 TOMAHAWK WIFI") == "DDR5"
    assert infer_ram_type("LGA1700", "ASUS PRIME B760M-A D4") == "DDR4"
    assert infer_ram_type("LGA1700", "Gigabyte B760 AORUS ELITE AX") == "DDR5"
