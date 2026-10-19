"""Maps Icecat product datasheets onto RigRAG's Product + spec rows.

Features are read by Icecat feature ID, which is stable across products and
categories (e.g. 2738 is "Length" everywhere). Raw values are already in the
feature's base unit (mm, GB, W), so no unit conversion is needed. A few
families of features (PCIe slots per generation, radiator sizes per
position) are matched by name because each variant has its own ID.

The mapper never guesses a value a rule depends on: anything missing stays
None and is reported by app.ingestion.validation.specs instead.
"""

import re
from dataclasses import dataclass

from app.ingestion.normalization.gpu_chips import lookup_chip
from app.models.product import Category

# Icecat category IDs, as reported in GeneralInfo.Category.CategoryID.
CATEGORY_BY_ICECAT_ID = {
    170: Category.gpu,
    164: Category.motherboard,
    911: Category.ram,
    1563: Category.storage,  # internal SSDs
    219: Category.storage,  # internal HDDs
    963: Category.psu,
    921: Category.cooler,  # also contains case fans and thermal paste; filtered below
    237: Category.case,
}
ICECAT_IDS_BY_CATEGORY: dict[Category, list[int]] = {}
for _icecat_id, _category in CATEGORY_BY_ICECAT_ID.items():
    ICECAT_IDS_BY_CATEGORY.setdefault(_category, []).append(_icecat_id)

BRAND_ALIASES = {"Kingston Technology": "Kingston", "GIGABYTE": "Gigabyte"}

# Feature IDs.
F_GPU_CHIP = 1654
F_GPU_FAMILY = 9787
F_GPU_MEMORY_GB = 18403
F_GPU_MEMORY_TYPE = 958
F_INTERFACE = 21
F_GPU_INTERFACE = 990
F_LENGTH_MM = 2738
F_HEIGHT_MM = 1464
F_MIN_PSU_W = 3462

F_SOCKET = 2325
F_SUPPORTED_SOCKETS = 7443
F_CHIPSET = 436
F_MOBO_FORM_FACTOR = 1262
F_SUPPORTED_MEMORY_TYPES = 7482
F_MAX_MEMORY_GB = 1452
F_MEMORY_SLOTS = 18674
F_M2_SLOTS = 20894
F_SATA_PORTS = 7826
F_WIFI = 3239
F_BLUETOOTH = 2183

F_RAM_CAPACITY_GB = 11381
F_RAM_LAYOUT = 16023
F_RAM_TYPE = 427
F_RAM_TRANSFER_RATE = 37536
F_RAM_CLOCK_SPEED = 1446
F_CAS_LATENCY = 1635
F_RAM_VOLTAGE = 6266
F_RAM_FORM_FACTOR = 7696
F_RAM_BUFFERED = 38209

F_SSD_CAPACITY_GB = 5586
F_HDD_CAPACITY_GB = 7
F_SSD_FORM_FACTOR = 21731
F_NVME = 29388
F_READ_MB_S = 1644
F_WRITE_MB_S = 1645

F_PSU_WATTAGE = 10713
F_80PLUS = 8110
F_ATX_VERSION = 8107
F_PSU_PURPOSE = 2585
F_PCIE_16PIN = 45531  # 12VHPWR / 12V-2x6

F_TYPE = 31
F_SUITABLE_LOCATION = 8173
F_COOLER_TDP_W = 11778

F_CASE_FORM_FACTORS = 5605
F_CASE_MAX_COOLER_MM = 9648
F_CASE_MAX_GPU_MM = 9649
F_CASE_PSU_FORM_FACTORS = 23947

_PCIE_SLOT_NAME = re.compile(r"^PCI Express x(\d+) \(Gen [\d.x]+\) slots$")
_RADIATOR_NAME = re.compile(r"^(Front|Top|Rear|Side|Bottom) radiator sizes supported$")
_PCIE_POWER_NAME = re.compile(r"^PCI Express power connectors \((8-pin|6\+2-pin)\)$")
_PCIE_GEN = re.compile(r"PCI Express (\d)\.\d")
_RAM_LAYOUT = re.compile(r"^(\d+)\s*x\s*\d+")
_VALID_RADIATOR_MM = {120, 140, 240, 280, 360, 420}


class MappingError(Exception):
    """The datasheet is not a part RigRAG can use (wrong sub-type, unknown chip, ...)."""


@dataclass
class MappedProduct:
    category: Category
    product: dict
    spec: dict


class Features:
    """Index over a datasheet's FeaturesGroups by feature ID and by English name."""

    def __init__(self, data: dict):
        self.by_id: dict[int, str] = {}
        self.by_name: dict[str, str] = {}
        for group in data.get("FeaturesGroups") or []:
            for feature in group.get("Features") or []:
                raw = feature.get("Value")
                if raw in (None, ""):
                    continue
                self.by_id[int(feature["Feature"]["ID"])] = str(raw)
                self.by_name[feature["Feature"]["Name"]["Value"]] = str(raw)

    def raw(self, feature_id: int) -> str | None:
        return self.by_id.get(feature_id)

    def num(self, feature_id: int) -> float | None:
        value = self.raw(feature_id)
        if value is None:
            return None
        try:
            return float(value.split(",")[0])
        except ValueError:
            return None

    def integer(self, feature_id: int) -> int | None:
        value = self.num(feature_id)
        return round(value) if value is not None else None

    def flag(self, feature_id: int) -> bool | None:
        value = self.raw(feature_id)
        return {"Y": True, "N": False}.get(value) if value is not None else None

    def named(self, pattern: re.Pattern) -> list[tuple[re.Match, str]]:
        return [(m, raw) for name, raw in self.by_name.items() if (m := pattern.match(name))]


def normalize_socket(value: str) -> str | None:
    """'Socket AM5' -> 'AM5', 'LGA 1700 (Socket V)' -> 'LGA1700'."""
    value = re.sub(r"\(.*?\)", "", value)
    value = re.sub(r"(?i)\bsocket\b", "", value)
    value = re.sub(r"\s+", "", value).upper()
    return value or None


def normalize_sockets(value: str | None) -> list[str]:
    if not value:
        return []
    sockets = (normalize_socket(part) for part in re.split(r",(?![^(]*\))", value))
    return sorted({s for s in sockets if s})


def normalize_form_factor(value: str) -> str | None:
    key = re.sub(r"[^a-z]", "", value.lower())
    return {
        "atx": "ATX",
        "microatx": "Micro-ATX",
        "matx": "Micro-ATX",
        "miniitx": "Mini-ITX",
        "extendedatx": "E-ATX",
        "eatx": "E-ATX",
    }.get(key)


def normalize_ram_type(value: str | None) -> str | None:
    if not value:
        return None
    match = re.search(r"DDR([45])", value.upper())
    return f"DDR{match.group(1)}" if match else None


def _pcie_gen(value: str | None) -> str | None:
    match = _PCIE_GEN.search(value or "")
    return f"{match.group(1)}.0" if match else None


def _brand(info: dict) -> str:
    brand = info.get("Brand") or ""
    return BRAND_ALIASES.get(brand, brand)


def _model_name(info: dict, category: Category, spec: dict) -> str:
    model = (info.get("ProductName") or info.get("Title") or "").strip()
    family = ((info.get("ProductFamily") or {}).get("Value") or "").strip()
    if family and family.lower() not in model.lower():
        model = f"{family} {model}"
    if category is Category.storage and spec.get("capacity_gb"):
        capacity = spec["capacity_gb"]
        label = f"{capacity // 1000}TB" if capacity >= 1000 and capacity % 1000 == 0 else f"{capacity}GB"
        if label.lower() not in model.lower().replace(" ", ""):
            model = f"{model} {label}"
    return model[:256]


def _map_gpu(f: Features) -> dict:
    chip_name = f.raw(F_GPU_CHIP)
    chip = lookup_chip(chip_name)
    if chip is None:
        raise MappingError(f"unknown GPU chip {chip_name!r}; add it to data/reference/gpu_chips.json")

    memory_type = (f.raw(F_GPU_MEMORY_TYPE) or "").upper()
    vram_type = "HBM" if memory_type.startswith("HBM") else memory_type or None

    return {
        "chip": chip.chip,
        "chip_vendor": chip.vendor,
        "vram_gb": f.integer(F_GPU_MEMORY_GB),
        "vram_type": vram_type,
        "tdp_w": chip.tdp_w,
        "recommended_psu_w": f.integer(F_MIN_PSU_W),
        "pcie_gen": _pcie_gen(f.raw(F_GPU_INTERFACE)),
        "length_mm": f.integer(F_LENGTH_MM),
        "performance_tier": chip.performance_tier,
    }


def _map_motherboard(f: Features) -> dict:
    sockets = normalize_sockets(f.raw(F_SOCKET) or f.raw(F_SUPPORTED_SOCKETS))
    chipset = f.raw(F_CHIPSET)
    if chipset:
        chipset = re.sub(r"^(AMD|Intel)\s+", "", chipset)

    pcie_slots: dict[str, int] = {}
    for match, raw in f.named(_PCIE_SLOT_NAME):
        lanes = f"x{match.group(1)}"
        pcie_slots[lanes] = pcie_slots.get(lanes, 0) + int(float(raw))

    return {
        "socket": sockets[0] if len(sockets) == 1 else None,
        "chipset": chipset,
        "form_factor": normalize_form_factor(f.raw(F_MOBO_FORM_FACTOR) or ""),
        "ram_type": normalize_ram_type(f.raw(F_SUPPORTED_MEMORY_TYPES)),
        "max_ram_gb": f.integer(F_MAX_MEMORY_GB),
        "ram_slots": f.integer(F_MEMORY_SLOTS),
        "m2_slots": f.integer(F_M2_SLOTS),
        "sata_ports": f.integer(F_SATA_PORTS),
        "wifi": bool(f.flag(F_WIFI)),
        "bluetooth": bool(f.flag(F_BLUETOOTH)),
        "pcie_slots": pcie_slots,
        "bios_flash_required": False,
    }


def _map_ram(f: Features) -> dict:
    form_factor = (f.raw(F_RAM_FORM_FACTOR) or "").upper()
    if "SO-DIMM" in form_factor or "SODIMM" in form_factor:
        raise MappingError("SO-DIMM (laptop) memory")
    # Values look like "Unregistered (unbuffered)" or "Registered (buffered)".
    if re.search(r"(?<!un)registered", (f.raw(F_RAM_BUFFERED) or "").lower()):
        raise MappingError("registered (server) memory")

    layout = _RAM_LAYOUT.match(f.raw(F_RAM_LAYOUT) or "")
    return {
        "capacity_gb": f.integer(F_RAM_CAPACITY_GB),
        "sticks": int(layout.group(1)) if layout else None,
        "ddr_gen": normalize_ram_type(f.raw(F_RAM_TYPE)),
        "speed_mhz": f.integer(F_RAM_TRANSFER_RATE) or f.integer(F_RAM_CLOCK_SPEED),
        "cas_latency": f.integer(F_CAS_LATENCY),
        "voltage": f.num(F_RAM_VOLTAGE),
    }


def _map_storage(f: Features, icecat_category_id: int) -> dict:
    interface = f.raw(F_INTERFACE) or ""
    if icecat_category_id == 219:
        return {
            "type": "HDD",
            "capacity_gb": f.integer(F_HDD_CAPACITY_GB),
            "interface": "SATA" if "ATA" in interface.upper() else interface or None,
            "pcie_gen": None,
            "read_mb_s": None,
            "write_mb_s": None,
        }

    is_m2 = (f.raw(F_SSD_FORM_FACTOR) or "").upper().startswith("M.2")
    is_nvme = bool(f.flag(F_NVME)) or "PCI EXPRESS" in interface.upper()
    if is_nvme:
        storage_type, interface_label = "SSD_NVME", "M.2 NVMe"
    else:
        storage_type, interface_label = "SSD_SATA", "M.2 SATA" if is_m2 else "SATA"

    return {
        "type": storage_type,
        "capacity_gb": f.integer(F_SSD_CAPACITY_GB),
        "interface": interface_label,
        "pcie_gen": _pcie_gen(interface) if is_nvme else None,
        "read_mb_s": f.integer(F_READ_MB_S),
        "write_mb_s": f.integer(F_WRITE_MB_S),
    }


def _map_psu(f: Features) -> dict:
    purpose = f.raw(F_PSU_PURPOSE)
    if purpose and "PC" not in purpose:
        raise MappingError(f"PSU purpose {purpose!r} is not a desktop PC")

    rating = f.raw(F_80PLUS)
    efficiency = None
    if rating:
        match = re.search(r"80 PLUS (White|Bronze|Silver|Gold|Platinum|Titanium)", rating, re.I)
        if match:
            efficiency = f"80+ {match.group(1).title()}"
        elif rating.strip().upper() == "80 PLUS":
            efficiency = "80+ White"

    eight_pin = sum(int(float(raw)) for _, raw in f.named(_PCIE_POWER_NAME))
    sixteen_pin = f.integer(F_PCIE_16PIN) or 0
    atx_version = f.raw(F_ATX_VERSION)

    return {
        "wattage": f.integer(F_PSU_WATTAGE),
        "efficiency_rating": efficiency,
        "atx_version": f"ATX {atx_version}" if atx_version else None,
        "pcie_connectors": {"pcie_8pin": eight_pin} if eight_pin else {},
        "supports_12vhpwr": sixteen_pin > 0,
    }


def _map_cooler(f: Features) -> dict:
    location = f.raw(F_SUITABLE_LOCATION) or ""
    if "Processor" not in location:
        raise MappingError(f"cooling product for {location or 'unknown location'!r}, not a CPU cooler")

    kind = (f.raw(F_TYPE) or "").lower()
    if "air" in kind:
        cooler_type = "air"
    elif "liquid" in kind or "all-in-one" in kind or "aio" in kind:
        cooler_type = "aio"
    else:
        raise MappingError(f"unsupported cooler type {kind!r}")

    radiator_mm = None
    if cooler_type == "aio":
        for name, raw in f.by_name.items():
            if "radiator" in name.lower() and "material" not in name.lower():
                sizes = [int(float(v)) for v in re.findall(r"\d+(?:\.\d+)?", raw)]
                matches = [s for s in sizes if s in _VALID_RADIATOR_MM]
                if matches:
                    radiator_mm = max(matches)
                    break

    return {
        "type": cooler_type,
        "socket_support": normalize_sockets(f.raw(F_SUPPORTED_SOCKETS)),
        # For an AIO, the datasheet "Height" is the pump block, not a clearance figure.
        "height_mm": f.integer(F_HEIGHT_MM) if cooler_type == "air" else None,
        "radiator_mm": radiator_mm,
        "tdp_rating_w": f.integer(F_COOLER_TDP_W),
    }


def _map_case(f: Features) -> dict:
    form_factors = []
    for part in (f.raw(F_CASE_FORM_FACTORS) or "").split(","):
        normalized = normalize_form_factor(part)
        if normalized and normalized not in form_factors:
            form_factors.append(normalized)

    radiator_support: dict[str, int] = {}
    for match, raw in f.named(_RADIATOR_NAME):
        sizes = [int(float(v)) for v in raw.split(",") if v.strip().replace(".", "").isdigit()]
        if sizes:
            radiator_support[match.group(1).lower()] = max(sizes)

    psu_form_factor = (f.raw(F_CASE_PSU_FORM_FACTORS) or "ATX").split(",")[0].strip()

    return {
        "form_factor_support": form_factors,
        "max_gpu_length_mm": f.integer(F_CASE_MAX_GPU_MM),
        "max_cooler_height_mm": f.integer(F_CASE_MAX_COOLER_MM),
        "psu_form_factor": psu_form_factor[:16],
        "radiator_support": radiator_support,
        "fan_mounts": {},
    }


def map_icecat_product(data: dict) -> MappedProduct:
    """Converts one Live API `data` object into Product and spec field dicts.

    Raises MappingError for datasheets outside RigRAG's categories or that
    describe a part the engine cannot use (laptop RAM, case fans, ...).
    """
    info = data.get("GeneralInfo") or {}
    try:
        icecat_category_id = int((info.get("Category") or {}).get("CategoryID"))
    except (TypeError, ValueError):
        raise MappingError("datasheet has no category")
    category = CATEGORY_BY_ICECAT_ID.get(icecat_category_id)
    if category is None:
        raise MappingError(f"Icecat category {icecat_category_id} is not a supported part category")

    f = Features(data)
    if category is Category.gpu:
        spec = _map_gpu(f)
    elif category is Category.motherboard:
        spec = _map_motherboard(f)
    elif category is Category.ram:
        spec = _map_ram(f)
    elif category is Category.storage:
        spec = _map_storage(f, icecat_category_id)
    elif category is Category.psu:
        spec = _map_psu(f)
    elif category is Category.cooler:
        spec = _map_cooler(f)
    else:
        spec = _map_case(f)

    icecat_id = int(info["IcecatId"])
    gtins = sorted({g for g in (info.get("GTIN") or []) if g})
    product = {
        "manufacturer": _brand(info),
        "model": _model_name(info, category, spec),
        "icecat_id": icecat_id,
        "mpn": (info.get("BrandPartCode") or None),
        "gtins": gtins,
        "source": f"Specs Icecat, product {icecat_id} (https://icecat.biz)",
        "performance_tier": spec.get("performance_tier"),
    }
    return MappedProduct(category=category, product=product, spec=spec)
