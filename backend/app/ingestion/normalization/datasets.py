"""Maps rows from the combined public datasets (data/combined/*.jsonl) onto
RigRAG's Product + spec rows.

The combined rows come from app.ingestion.datasets.build. They describe most
parts well but leave out a few fields the compatibility rules read. Those are
handled one of three ways:

- Derived from a reliable convention: a case's supported board sizes from its
  type ("ATX Mid Tower" takes ATX, Micro-ATX, and Mini-ITX), a board's Wi-Fi
  from its name, a GPU's memory type and PCIe generation from its chip.
- Set to a conservative floor and listed in ASSUMED_FIELDS: M.2 slots and
  SATA ports on boards, PCIe power connectors on PSUs.
- Looked up in a hand-maintained reference file: case clearances
  (data/reference/case_specs.json) and cooler sockets and heights
  (data/reference/cooler_specs.json), filled in from manufacturer spec pages
  for the most popular models.
- Left unknown and listed in MappedRow.unverified: those same fields for
  every other case and cooler. The compatibility rules warn about unknown
  specs instead of checking them, and scoring prefers verified parts.

Performance tiers are the same hand-tuned heuristic as the seed catalog,
expressed as rules over core count, generation, chipset, and so on.
"""

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from app.ingestion.normalization.gpu_chips import chip_key, lookup_chip
from app.ingestion.normalization.icecat import normalize_form_factor, normalize_ram_type
from app.models.product import Category
from app.recommendation.compatibility.power import round_up_to_common_tier

REFERENCE_DIR = Path(__file__).resolve().parents[4] / "data" / "reference"

# Sockets the engine builds on. Older platforms are listed in the datasets
# but are no longer sold new.
SUPPORTED_SOCKETS = {"AM4", "AM5", "LGA1200", "LGA1700", "LGA1851"}
CURRENT_SOCKETS = {"AM5", "LGA1700", "LGA1851"}

ASSUMED_FIELDS = {
    Category.motherboard: ["m2_slots", "sata_ports"],
    Category.psu: ["pcie_connectors"],
}

# Lowest counts found on boards for the supported sockets. A real board
# usually has more, so the storage rule only errs toward rejecting builds.
ASSUMED_M2_SLOTS = 1
ASSUMED_SATA_PORTS = 2
# ATX units at this wattage and up ship with at least two 6+2-pin PCIe leads.
TWO_PCIE_LEADS_FROM_W = 650

# Vendor PSU recommendations track board power plus roughly 400 W for the
# rest of the system (RTX 4060: 115 W, 550 W; RTX 4090: 450 W, 850 W).
GPU_PSU_SYSTEM_ALLOWANCE_W = 400

_CPU_EXCLUDED = re.compile(r"\b(EPYC|Xeon|Threadripper)\b", re.I)
_CPU_CLASS = re.compile(r"\b(?:Ryzen|Core\s+i|Core\s+Ultra)\s*(\d)\b", re.I)
_CPU_BUDGET_LINE = re.compile(r"\b(Athlon|Pentium|Celeron|A\d+-\d{4})", re.I)
_CURRENT_ARCHES = {"zen 4", "zen 5", "raptor lake", "raptor lake refresh", "arrow lake"}
_PREVIOUS_ARCHES = {"zen 3", "alder lake", "rocket lake"}
_CPU_TIERS = {
    "current": {3: "entry", 5: "mid", 7: "high", 9: "enthusiast"},
    "previous": {3: "entry", 5: "entry", 7: "mid", 9: "high"},
    "older": {3: "entry", 5: "entry", 7: "entry", 9: "mid"},
}

KNOWN_CHIPSETS = {
    "A320", "B350", "X370", "B450", "X470", "A520", "B550", "X570",
    "A620", "B650", "B650E", "X670", "X670E", "B840", "B850", "X870", "X870E",
    "H410", "B460", "H470", "Z490", "W480", "Q470", "H510", "B560", "H570", "Z590", "W580", "Q570",
    "H610", "B660", "H670", "Z690", "W680", "Q670", "B760", "H770", "Z790",
    "H810", "B860", "Z890", "W880",
}
_CHIPSET_IN_NAME = re.compile(r"(?<![A-Z0-9])([ABHQWXZ]\d{3}E?)")
_WIFI_IN_NAME = re.compile(r"wi-?fi|\bAX\b|\bBE\b", re.I)

_CASE_TYPE_FORM_FACTORS = [
    (re.compile(r"^(E-?ATX|ATX)", re.I), ["ATX", "Micro-ATX", "Mini-ITX"]),
    (re.compile(r"^(MicroATX|mATX)", re.I), ["Micro-ATX", "Mini-ITX"]),
    (re.compile(r"^(Mini ITX|ITX)", re.I), ["Mini-ITX"]),
]

_VRAM_TYPES = ("GDDR6X", "GDDR7", "GDDR6", "HBM")
_CHIP_VARIANT_SUFFIX = re.compile(r"\s*\b(\d+\s*GB|LHR)\b", re.I)
_PCIE_GEN = re.compile(r"PCIe\s*(\d)(?:\.\d)?", re.I)
_TRAILING_CAPACITY = re.compile(r"\s+\d+(?:\.\d+)?\s*[GT]B$", re.I)
_INVISIBLE = re.compile("[\u200b-\u200f\ufeff]")
# Server (ECC, registered) and laptop (SO-DIMM) memory, by name or by the
# part-number prefixes Kingston (KSM, KVR..S) and Samsung (M391, M393) use.
_NON_DESKTOP_RAM = re.compile(
    r"\b(ECC|RDIMM|LRDIMM|Registered|Server|SO-?DIMM|Laptop|Notebook)\b|\bKSM\d|\bKVR\d+S|\bM39[13]", re.I
)


class MappingError(Exception):
    """The row describes a part the engine cannot use, or misses a field a rule needs."""


@dataclass
class MappedRow:
    category: Category
    manufacturer: str
    model: str
    performance_tier: str | None
    spec: dict
    assumed: list[str]
    unverified: list[str] = field(default_factory=list)
    spec_source: str | None = None


@dataclass(frozen=True)
class ChipInfo:
    memory_type: str | None
    bus_interface: str | None


def strip_manufacturer(name: str, manufacturer: str) -> str:
    name = _INVISIBLE.sub("", name).strip()
    if name.lower().startswith(manufacturer.lower() + " "):
        return name[len(manufacturer) + 1:].strip()
    return name.strip()


def capacity_label(capacity_gb: int) -> str:
    if capacity_gb >= 1000 and capacity_gb % 1000 == 0:
        return f"{capacity_gb // 1000}TB"
    return f"{capacity_gb}GB"


def _require(row: dict, *keys: str) -> None:
    missing = [k for k in keys if row.get(k) in (None, "")]
    if missing:
        raise MappingError(f"missing {', '.join(missing)}")


def _pcie_gen(value: str | None) -> str | None:
    match = _PCIE_GEN.search(value or "")
    return f"{match.group(1)}.0" if match else None


def cpu_generation(model: str, architecture: str) -> str:
    """"Ryzen 5 7600" -> "Ryzen 7000", "Core i5-13400F" -> "Core 13th Gen",
    "Core Ultra 7 265K" -> "Core Ultra 200S". Falls back to the architecture."""
    if match := re.search(r"Ryzen\s+\d\s+(?:PRO\s+)?(\d)\d{3}", model, re.I):
        return f"Ryzen {match.group(1)}000"
    if match := re.search(r"Core\s+i\d-(\d{4,5})", model, re.I):
        digits = match.group(1)
        # Five-digit model numbers carry a two-digit generation (13400 -> 13).
        return f"Core {digits[:2] if len(digits) == 5 else digits[0]}th Gen"
    if match := re.search(r"Core\s+Ultra\s+\d\s+(\d)\d{2}", model, re.I):
        return f"Core Ultra {match.group(1)}00S"
    return architecture


def cpu_tier(model: str, architecture: str) -> str:
    if _CPU_BUDGET_LINE.search(model):
        return "entry"
    match = _CPU_CLASS.search(model)
    if not match:
        raise MappingError(f"unrecognized CPU line: {model}")
    arch = architecture.lower()
    era = "current" if arch in _CURRENT_ARCHES else "previous" if arch in _PREVIOUS_ARCHES else "older"
    return _CPU_TIERS[era].get(int(match.group(1)), "entry")


def map_cpu(row: dict) -> MappedRow:
    _require(row, "name", "socket")
    if row["socket"] not in SUPPORTED_SOCKETS:
        raise MappingError(f"socket {row['socket']} is not supported")
    if _CPU_EXCLUDED.search(row["name"]):
        raise MappingError("server or workstation CPU")
    _require(row, "cores", "threads", "base_clock_ghz", "tdp_w", "microarchitecture")
    manufacturer = row.get("manufacturer") or row["name"].split()[0]
    model = strip_manufacturer(row["name"], manufacturer)
    architecture = row["microarchitecture"]
    base = float(row["base_clock_ghz"])
    boost = float(row.get("boost_clock_ghz") or 0)
    graphics = (row.get("integrated_graphics") or "").strip()
    spec = {
        "socket": row["socket"],
        "cores": int(row["cores"]),
        "threads": int(row["threads"]),
        "base_clock_ghz": base,
        # Some sources report 0 or omit boost for parts without one.
        "boost_clock_ghz": boost if boost >= base else base,
        "tdp_w": int(row["tdp_w"]),
        "has_igpu": bool(graphics) and graphics.lower() != "none",
        "architecture": architecture,
        "generation": cpu_generation(model, architecture),
    }
    return MappedRow(Category.cpu, manufacturer, model, cpu_tier(model, architecture), spec, [])


def load_chip_table(rows) -> dict[str, list[dict]]:
    """Indexes gpu_chip.jsonl rows by chip_key, with and without a VRAM suffix,
    so "GeForce RTX 5060 Ti 16 GB" is found from "GeForce RTX 5060 Ti"."""
    table: dict[str, list[dict]] = {}
    for row in rows:
        table.setdefault(chip_key(row["name"]), []).append(row)
        base = chip_key(_CHIP_VARIANT_SUFFIX.sub("", row["name"]))
        table.setdefault(base, []).append(row)
    return table


def chip_info(chip: str, vram_gb: int, table: dict[str, list[dict]]) -> ChipInfo:
    rows = table.get(chip_key(chip), []) + table.get(chip_key(_CHIP_VARIANT_SUFFIX.sub("", chip)), [])
    for row in sorted(rows, key=lambda r: round(r.get("vram_gb") or 0) != vram_gb):
        if row.get("memory_type") or row.get("bus_interface"):
            return ChipInfo(row.get("memory_type"), row.get("bus_interface"))
    return ChipInfo(None, None)


def map_gpu(row: dict, chip_table: dict[str, list[dict]]) -> MappedRow:
    _require(row, "full_name", "manufacturer", "chip", "vram_gb", "length_mm")
    reference = lookup_chip(_CHIP_VARIANT_SUFFIX.sub("", row["chip"]))
    if reference is None:
        raise MappingError(f"chip {row['chip']} is not in gpu_chips.json")
    vram_gb = round(float(row["vram_gb"]))
    info = chip_info(row["chip"], vram_gb, chip_table)
    memory = (info.memory_type or "").upper()
    vram_type = next((t for t in _VRAM_TYPES if t in memory), None)
    pcie_gen = _pcie_gen(info.bus_interface)
    if vram_type is None or pcie_gen is None:
        raise MappingError(f"no memory type or bus interface for chip {row['chip']}")
    # The reference TDP is the highest across memory variants, so it keeps
    # PSU sizing conservative when a card's own figure is lower.
    tdp_w = max(int(row.get("tdp_w") or 0), reference.tdp_w)
    spec = {
        "chip": reference.chip,
        "chip_vendor": reference.vendor,
        "vram_gb": vram_gb,
        "vram_type": vram_type,
        "tdp_w": tdp_w,
        "recommended_psu_w": round_up_to_common_tier(tdp_w + GPU_PSU_SYSTEM_ALLOWANCE_W),
        "pcie_gen": pcie_gen,
        "length_mm": int(row["length_mm"]),
        "performance_tier": reference.performance_tier,
    }
    model = strip_manufacturer(row["full_name"], row["manufacturer"])
    # Memory variants of a card often share a name ("Ventus 2X GeForce RTX 5060 Ti").
    if f"{vram_gb}gb" not in model.lower().replace(" ", ""):
        model = f"{model} {vram_gb}GB"
    return MappedRow(Category.gpu, row["manufacturer"], model, reference.performance_tier, spec, [])


def motherboard_chipset(row: dict) -> str | None:
    if row.get("chipset"):
        chipset = re.sub(r"^(AMD|Intel)\s+", "", row["chipset"]).upper()
        if chipset in KNOWN_CHIPSETS:
            return chipset
    for match in _CHIPSET_IN_NAME.finditer(row["name"].upper()):
        if match.group(1) in KNOWN_CHIPSETS:
            return match.group(1)
    return None


def motherboard_tier(chipset: str, socket: str) -> str:
    current = socket in CURRENT_SOCKETS
    if chipset.startswith("A") or chipset in {"H410", "H510", "H610", "H810"}:
        return "entry"
    if chipset[0] in "XZW":
        return "high" if current else "mid"
    return "mid" if current else "entry"


def map_motherboard(row: dict) -> MappedRow:
    _require(row, "name", "socket")
    if row["socket"] not in SUPPORTED_SOCKETS:
        raise MappingError(f"socket {row['socket']} is not supported")
    _require(row, "manufacturer", "form_factor", "max_ram_gb", "ram_slots")
    ram_type = normalize_ram_type(row.get("ram_type"))
    form_factor = normalize_form_factor(row["form_factor"])
    chipset = motherboard_chipset(row)
    if ram_type is None:
        raise MappingError("missing ram_type")
    if form_factor is None:
        raise MappingError(f"form factor {row['form_factor']} is not supported")
    if chipset is None:
        raise MappingError("chipset not found")
    wifi = bool(_WIFI_IN_NAME.search(row["name"]))
    assumed = list(ASSUMED_FIELDS[Category.motherboard])
    m2_slots = row.get("m2_slots")
    if m2_slots is not None:
        assumed.remove("m2_slots")
    spec = {
        "socket": row["socket"],
        "chipset": chipset,
        "form_factor": form_factor,
        "ram_type": ram_type,
        "max_ram_gb": int(row["max_ram_gb"]),
        "ram_slots": int(row["ram_slots"]),
        "m2_slots": int(m2_slots) if m2_slots is not None else ASSUMED_M2_SLOTS,
        "sata_ports": ASSUMED_SATA_PORTS,
        "wifi": wifi,
        # Board Wi-Fi ships as a combo module, so Bluetooth comes with it.
        "bluetooth": wifi,
        "pcie_slots": {},
        "bios_flash_required": False,
    }
    model = strip_manufacturer(row["name"], row["manufacturer"])
    return MappedRow(Category.motherboard, row["manufacturer"], model, motherboard_tier(chipset, row["socket"]), spec, assumed)


def ram_tier(ddr_gen: str, speed: int) -> str:
    if ddr_gen == "DDR5":
        return "high" if speed >= 6000 else "mid"
    return "mid" if speed >= 3600 else "entry"


def map_ram(row: dict) -> MappedRow:
    _require(row, "name", "manufacturer", "ddr_gen", "speed_mts", "modules", "module_gb", "capacity_gb")
    ddr_gen = normalize_ram_type(row["ddr_gen"])
    if ddr_gen is None:
        raise MappingError(f"{row['ddr_gen']} is not supported")
    if _NON_DESKTOP_RAM.search(row["name"]):
        raise MappingError("server or laptop memory")
    capacity = int(row["capacity_gb"])
    modules = int(row["modules"])
    speed = int(row["speed_mts"])
    cas = int(row["cas_latency"]) if row.get("cas_latency") else None
    base = _TRAILING_CAPACITY.sub("", strip_manufacturer(row["name"], row["manufacturer"]))
    model = f"{base} {capacity}GB ({modules}x{int(row['module_gb'])}) {ddr_gen}-{speed}"
    if cas:
        model += f" CL{cas}"
    spec = {
        "capacity_gb": capacity,
        "sticks": modules,
        "ddr_gen": ddr_gen,
        "speed_mhz": speed,
        "cas_latency": cas,
        "voltage": None,
    }
    return MappedRow(Category.ram, row["manufacturer"], model, ram_tier(ddr_gen, speed), spec, [])


def map_storage(row: dict) -> MappedRow:
    _require(row, "name", "manufacturer", "type", "capacity_gb", "interface", "form_factor")
    interface = row["interface"]
    form_factor = row["form_factor"]
    if row["type"] == "SSD" and form_factor.startswith("M.2") and "PCIE" in interface.upper():
        kind, label, pcie_gen = "SSD_NVME", "M.2 NVMe", _pcie_gen(interface)
    elif row["type"] in ("SSD", "HDD") and interface.startswith("SATA") and form_factor in ('2.5"', '3.5"'):
        kind, label, pcie_gen = ("SSD_SATA" if row["type"] == "SSD" else "HDD"), "SATA", None
    else:
        # M.2 SATA, mSATA, SAS, and add-in cards don't fit the storage rule's slot model.
        raise MappingError(f"{row['type']} {interface} ({form_factor}) is not supported")
    capacity = int(row["capacity_gb"])
    tier = {"5.0": "high", "4.0": "mid"}.get(pcie_gen or "", "entry")
    base = _TRAILING_CAPACITY.sub("", strip_manufacturer(row["name"], row["manufacturer"]))
    spec = {
        "type": kind,
        "capacity_gb": capacity,
        "interface": label,
        "pcie_gen": pcie_gen,
        "read_mb_s": row.get("read_mb_s"),
        "write_mb_s": row.get("write_mb_s"),
    }
    return MappedRow(Category.storage, row["manufacturer"], f"{base} {capacity_label(capacity)}", tier, spec, [])


def psu_efficiency(value: str | None) -> str | None:
    if not value:
        return None
    return "80+ White" if value.strip() == "80+" else value.strip()


def psu_tier(efficiency: str | None) -> str:
    if efficiency in ("80+ Platinum", "80+ Titanium"):
        return "high"
    if efficiency in ("80+ Gold", "80+ Silver"):
        return "mid"
    return "entry"


def map_psu(row: dict) -> MappedRow:
    _require(row, "name", "manufacturer", "wattage", "form_factor")
    if row["form_factor"] != "ATX":
        raise MappingError(f"{row['form_factor']} PSUs are not supported")
    wattage = int(row["wattage"])
    efficiency = psu_efficiency(row.get("efficiency"))
    spec = {
        "wattage": wattage,
        "efficiency_rating": efficiency,
        "atx_version": None,
        "pcie_connectors": {"pcie_8pin": 2} if wattage >= TWO_PCIE_LEADS_FROM_W else {},
        "supports_12vhpwr": False,
    }
    base = strip_manufacturer(row["name"], row["manufacturer"])
    model = f"{base} {wattage}W"
    return MappedRow(Category.psu, row["manufacturer"], model, psu_tier(efficiency), spec, list(ASSUMED_FIELDS[Category.psu]))


def _reference_key(manufacturer: str, model: str) -> tuple[str, str]:
    return re.sub(r"[^a-z0-9]", "", manufacturer.lower()), re.sub(r"[^a-z0-9]", "", model.lower())


@lru_cache
def load_reference(filename: str, key: str) -> dict[tuple[str, str], dict]:
    """Indexes a reference file's entries by (manufacturer, model) and by each alias."""
    entries = json.loads((REFERENCE_DIR / filename).read_text(encoding="utf-8"))[key]
    index = {}
    for entry in entries:
        for name in [entry["model"], *entry.get("aliases", [])]:
            index[_reference_key(entry["manufacturer"], name)] = entry
    return index


def lookup_reference(filename: str, key: str, manufacturer: str, model: str) -> dict | None:
    return load_reference(filename, key).get(_reference_key(manufacturer, model))


def cooler_sockets(listed: list[str]) -> list[str]:
    """Supported sockets, limited to the ones the engine builds on.

    LGA1851 keeps LGA1700's cooler mounting holes and keep-out zone, so a
    cooler listed for LGA1700 fits LGA1851 too, even where the spec page
    predates Arrow Lake.
    """
    sockets = {s for s in listed if s in SUPPORTED_SOCKETS}
    if "LGA1700" in sockets:
        sockets.add("LGA1851")
    return sorted(sockets)


def map_cooler(row: dict) -> MappedRow:
    _require(row, "name", "manufacturer", "type")
    if row["type"] not in ("air", "aio"):
        raise MappingError(f"cooler type {row['type']} is not supported")
    model = strip_manufacturer(row["name"], row["manufacturer"])
    reference = lookup_reference("cooler_specs.json", "coolers", row["manufacturer"], model)
    if reference and reference["type"] != row["type"]:
        raise MappingError(f"reference lists {model} as {reference['type']}, dataset as {row['type']}")

    spec = {
        "type": row["type"],
        "socket_support": cooler_sockets(reference["socket_support"]) if reference else [],
        "height_mm": reference.get("height_mm") if reference and row["type"] == "air" else None,
        "radiator_mm": row.get("radiator_mm") if row["type"] == "aio" else None,
        "tdp_rating_w": None,
    }
    unverified = [] if reference else ["socket_support"]
    if row["type"] == "air" and spec["height_mm"] is None:
        unverified.append("height_mm")
    if row["type"] == "aio" and spec["radiator_mm"] is None:
        unverified.append("radiator_mm")
    radiator = spec["radiator_mm"]
    # Every radiator size of an AIO shares one name ("Aqua Elite V3").
    if radiator and str(radiator) not in model:
        model = f"{model} {radiator}mm"
    tier = "high" if row["type"] == "aio" else "mid"
    return MappedRow(Category.cooler, row["manufacturer"], model, tier, spec, [], unverified,
                     reference["source"] if reference else None)


def case_form_factors(row: dict) -> list[str]:
    if row.get("supported_form_factors"):
        names = (s.strip() for s in row["supported_form_factors"].split(","))
        sizes = {normalize_form_factor("Mini-ITX" if n == "ITX" else n) for n in names}
        return sorted(s for s in sizes if s)
    for pattern, sizes in _CASE_TYPE_FORM_FACTORS:
        if pattern.search(row.get("type") or ""):
            return sizes
    return []


def map_case(row: dict) -> MappedRow:
    _require(row, "name", "manufacturer")
    if row.get("psu_included"):
        raise MappingError("case ships with its own PSU")
    model = strip_manufacturer(row["name"], row["manufacturer"])
    reference = lookup_reference("case_specs.json", "cases", row["manufacturer"], model)
    if reference:
        spec = {
            "form_factor_support": reference["form_factor_support"],
            "max_gpu_length_mm": reference["max_gpu_length_mm"],
            "max_cooler_height_mm": reference["max_cooler_height_mm"],
            "psu_form_factor": "ATX",
            "radiator_support": reference["radiator_support"],
            "fan_mounts": {},
        }
        return MappedRow(Category.case, row["manufacturer"], model, "mid", spec, [], [], reference["source"])

    form_factors = case_form_factors(row)
    if not form_factors:
        raise MappingError(f"case type {row.get('type')} is not supported")
    if row.get("max_gpu_length_mm") and row.get("max_cooler_height_mm"):
        spec = {
            "form_factor_support": form_factors,
            "max_gpu_length_mm": int(row["max_gpu_length_mm"]),
            "max_cooler_height_mm": int(row["max_cooler_height_mm"]),
            "psu_form_factor": "ATX",
            "radiator_support": {},
            "fan_mounts": {},
        }
        return MappedRow(Category.case, row["manufacturer"], model, "mid", spec, [], ["radiator_support"])
    # Towers take an ATX PSU. Desktop, slim, and small ITX cases often need SFX
    # or TFX, which the catalog's PSUs aren't, so they need verified specs.
    if "tower" not in (row.get("type") or "").lower() or form_factors == ["Mini-ITX"]:
        raise MappingError(f"{row.get('type')} case needs verified specs (PSU size unknown)")
    spec = {
        "form_factor_support": form_factors,
        "max_gpu_length_mm": None,
        "max_cooler_height_mm": None,
        "psu_form_factor": "ATX",
        "radiator_support": {},
        "fan_mounts": {},
    }
    unverified = ["max_gpu_length_mm", "max_cooler_height_mm", "radiator_support"]
    return MappedRow(Category.case, row["manufacturer"], model, "mid", spec, [], unverified)
