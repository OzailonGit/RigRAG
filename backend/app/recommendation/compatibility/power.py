"""Deterministic system power estimation.

Estimated draw = CPU TDP + GPU TDP + fixed overhead for motherboard, RAM,
storage, and fans. A headroom margin on top gives the recommended PSU
capacity. This is an estimate, not a measurement; it gives the
compatibility engine a documented number to check the PSU against.
"""

# Fixed overhead for components whose draw is small and fairly constant
# regardless of exact model (motherboard, RAM, storage, fans, AIO pump).
BASE_OVERHEAD_W = 60

# Recommended safety margin on top of estimated draw.
HEADROOM_RATIO = 0.25

# Round the recommended PSU wattage up to these commonly available tiers.
COMMON_PSU_TIERS_W = [450, 550, 650, 750, 850, 1000, 1200]


def estimate_system_watts(cpu: dict, gpu: dict | None) -> int:
    cpu_w = cpu["tdp_w"]
    gpu_w = gpu["tdp_w"] if gpu else 0
    return cpu_w + gpu_w + BASE_OVERHEAD_W


def recommended_psu_watts(cpu: dict, gpu: dict | None) -> int:
    estimated = estimate_system_watts(cpu, gpu)
    with_headroom = estimated * (1 + HEADROOM_RATIO)
    return round(with_headroom)


def round_up_to_common_tier(watts: int) -> int:
    for tier in COMMON_PSU_TIERS_W:
        if tier >= watts:
            return tier
    return watts
