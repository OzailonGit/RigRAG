from app.recommendation.compatibility import rules
from app.recommendation.compatibility.build_components import BuildComponents
from app.recommendation.compatibility.results import CompatibilityResult


class CompatibilityChecker:
    """Runs every applicable compatibility rule against a candidate build.

    Called twice: during candidate pruning, to discard incompatible
    combinations before scoring, and on the final build, so the ✓/✗ report
    shown to the user always reflects the build actually returned.
    """

    @staticmethod
    def run(build: BuildComponents) -> list[CompatibilityResult]:
        results = [
            rules.check_cpu_motherboard(build.cpu, build.motherboard),
            rules.check_motherboard_ram(build.motherboard, build.ram),
            rules.check_motherboard_case(build.motherboard, build.case),
            rules.check_gpu_case(build.gpu, build.case),
            rules.check_cooler_cpu(build.cooler, build.cpu),
            rules.check_cooler_case(build.cooler, build.case),
            rules.check_psu_system(build.psu, build.cpu, build.gpu),
            rules.check_storage_motherboard(build.storage, build.motherboard),
        ]
        return results

    @staticmethod
    def has_blocking_issue(results: list[CompatibilityResult]) -> bool:
        return any(not r.compatible and r.severity == "blocking" for r in results)
