import copy

from app.recommendation.compatibility import rules
from app.recommendation.compatibility.build_components import BuildComponents
from app.recommendation.compatibility.checker import CompatibilityChecker
from tests.conftest import (
    AM4_CPU,
    AM5_CPU,
    AM5_DDR5_MOBO,
    DDR4_RAM,
    DDR5_RAM,
    LGA1700_CPU,
    LGA1700_MOBO,
    PSU_250,
)


# --- Core socket and RAM type checks ---

def test_am5_cpu_with_am5_motherboard_is_compatible():
    result = rules.check_cpu_motherboard(AM5_CPU, AM5_DDR5_MOBO)
    assert result.compatible is True


def test_am5_cpu_with_lga1700_motherboard_is_incompatible():
    result = rules.check_cpu_motherboard(AM5_CPU, LGA1700_MOBO)
    assert result.compatible is False
    assert result.severity == "blocking"


def test_ddr5_ram_with_ddr4_only_motherboard_is_incompatible():
    ddr4_mobo = copy.deepcopy(AM5_DDR5_MOBO)
    ddr4_mobo["ram_type"] = "DDR4"
    result = rules.check_motherboard_ram(ddr4_mobo, DDR5_RAM)
    assert result.compatible is False
    assert result.severity == "blocking"


def test_ddr4_ram_matches_ddr4_motherboard():
    ddr4_mobo = copy.deepcopy(AM5_DDR5_MOBO)
    ddr4_mobo["ram_type"] = "DDR4"
    result = rules.check_motherboard_ram(ddr4_mobo, DDR4_RAM)
    assert result.compatible is True


# --- Additional rule coverage ---

def test_am4_cpu_incompatible_with_lga1700_motherboard():
    result = rules.check_cpu_motherboard(AM4_CPU, LGA1700_MOBO)
    assert result.compatible is False


def test_ram_exceeding_slot_count_is_incompatible():
    mobo = copy.deepcopy(AM5_DDR5_MOBO)
    mobo["ram_slots"] = 2
    ram = copy.deepcopy(DDR5_RAM)
    ram["sticks"] = 4
    result = rules.check_motherboard_ram(mobo, ram)
    assert result.compatible is False
    assert result.rule == "motherboard_ram_slots"


def test_gpu_too_long_for_case_is_incompatible():
    gpu = {"manufacturer": "AMD", "model": "Big GPU", "length_mm": 400}
    case = {"max_gpu_length_mm": 380}
    result = rules.check_gpu_case(gpu, case)
    assert result.compatible is False


def test_gpu_none_is_always_compatible_with_case():
    case = {"max_gpu_length_mm": 380}
    result = rules.check_gpu_case(None, case)
    assert result.compatible is True


def test_undersized_psu_is_blocking(base_build_kwargs):
    build = BuildComponents(**{**base_build_kwargs, "psu": PSU_250})
    results = CompatibilityChecker.run(build)
    psu_result = next(r for r in results if r.rule == "psu_wattage")
    assert psu_result.compatible is False
    assert psu_result.severity == "blocking"


def test_full_am5_ddr5_build_has_no_blocking_issues(base_build_kwargs):
    build = BuildComponents(**base_build_kwargs)
    results = CompatibilityChecker.run(build)
    assert CompatibilityChecker.has_blocking_issue(results) is False


def test_cooler_without_socket_support_is_incompatible(base_build_kwargs):
    build_kwargs = copy.deepcopy(base_build_kwargs)
    build_kwargs["cooler"]["socket_support"] = ["LGA1700"]  # AM5 CPU in base fixture
    build = BuildComponents(**build_kwargs)
    results = CompatibilityChecker.run(build)
    cooler_result = next(r for r in results if r.rule == "cooler_cpu_socket")
    assert cooler_result.compatible is False
    assert CompatibilityChecker.has_blocking_issue(results) is True


def test_storage_exceeding_m2_slots_is_incompatible():
    motherboard = {"m2_slots": 1, "sata_ports": 4}
    storage = [
        {"type": "SSD_NVME"},
        {"type": "SSD_NVME"},
    ]
    result = rules.check_storage_motherboard(storage, motherboard)
    assert result.compatible is False
    assert result.rule == "storage_motherboard_m2"


def test_lga1700_cpu_with_lga1700_motherboard_is_compatible():
    result = rules.check_cpu_motherboard(LGA1700_CPU, LGA1700_MOBO)
    assert result.compatible is True
