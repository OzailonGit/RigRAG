import copy

import pytest

from app.rag.knowledge import retrieval


@pytest.fixture(autouse=True)
def note_retriever_without_vectors(monkeypatch):
    """Note retrieval in tests ranks by shared words, so no test needs
    Qdrant or the embedding model."""
    monkeypatch.setattr(retrieval, "_default", retrieval.NoteRetriever(use_vectors=False))

AM5_CPU = {
    "manufacturer": "AMD", "model": "Ryzen 5 7600", "socket": "AM5",
    "tdp_w": 65, "bios_flash_required": False,
}
AM4_CPU = {
    "manufacturer": "AMD", "model": "Ryzen 5 5600", "socket": "AM4",
    "tdp_w": 65, "bios_flash_required": False,
}
LGA1700_CPU = {
    "manufacturer": "Intel", "model": "Core i5-13400F", "socket": "LGA1700",
    "tdp_w": 65, "bios_flash_required": False,
}

AM5_DDR5_MOBO = {
    "manufacturer": "ASUS", "model": "Prime B650M-A", "socket": "AM5",
    "form_factor": "Micro-ATX", "ram_type": "DDR5", "max_ram_gb": 128,
    "ram_slots": 4, "m2_slots": 2, "sata_ports": 4, "bios_flash_required": False,
}
LGA1700_MOBO = {
    "manufacturer": "MSI", "model": "PRO B760M-A WiFi", "socket": "LGA1700",
    "form_factor": "Micro-ATX", "ram_type": "DDR5", "max_ram_gb": 128,
    "ram_slots": 4, "m2_slots": 2, "sata_ports": 4, "bios_flash_required": False,
}

DDR5_RAM = {
    "manufacturer": "Kingston", "model": "Fury Beast 16GB DDR5-5600",
    "capacity_gb": 16, "sticks": 2, "ddr_gen": "DDR5", "speed_mhz": 5600,
}
DDR4_RAM = {
    "manufacturer": "Kingston", "model": "Fury Beast 16GB DDR4-3200",
    "capacity_gb": 16, "sticks": 2, "ddr_gen": "DDR4", "speed_mhz": 3200,
}

GPU = {
    "manufacturer": "NVIDIA", "model": "RTX 4060", "vram_gb": 8, "tdp_w": 115,
    "recommended_psu_w": 550, "length_mm": 200,
}

PSU_550 = {
    "manufacturer": "Corsair", "model": "CV550", "wattage": 550,
    "pcie_connectors": {"pcie_8pin": 2}, "supports_12vhpwr": False,
}
PSU_250 = {
    "manufacturer": "Generic", "model": "OldPSU 250W", "wattage": 250,
    "pcie_connectors": {"pcie_8pin": 1}, "supports_12vhpwr": False,
}

COOLER_UNIVERSAL_AIR = {
    "manufacturer": "Cooler Master", "model": "Hyper 212", "type": "air",
    "socket_support": ["AM4", "AM5", "LGA1700", "LGA1200"],
    "height_mm": 159, "radiator_mm": None, "tdp_rating_w": 150,
}

CASE_ATX = {
    "manufacturer": "NZXT", "model": "H510",
    "form_factor_support": ["ATX", "Micro-ATX", "Mini-ITX"],
    "max_gpu_length_mm": 381, "max_cooler_height_mm": 165,
    "radiator_support": {"front": 240, "top": 0},
}

STORAGE_NVME = [{"manufacturer": "Kingston", "model": "NV2 500GB", "type": "SSD_NVME"}]


@pytest.fixture
def base_build_kwargs():
    return dict(
        cpu=copy.deepcopy(AM5_CPU),
        motherboard=copy.deepcopy(AM5_DDR5_MOBO),
        ram=copy.deepcopy(DDR5_RAM),
        storage=copy.deepcopy(STORAGE_NVME),
        psu=copy.deepcopy(PSU_550),
        cooler=copy.deepcopy(COOLER_UNIVERSAL_AIR),
        case=copy.deepcopy(CASE_ATX),
        gpu=copy.deepcopy(GPU),
    )
