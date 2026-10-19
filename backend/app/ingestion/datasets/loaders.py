"""Per-source loaders: each turns one raw dataset into (category, record)
pairs using the shared field names in app.ingestion.datasets.merge.SCHEMAS.

Loaders only translate formats and units. Choosing between conflicting
values happens in merge.py.
"""

import csv
import json
import re
from collections.abc import Iterator
from pathlib import Path

from app.ingestion.normalization.icecat import normalize_form_factor

Record = tuple[str, dict]

_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
_TRADEMARKS = re.compile(r"[™®©]")

DESKTOP_SOCKETS = {
    "AM1", "AM2", "AM2+", "AM3", "AM3+", "AM4", "AM5", "FM1", "FM2", "FM2+",
    "LGA775", "LGA1150", "LGA1151", "LGA1151-2", "LGA1155", "LGA1156", "LGA1200", "LGA1366",
    "LGA1700", "LGA1851", "LGA2011", "LGA2011-3", "LGA2066",
    "TR4", "STRX4", "STR5", "SWRX8",
}

CHIPSETS = re.compile(
    r"\b(A320|A520|A620A?|B350|B450|B550|B650E?|B840|B850|X370|X470|X570|X670E?|X870E?|"
    r"H81|H87|H97|B85|Z87|Z97|H110|H170|H270|B150|B250|Z170|Z270|H310|H370|B360|B365|Z370|Z390|"
    r"H410|H470|B460|Z490|H510|H570|B560|Z590|H610|H670|B660|Z690|H770|B760|Z790|B860|Z890|X99|X299|TRX40|TRX50|WRX80|WRX90)\b"
)


# --- value helpers -----------------------------------------------------------

def number(value) -> float | None:
    """First number in a value: 750 -> 750.0, "4.2 GHz" -> 4.2, "1,600 W" -> 1600.0."""
    if value is None or value == "" or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = _NUMBER.search(str(value).replace(",", ""))
    return float(match.group()) if match else None


def number_max(value) -> float | None:
    """Upper end of a range: "500 - 3050 RPM" -> 3050.0, [17, 28.5] -> 28.5."""
    if isinstance(value, list):
        nums = [number(v) for v in value if number(v) is not None]
        return max(nums) if nums else None
    if isinstance(value, str):
        nums = [float(n) for n in _NUMBER.findall(value.replace(",", ""))]
        return max(nums) if nums else None
    return number(value)


def integer(value) -> int | None:
    n = number(value)
    return round(n) if n is not None else None


def capacity_gb(value) -> int | None:
    """ "2 TB" -> 2000, "800 GB" -> 800, 2000 -> 2000."""
    n = number(value)
    if n is None:
        return None
    return round(n * 1000) if isinstance(value, str) and "TB" in value.upper() else round(n)


def text(value) -> str | None:
    if value is None or isinstance(value, (list, dict)):
        return None
    value = re.sub(r"\s+", " ", _TRADEMARKS.sub("", str(value))).strip()
    return None if value.lower() in ("", "nan", "none", "n/a", "-") else value


def clean_cpu_name(name: str) -> str:
    name = _TRADEMARKS.sub("", name)
    name = re.sub(r"\(.*?\)", "", name)
    name = re.sub(r"\bProcessor\b", "", name)
    return re.sub(r"\s+", " ", name).strip()


def socket(value) -> str | None:
    value = text(value)
    if not value:
        return None
    parts = []
    for part in value.split(","):
        part = re.sub(r"\(.*?\)", "", part)
        part = re.sub(r"(?i)\bsocket\b", "", part).upper().replace(" ", "")
        part = part.replace("FCLGA", "LGA")
        part = {"STR4": "TR4", "SP3R2": "TR4"}.get(part, part)
        if part:
            parts.append(part)
    return ", ".join(dict.fromkeys(parts)) or None


def is_desktop_socket(value: str | None) -> bool:
    return bool(value) and any(s in DESKTOP_SOCKETS for s in value.split(", "))


def form_factor(value) -> str | None:
    value = text(value)
    return (normalize_form_factor(value) or value) if value else None


def ddr(value) -> str | None:
    match = re.search(r"DDR\s?(\d)", str(value or "").upper())
    return f"DDR{match.group(1)}" if match else None


def ddr_list(value) -> str | None:
    gens = sorted({f"DDR{g}" for g in re.findall(r"DDR\s?(\d)", str(value or "").upper())})
    return ", ".join(gens) or None


def efficiency(value) -> str | None:
    value = text(value)
    if not value:
        return None
    level = re.search(r"(white|bronze|silver|gold|platinum|titanium)", value, re.I)
    if level:
        return f"80+ {level.group(1).title()}"
    return "80+" if re.search(r"80\s*\+|plus", value, re.I) else value


def modular(value) -> str | None:
    value = text(value)
    if value is None:
        return None
    lowered = value.lower()
    if lowered.startswith("full"):
        return "Full"
    if lowered.startswith("semi"):
        return "Semi"
    if lowered in ("no", "false", "non-modular", "non modular"):
        return "No"
    return value


def chip_vendor_name(value) -> str | None:
    """ATI was acquired by AMD; sources disagree on which name to use."""
    value = text(value)
    return "AMD" if value and value.upper() in ("ATI", "AMD", "AMD/ATI") else value


def bus_interface(value) -> str | None:
    """ "Gen5 x16", "PCIe ×16 2.1", and "PCIe 5.0 x16" become "PCIe 5.0 x16"; "AGP 8×" becomes "AGP 8x"."""
    value = text(value)
    if not value:
        return None
    s = value.replace("×", "x")
    if re.search(r"(?i)agp", s):
        speed = re.search(r"(\d)x", s)
        return f"AGP {speed.group(1)}x" if speed else "AGP"
    if re.search(r"(?i)pcie|pci express|gen\s?\d", s):
        gen = re.search(r"(?i)(?:gen\s?|pcie\s?)(\d(?:\.\d)?)\b|\b(\d\.\d)\b", s)
        lanes = re.search(r"x\s?(\d+)", s)
        gen_value = next((g for g in gen.groups() if g), None) if gen else None
        if gen_value and "." not in gen_value:
            gen_value += ".0"
        return " ".join(p for p in ("PCIe", gen_value, f"x{lanes.group(1)}" if lanes else None) if p)
    return value


def gpu_vendor(chip: str | None) -> str | None:
    lowered = (chip or "").lower()
    if any(k in lowered for k in ("geforce", "rtx", "gtx", "quadro", "titan", "nvidia")):
        return "NVIDIA"
    if any(k in lowered for k in ("radeon", "rx ", "firepro", "amd")):
        return "AMD"
    if any(k in lowered for k in ("arc ", "intel")):
        return "Intel"
    return None


def gpu_segment(name: str, generation: str = "") -> str:
    s = f"{name} {generation}".lower()
    if re.search(r"\b(mobile|mobility|max-q|laptop|notebook|igp|embedded|go)\b|\b\d{3,4}m\b|-mw\b|\(.*-m\b|\bradeon e\d", s):
        return "mobile"
    if re.search(r"tesla|instinct|data center|\bserver\b|\bgrid\b|\bh100\b|\ba100\b|\bmi\d{2,3}\b|radeon sky", s):
        return "datacenter"
    if re.search(r"quadro|radeon pro|firepro|firegl|\brtx a\d|ada generation|\bwx\s?\d", s):
        return "workstation"
    if re.search(r"\b(uhd|iris|hd graphics)\b|\bvega \d+\b", s):
        return "integrated"
    return "desktop"


def infer_chipset(name: str) -> str | None:
    match = CHIPSETS.search(name.upper())
    return match.group(1) if match else None


def infer_ram_type(board_socket: str | None, name: str) -> str | None:
    if not board_socket:
        return None
    if board_socket in ("AM5", "LGA1851"):
        return "DDR5"
    if board_socket in ("AM4", "LGA1200", "LGA1151", "LGA1151-2", "TR4", "STRX4"):
        return "DDR4"
    if board_socket == "LGA1700":
        return "DDR4" if re.search(r"\bD4\b|DDR4", name.upper()) else "DDR5"
    return None


# --- loaders -----------------------------------------------------------------

def load_amd_official(path: Path) -> Iterator[Record]:
    with (path / "amd-cpus.csv").open(encoding="utf-8-sig", errors="replace") as fh:
        for row in csv.DictReader(fh):
            if row.get("Platform") not in ("Desktop", "Boxed Processor") or not text(row.get("Model")):
                continue
            yield "cpu", {
                "name": clean_cpu_name(row["Model"]),
                "manufacturer": "AMD",
                "socket": socket(row.get("CPU Socket")),
                "cores": integer(row.get("# of CPU Cores")),
                "threads": integer(row.get("# of Threads")),
                "base_clock_ghz": number(row.get("Base Clock")),
                "boost_clock_ghz": number(row.get("Max. Boost Clock ¹ ²")),
                "tdp_w": integer(row.get("Default TDP")),
                "integrated_graphics": text(row.get("Graphics Model")) if "Discrete" not in (row.get("Graphics Model") or "") else None,
                "memory_types": ddr_list(row.get("System Memory Type")),
                "release": text(row.get("Launch Date")),
            }


def load_intel_ark(path: Path) -> Iterator[Record]:
    with (path / "intel-cpus.csv").open(encoding="utf-8", errors="replace") as fh:
        for row in csv.DictReader(fh):
            if row.get("MarketSegment") != "Desktop" or not text(row.get("CpuName")):
                continue
            base = next((row[c] for c in ("ClockSpeed", "PerfCoreBaseFreq", "PCoreBaseFreq") if row.get(c)), None)
            tdp = row.get("MaxTDP") or row.get("ProcessorBasePower")
            yield "cpu", {
                "name": clean_cpu_name(row["CpuName"]),
                "manufacturer": "Intel",
                "socket": socket(row.get("SocketsSupported")),
                "cores": integer(row.get("CoreCount")),
                "threads": integer(row.get("ThreadCount")),
                "base_clock_ghz": number(base),
                "boost_clock_ghz": number(row.get("ClockSpeedMax")),
                "tdp_w": integer(tdp),
                "integrated_graphics": text(row.get("ProcessorGraphicsModelId")),
                "memory_types": ddr_list(row.get("MemoryTypes")),
                "microarchitecture": text(re.sub(r"^Products formerly ", "", row.get("CodeNameText") or "")),
                "release": text(row.get("BornOnDate")),
            }


def load_iblessi(path: Path) -> Iterator[Record]:
    data = json.loads((path / "pc-builder-parts.json").read_text(encoding="utf-8"))
    for row in data.get("cpu", []):
        yield "cpu", {
            "name": f"{row['vendor']} {row['model']}", "manufacturer": row["vendor"], "socket": socket(row.get("socket")),
            "cores": row.get("cores"), "threads": row.get("threads"), "boost_clock_ghz": row.get("boost_ghz"),
            "tdp_w": row.get("tdp_w"), "memory_types": ddr_list(row.get("ram_type")),
        }
    for row in data.get("gpu", []):
        vendor = gpu_vendor(row["model"]) or row["vendor"].split()[0]
        yield "gpu_chip", {
            "name": row["model"], "vendor": vendor, "vram_gb": row.get("vram_gb"), "tdp_w": row.get("tgp_w"),
            "bus_interface": bus_interface(row.get("pcie")),
        }
    for row in data.get("motherboard", []):
        yield "motherboard", {
            "name": f"{row['vendor']} {row['model']}", "manufacturer": row["vendor"], "socket": socket(row.get("socket")),
            "chipset": re.sub(r"^(AMD|Intel)\s+", "", row.get("chipset") or "") or None,
            "form_factor": form_factor(row.get("form_factor")), "ram_type": ddr(row.get("ram_type")),
            "max_ram_gb": row.get("ram_max_gb"), "ram_slots": row.get("ram_slots"), "m2_slots": row.get("m2_slots"),
        }
    for row in data.get("ram", []):
        kit = re.match(r"(\d+)\s*x\s*(\d+)", row.get("kit") or "")
        yield "ram", {
            "name": f"{row['vendor']} {row['model']}", "manufacturer": row["vendor"], "ddr_gen": ddr(row.get("ram_type")),
            "speed_mts": row.get("speed_mtps"), "modules": int(kit.group(1)) if kit else None,
            "module_gb": int(kit.group(2)) if kit else None, "capacity_gb": row.get("capacity_gb"), "cas_latency": row.get("cas_latency"),
        }
    for row in data.get("storage", []):
        interface = row.get("interface") or ""
        yield "storage", {
            "name": f"{row['vendor']} {row['model']}", "manufacturer": row["vendor"],
            "type": "HDD" if "HDD" in interface.upper() or "RPM" in (row.get("model") or "").upper() else "SSD",
            "capacity_gb": row.get("capacity_gb"), "interface": text(interface), "form_factor": text(row.get("form_factor")),
            "read_mb_s": row.get("read_mbps"), "write_mb_s": row.get("write_mbps"),
        }
    for row in data.get("psu", []):
        yield "psu", {
            "name": f"{row['vendor']} {row['model']}", "manufacturer": row["vendor"], "wattage": row.get("wattage_w"),
            "efficiency": efficiency(row.get("efficiency")), "modular": modular(row.get("modular")),
            "form_factor": "SFX" if "SFX" in (row.get("form_factor") or "").upper() else "ATX",
        }
    for row in data.get("case", []):
        yield "case", {
            "name": f"{row['vendor']} {row['model']}", "manufacturer": row["vendor"], "type": text(row.get("form_factor")),
            "supported_form_factors": ", ".join(filter(None, (form_factor(f) for f in row.get("supports_form_factors") or []))) or None,
            "max_gpu_length_mm": row.get("max_gpu_length_mm"), "max_cooler_height_mm": row.get("max_cpu_cooler_mm"),
        }


def load_bottleneckpc(path: Path) -> Iterator[Record]:
    data = json.loads((path / "data.json").read_text(encoding="utf-8"))
    for row in data.get("cpus", []):
        name = row["name"]
        manufacturer = "AMD" if re.search(r"ryzen|athlon|threadripper|\bfx\b|amd", name, re.I) else "Intel"
        yield "cpu", {
            "name": name if name.startswith(manufacturer) else f"{manufacturer} {name}", "manufacturer": manufacturer,
            "socket": socket(row.get("socket")), "tdp_w": row.get("tdp"), "msrp_usd": row.get("msrp"),
            "release": str(row["release_year"]) if row.get("release_year") else None,
        }
    for row in data.get("gpus", []):
        yield "gpu_chip", {
            "name": row["name"], "vendor": gpu_vendor(row["name"]), "vram_gb": row.get("vram"), "tdp_w": row.get("tdp"),
            "msrp_usd": row.get("msrp"), "release": str(row["release_year"]) if row.get("release_year") else None,
        }


def load_wikipedia_gpus(path: Path) -> Iterator[Record]:
    data = json.loads((path / "gpu.json").read_text(encoding="utf-8"))
    for row in data.values():
        name = text(row.get("Model name")) or text(row.get("Model"))
        if not name or gpu_segment(name) != "desktop":
            continue
        yield "gpu_chip", {
            "name": name, "vendor": chip_vendor_name(row.get("Vendor")), "vram_gb": number(row.get("Memory Size (GiB)")),
            "memory_type": text(row.get("Memory Bus type")), "bus_interface": bus_interface(row.get("Bus interface")),
            "tdp_w": integer(row.get("TDP (Watts)")), "msrp_usd": integer(row.get("Release price (USD) MSRP")),
            "release": text(str(row.get("Launch") or "")[:10]),
        }


def load_techpowerup_gpus(path: Path) -> Iterator[Record]:
    data = json.loads((path / "all-gpus.json").read_text(encoding="utf-8"))
    rows = data if isinstance(data, list) else next(v for v in data.values() if isinstance(v, list))
    for row in rows:
        name = text(row.get("name"))
        if not name or gpu_segment(name, row.get("generation") or "") != "desktop":
            continue
        yield "gpu_chip", {
            "name": name, "vendor": chip_vendor_name(row.get("manufacturer")), "architecture": text(row.get("architecture")),
            "vram_gb": number(row.get("memorySize")), "memory_type": text(row.get("memoryType")),
            "bus_interface": bus_interface(row.get("busInterface")), "tdp_w": integer(row.get("tdp")),
            "suggested_psu_w": integer(row.get("suggestedPSU")), "power_connectors": text(row.get("powerConnectors")),
            "release": text(row.get("releaseDate")),
        }


def _pcpp_ram(speed, modules_value) -> dict:
    """PCPartPicker speed/modules come as "DDR5-6000"/"2 x 16GB" (HF) or [5, 6000]/[2, 16] (docyx)."""
    if isinstance(speed, list) and len(speed) == 2:
        gen, mts = f"DDR{speed[0]}", integer(speed[1])
    else:
        gen = ddr(speed)
        match = re.search(r"-(\d+)", str(speed or ""))
        mts = int(match.group(1)) if match else None
    if isinstance(modules_value, list) and len(modules_value) == 2:
        count, size = integer(modules_value[0]), integer(modules_value[1])
    else:
        match = re.match(r"(\d+)\s*x\s*(\d+)", str(modules_value or ""))
        count, size = (int(match.group(1)), int(match.group(2))) if match else (None, None)
    return {
        "ddr_gen": gen, "speed_mts": mts, "modules": count, "module_gb": size,
        "capacity_gb": count * size if count and size else None,
    }


def _pcpp_record(category: str, name: str, manufacturer: str | None, specs: dict, price_field: str, price) -> Record | None:
    """Maps one PCPartPicker row (HF `specs` dict or docyx row) to a shared record."""
    get = specs.get
    common = {"name": name, "manufacturer": manufacturer, price_field: number(price)}

    if category == "cpu":
        graphics = text(get("Integrated Graphics", get("graphics")))
        return "cpu", {**common,
            "cores": integer(get("Core Count", get("core_count"))),
            "base_clock_ghz": number(get("Performance Core Clock", get("core_clock"))),
            "boost_clock_ghz": number(get("Performance Core Boost Clock", get("boost_clock"))),
            "tdp_w": integer(get("TDP", get("tdp"))),
            "integrated_graphics": graphics,
            "microarchitecture": text(get("Microarchitecture", get("microarchitecture"))),
        }
    if category == "video-card":
        chip = text(get("Chipset", get("chipset")))
        return "gpu", {**common,
            "chip": chip, "chip_vendor": gpu_vendor(chip), "vram_gb": number(get("Memory", get("memory"))),
            "length_mm": integer(get("Length", get("length"))), "core_clock_mhz": integer(get("Core Clock", get("core_clock"))),
            "boost_clock_mhz": integer(get("Boost Clock", get("boost_clock"))), "color": text(get("Color", get("color"))),
        }
    if category == "motherboard":
        board_socket = socket(get("Socket / CPU", get("socket")))
        derived = []
        chipset = infer_chipset(name)
        if chipset:
            derived.append("chipset")
        ram_type = infer_ram_type(board_socket, name)
        if ram_type:
            derived.append("ram_type")
        return "motherboard", {**common,
            "socket": board_socket, "chipset": chipset, "ram_type": ram_type,
            "form_factor": form_factor(get("Form Factor", get("form_factor"))),
            "max_ram_gb": integer(get("Memory Max", get("max_memory"))),
            "ram_slots": integer(get("Memory Slots", get("memory_slots"))), "color": text(get("Color", get("color"))),
            "_derived": derived,
        }
    if category == "memory":
        return "ram", {**common,
            **_pcpp_ram(get("Speed", get("speed")), get("Modules", get("modules"))),
            "cas_latency": integer(get("CAS Latency", get("cas_latency"))),
            "first_word_latency_ns": number(get("First Word Latency", get("first_word_latency"))),
            "color": text(get("Color", get("color"))),
        }
    if category == "internal-hard-drive":
        kind = str(get("Type", get("type")) or "")
        cache = get("Cache", get("cache"))
        return "storage", {**common,
            "type": "SSD" if "SSD" in kind.upper() else "HDD" if kind else None,
            "capacity_gb": capacity_gb(get("Capacity", get("capacity"))),
            "interface": text(get("Interface", get("interface"))), "form_factor": text(get("Form Factor", get("form_factor"))),
            "cache_mb": integer(cache) if cache not in (None, "") else None,
        }
    if category == "power-supply":
        return "psu", {**common,
            "wattage": integer(get("Wattage", get("wattage"))), "efficiency": efficiency(get("Efficiency Rating", get("efficiency"))),
            "modular": modular(get("Modular", get("modular"))), "form_factor": text(get("Type", get("type"))),
            "color": text(get("Color", get("color"))),
        }
    if category == "cpu-cooler":
        radiator = integer(get("Radiator Size", get("size")))
        return "cooler", {**common,
            "type": "aio" if radiator else "air", "radiator_mm": radiator,
            "fan_rpm": integer(number_max(get("Fan RPM", get("rpm")))),
            "noise_db": number_max(get("Noise Level", get("noise_level"))), "color": text(get("Color", get("color"))),
        }
    if category == "case":
        psu = get("Power Supply", get("psu"))
        return "case", {**common,
            "type": text(get("Type", get("type"))), "side_panel": text(get("Side Panel", get("side_panel"))),
            "volume_l": number(get("External Volume", get("external_volume"))),
            "internal_35_bays": integer(get("Internal 3.5\" Bays", get("internal_35_bays"))),
            "psu_included": bool(number(psu)) if psu not in (None, "") else False,
            "color": text(get("Color", get("color"))),
        }
    return None


def load_pcpartpicker_hf(path: Path) -> Iterator[Record]:
    with (path / "pcpartpicker_parts.jsonl").open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            record = _pcpp_record(row["category"], text(row["name"]), text(row.get("brand")), row.get("specs") or {}, "price_eur", row.get("price_eur"))
            if record:
                record[1]["pcpartpicker_url"] = row.get("url")
                yield record


def load_pcpartpicker_docyx(path: Path, brands: list[str] | None = None) -> Iterator[Record]:
    """`brands` (longest first) maps a name prefix to its brand, since docyx
    rows carry no brand field; unknown prefixes fall back to the first word."""
    brands = brands or []
    for category_file in sorted(path.glob("*.json")):
        category = category_file.stem
        for row in json.loads(category_file.read_text(encoding="utf-8")):
            name = text(row.get("name"))
            if not name:
                continue
            brand = next((b for b in brands if name.lower().startswith(b.lower() + " ")), name.split()[0])
            record = _pcpp_record(category, name, brand, row, "price_usd", row.get("price"))
            if record:
                yield record


def load_mehax(path: Path) -> Iterator[Record]:
    data = json.loads((path / "CPU.json").read_text(encoding="utf-8"))
    rows = data if isinstance(data, list) else next(v for v in data.values() if isinstance(v, list))
    for row in rows:
        cpu_socket = socket(row.get("socket"))
        if not is_desktop_socket(cpu_socket):
            continue
        meta, cores, specs = row.get("metadata") or {}, row.get("cores") or {}, row.get("specifications") or {}
        clocks = (row.get("clocks") or {}).get("performance") or {}
        graphics = text(((specs.get("integratedGraphics") or {}).get("model")))
        yield "cpu", {
            "name": text(meta.get("name")), "manufacturer": text(meta.get("manufacturer")), "socket": cpu_socket,
            "cores": cores.get("total"), "threads": cores.get("threads"), "base_clock_ghz": number(clocks.get("base")),
            "boost_clock_ghz": number(clocks.get("boost")), "tdp_w": integer(specs.get("tdp")),
            "integrated_graphics": graphics, "memory_types": ddr_list(" ".join((specs.get("memory") or {}).get("types") or [])),
            "microarchitecture": text(row.get("microarchitecture")),
        }


def load_passmark(path: Path) -> Iterator[Record]:
    with (path / "benchmark-cpus.csv").open(encoding="utf-8", errors="replace") as fh:
        for row in csv.DictReader(fh):
            cpu_socket = socket(row.get("Socket"))
            name = text(row.get("CpuName"))
            if not name or not is_desktop_socket(cpu_socket) or not re.match(r"(AMD|Intel)\b", name):
                continue
            yield "cpu", {
                "name": name, "manufacturer": name.split()[0], "socket": cpu_socket, "cores": integer(row.get("Cores")),
                "threads": integer(row.get("Threads")), "base_clock_ghz": number(row.get("ClockSpeed")),
                "boost_clock_ghz": number(row.get("TurboSpeed")), "tdp_w": integer(row.get("TDP")),
                "release": text(row.get("ReleaseDate")),
            }


LOADERS = {
    "amd_official": load_amd_official,
    "intel_ark": load_intel_ark,
    "iblessi": load_iblessi,
    "bottleneckpc": load_bottleneckpc,
    "wikipedia_gpus": load_wikipedia_gpus,
    "techpowerup_gpus": load_techpowerup_gpus,
    "pcpartpicker_hf": load_pcpartpicker_hf,
    "pcpartpicker_docyx": load_pcpartpicker_docyx,
    "mehax": load_mehax,
    "passmark": load_passmark,
}
