import copy

from app.recommendation.compatibility import rules
from app.recommendation.compatibility.build_components import BuildComponents
from app.recommendation.compatibility.checker import CompatibilityChecker
from tests.conftest import (
    AM4_CPU,
    AM5_CPU,
    AM5_DDR5_MOBO,
    CASE_ATX,
    COOLER_UNIVERSAL_AIR,
    DDR4_RAM,
    DDR5_RAM,
    GPU,
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


def test_case_without_listed_clearance_warns_instead_of_blocking():
    case = {**copy.deepcopy(CASE_ATX), "max_gpu_length_mm": None}
    result = rules.check_gpu_case(copy.deepcopy(GPU), case)
    assert result.compatible is True
    assert result.severity == "warning"
    assert rules.is_unverified(result)
    assert "couldn't be checked" in result.message


def test_cooler_without_listed_sockets_warns_instead_of_blocking():
    cooler = {**copy.deepcopy(COOLER_UNIVERSAL_AIR), "socket_support": []}
    result = rules.check_cooler_cpu(cooler, copy.deepcopy(AM5_CPU))
    assert result.compatible is True and rules.is_unverified(result)
    assert "AM5" in result.message


def test_air_cooler_without_height_or_case_without_clearance_is_unverified():
    no_height = {**copy.deepcopy(COOLER_UNIVERSAL_AIR), "height_mm": None}
    no_clearance = {**copy.deepcopy(CASE_ATX), "max_cooler_height_mm": None}
    assert rules.is_unverified(rules.check_cooler_case(no_height, copy.deepcopy(CASE_ATX)))
    assert rules.is_unverified(rules.check_cooler_case(copy.deepcopy(COOLER_UNIVERSAL_AIR), no_clearance))


def test_aio_in_case_without_listed_radiator_mounts_is_unverified():
    aio = {"manufacturer": "ARCTIC", "model": "Liquid Freezer III 360", "type": "aio",
           "socket_support": ["AM5"], "radiator_mm": 360}
    case = {**copy.deepcopy(CASE_ATX), "radiator_support": {}}
    assert rules.is_unverified(rules.check_cooler_case(aio, case))


def test_unverified_checks_lower_the_build_score(base_build_kwargs):
    from app.recommendation.scoring.scoring import W_UNVERIFIED, score_build
    from app.schemas import BuildRequest, UseCase

    priced = {role: [{**s, "price_php": 2000} for s in part] if role == "storage" else {**part, "price_php": 5000}
              for role, part in base_build_kwargs.items()}
    build = BuildComponents(**priced)
    request = BuildRequest(budget_php=70000, use_case=[UseCase.gaming])
    bands = {"cpu": (10000, 15000)}
    verified = CompatibilityChecker.run(build)
    unverified_build = BuildComponents(**{**priced, "case": {**priced["case"], "max_gpu_length_mm": None}})
    unverified = CompatibilityChecker.run(unverified_build)

    assert not CompatibilityChecker.has_blocking_issue(unverified)
    gap = score_build(build, request, bands, verified).total - score_build(unverified_build, request, bands, unverified).total
    assert abs(gap - W_UNVERIFIED) < 1e-9
