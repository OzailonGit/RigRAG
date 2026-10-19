"""Deterministic compatibility rules. No LLM involvement.

Each function inspects the concrete part dicts (see build_components.py)
and returns a single CompatibilityResult. One function per part
relationship keeps each rule independently testable against known-good
and known-bad combinations.

When a spec a rule needs is unknown (a case with no listed clearance, a
cooler with no listed sockets), the rule can't pass or fail the pair. It
returns a warning whose rule name ends in UNVERIFIED_SUFFIX instead, so the
user is told what to check and scoring can prefer verified parts.
"""

from app.recommendation.compatibility.power import recommended_psu_watts, round_up_to_common_tier
from app.recommendation.compatibility.results import CompatibilityResult

UNVERIFIED_SUFFIX = "_unverified"


def _part_name(part: dict) -> str:
    return f"{part['manufacturer']} {part['model']}"


def _unverified(rule: str, message: str) -> CompatibilityResult:
    return CompatibilityResult(rule=rule + UNVERIFIED_SUFFIX, compatible=True, severity="warning", message=message)


def is_unverified(result: CompatibilityResult) -> bool:
    return result.rule.endswith(UNVERIFIED_SUFFIX)


def check_cpu_motherboard(cpu: dict, motherboard: dict) -> CompatibilityResult:
    if cpu["socket"] != motherboard["socket"]:
        return CompatibilityResult(
            rule="cpu_motherboard_socket",
            compatible=False,
            severity="blocking",
            message=(
                f"CPU socket {cpu['socket']} does not match motherboard socket "
                f"{motherboard['socket']} ({motherboard['manufacturer']} {motherboard['model']})."
            ),
        )
    if motherboard.get("bios_flash_required"):
        return CompatibilityResult(
            rule="cpu_motherboard_bios",
            compatible=True,
            severity="warning",
            message=(
                f"{motherboard['manufacturer']} {motherboard['model']} may need a BIOS update "
                f"before it will boot with this CPU. Verify the board's supported-CPU list or "
                f"flash it with an older, known-compatible CPU first."
            ),
        )
    return CompatibilityResult(
        rule="cpu_motherboard_socket",
        compatible=True,
        severity="info",
        message=f"CPU socket {cpu['socket']} matches motherboard socket.",
    )


def check_motherboard_ram(motherboard: dict, ram: dict) -> CompatibilityResult:
    if motherboard["ram_type"] != ram["ddr_gen"]:
        return CompatibilityResult(
            rule="motherboard_ram_type",
            compatible=False,
            severity="blocking",
            message=(
                f"Motherboard supports {motherboard['ram_type']} only; selected RAM is "
                f"{ram['ddr_gen']}."
            ),
        )
    if ram["sticks"] > motherboard["ram_slots"]:
        return CompatibilityResult(
            rule="motherboard_ram_slots",
            compatible=False,
            severity="blocking",
            message=(
                f"RAM kit needs {ram['sticks']} DIMM slots; motherboard only has "
                f"{motherboard['ram_slots']}."
            ),
        )
    if ram["capacity_gb"] > motherboard["max_ram_gb"]:
        return CompatibilityResult(
            rule="motherboard_ram_capacity",
            compatible=False,
            severity="blocking",
            message=(
                f"RAM capacity {ram['capacity_gb']}GB exceeds motherboard maximum of "
                f"{motherboard['max_ram_gb']}GB."
            ),
        )
    return CompatibilityResult(
        rule="motherboard_ram",
        compatible=True,
        severity="info",
        message=f"{ram['ddr_gen']} RAM fits the motherboard's slots and capacity limit.",
    )


def check_motherboard_case(motherboard: dict, case: dict) -> CompatibilityResult:
    if motherboard["form_factor"] not in case["form_factor_support"]:
        return CompatibilityResult(
            rule="motherboard_case_form_factor",
            compatible=False,
            severity="blocking",
            message=(
                f"Case supports {case['form_factor_support']}; motherboard is "
                f"{motherboard['form_factor']}."
            ),
        )
    return CompatibilityResult(
        rule="motherboard_case_form_factor",
        compatible=True,
        severity="info",
        message=f"{motherboard['form_factor']} motherboard fits the case.",
    )


def check_gpu_case(gpu: dict | None, case: dict) -> CompatibilityResult:
    if gpu is None:
        return CompatibilityResult(
            rule="gpu_case_clearance",
            compatible=True,
            severity="info",
            message="No discrete GPU selected; clearance check not applicable.",
        )
    if case.get("max_gpu_length_mm") is None:
        return _unverified(
            "gpu_case_clearance",
            f"{_part_name(case)} doesn't list a GPU clearance, so the {gpu['length_mm']}mm "
            f"{_part_name(gpu)} couldn't be checked. Confirm the case fits it before buying.",
        )
    if gpu["length_mm"] > case["max_gpu_length_mm"]:
        return CompatibilityResult(
            rule="gpu_case_clearance",
            compatible=False,
            severity="blocking",
            message=(
                f"{gpu['manufacturer']} {gpu['model']} is {gpu['length_mm']}mm long; case "
                f"only clears {case['max_gpu_length_mm']}mm."
            ),
        )
    return CompatibilityResult(
        rule="gpu_case_clearance",
        compatible=True,
        severity="info",
        message=f"GPU length {gpu['length_mm']}mm fits within the case's {case['max_gpu_length_mm']}mm clearance.",
    )


def check_cooler_cpu(cooler: dict, cpu: dict) -> CompatibilityResult:
    if not cooler.get("socket_support"):
        return _unverified(
            "cooler_cpu_socket",
            f"{_part_name(cooler)} doesn't list its supported sockets, so {cpu['socket']} support "
            f"couldn't be checked. Confirm it ships with a {cpu['socket']} mounting kit.",
        )
    if cpu["socket"] not in cooler["socket_support"]:
        return CompatibilityResult(
            rule="cooler_cpu_socket",
            compatible=False,
            severity="blocking",
            message=(
                f"{cooler['manufacturer']} {cooler['model']} does not list {cpu['socket']} in its "
                f"supported sockets ({cooler['socket_support']})."
            ),
        )
    if cooler.get("tdp_rating_w") is not None and cooler["tdp_rating_w"] < cpu["tdp_w"]:
        return CompatibilityResult(
            rule="cooler_cpu_tdp",
            compatible=False,
            severity="warning",
            message=(
                f"Cooler is rated for {cooler['tdp_rating_w']}W; CPU TDP is {cpu['tdp_w']}W. "
                f"It may run hot under sustained load."
            ),
        )
    return CompatibilityResult(
        rule="cooler_cpu",
        compatible=True,
        severity="info",
        message="Cooler supports this CPU's socket and TDP.",
    )


def check_cooler_case(cooler: dict, case: dict) -> CompatibilityResult:
    if cooler["type"] == "air":
        if cooler.get("height_mm") is None or case.get("max_cooler_height_mm") is None:
            missing = _part_name(cooler) + "'s height" if cooler.get("height_mm") is None else _part_name(case) + "'s cooler clearance"
            return _unverified(
                "cooler_case_clearance",
                f"{missing} isn't listed, so the cooler couldn't be checked against the case. "
                f"Compare the two before buying.",
            )
        if cooler["height_mm"] > case["max_cooler_height_mm"]:
            return CompatibilityResult(
                rule="cooler_case_clearance",
                compatible=False,
                severity="blocking",
                message=(
                    f"Air cooler height {cooler['height_mm']}mm exceeds case clearance of "
                    f"{case['max_cooler_height_mm']}mm."
                ),
            )
    elif cooler["type"] == "aio":
        radiator = cooler.get("radiator_mm")
        supported_sizes = [v for v in case.get("radiator_support", {}).values() if v]
        if not radiator or not supported_sizes:
            return _unverified(
                "cooler_case_clearance",
                f"The radiator size or {_part_name(case)}'s radiator mounts aren't listed, so the "
                f"AIO couldn't be checked against the case. Confirm there's a mount for it before buying.",
            )
        if not any(radiator <= size for size in supported_sizes):
            return CompatibilityResult(
                rule="cooler_case_clearance",
                compatible=False,
                severity="blocking",
                message=(
                    f"{radiator}mm AIO radiator has no matching mount in this case "
                    f"({case['radiator_support']})."
                ),
            )
    return CompatibilityResult(
        rule="cooler_case_clearance",
        compatible=True,
        severity="info",
        message="Cooler fits within the case's clearance/radiator mounts.",
    )


def check_psu_system(psu: dict, cpu: dict, gpu: dict | None) -> CompatibilityResult:
    required = recommended_psu_watts(cpu, gpu)
    required_tiered = round_up_to_common_tier(required)

    if psu["wattage"] < required:
        return CompatibilityResult(
            rule="psu_wattage",
            compatible=False,
            severity="blocking",
            message=(
                f"Estimated system draw plus 25% headroom is ~{required}W; the "
                f"{psu['manufacturer']} {psu['model']} is only {psu['wattage']}W. "
                f"Recommend at least a {required_tiered}W unit."
            ),
        )
    if gpu is not None and psu["wattage"] < gpu["recommended_psu_w"]:
        return CompatibilityResult(
            rule="psu_gpu_vendor_recommendation",
            compatible=True,
            severity="warning",
            message=(
                f"{gpu['manufacturer']} {gpu['model']} vendor-recommended PSU is "
                f"{gpu['recommended_psu_w']}W; selected PSU is {psu['wattage']}W. Our own "
                f"headroom-based estimate ({required}W) is still satisfied, but there's less margin."
            ),
        )
    if gpu is not None and gpu["tdp_w"] >= 200 and psu.get("pcie_connectors", {}).get("pcie_8pin", 0) < 2 and not psu.get("supports_12vhpwr"):
        return CompatibilityResult(
            rule="psu_connectors",
            compatible=False,
            severity="blocking",
            message=(
                f"{gpu['manufacturer']} {gpu['model']} (TDP {gpu['tdp_w']}W) typically needs 2x "
                f"8-pin PCIe or a 12VHPWR connector; PSU reports {psu.get('pcie_connectors')}."
            ),
        )
    return CompatibilityResult(
        rule="psu_wattage",
        compatible=True,
        severity="info",
        message=f"PSU wattage ({psu['wattage']}W) covers estimated draw with headroom (~{required}W needed).",
    )


def check_storage_motherboard(storage_devices: list[dict], motherboard: dict) -> CompatibilityResult:
    nvme_count = sum(1 for s in storage_devices if s["type"] == "SSD_NVME")
    sata_count = sum(1 for s in storage_devices if s["type"] in ("SSD_SATA", "HDD"))

    if nvme_count > motherboard["m2_slots"]:
        return CompatibilityResult(
            rule="storage_motherboard_m2",
            compatible=False,
            severity="blocking",
            message=(
                f"Build has {nvme_count} M.2 NVMe drive(s); motherboard only has "
                f"{motherboard['m2_slots']} M.2 slot(s)."
            ),
        )
    if sata_count > motherboard["sata_ports"]:
        return CompatibilityResult(
            rule="storage_motherboard_sata",
            compatible=False,
            severity="blocking",
            message=(
                f"Build has {sata_count} SATA device(s); motherboard only has "
                f"{motherboard['sata_ports']} SATA port(s)."
            ),
        )
    return CompatibilityResult(
        rule="storage_motherboard",
        compatible=True,
        severity="info",
        message="Storage devices fit within available M.2/SATA connectivity.",
    )
