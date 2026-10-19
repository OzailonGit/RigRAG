"""Turns a structured product dict into the natural-language document that
gets embedded for semantic search.

Only facts present in the structured row are stated as fact. Use-case
framing derived from `performance_tier` is hedged as an estimated tier
rather than presented as a benchmark result.
"""

CATEGORY_TEMPLATES = {
    "cpu": (
        "{manufacturer} {model} is a {socket} socket CPU with {cores} cores and "
        "{threads} threads, base clock {base_clock_ghz}GHz boosting to {boost_clock_ghz}GHz, "
        "{tdp_w}W TDP, built on {architecture} ({generation})."
        "{igpu_clause}"
    ),
    "gpu": (
        "{manufacturer} {model} is a graphics card with {vram_gb}GB {vram_type} VRAM, "
        "{tdp_w}W TDP, {pcie_gen} interface, {length_mm}mm long. "
        "Manufacturer-recommended PSU: {recommended_psu_w}W."
    ),
    "motherboard": (
        "{manufacturer} {model} is a {form_factor} motherboard for {socket} socket CPUs "
        "with the {chipset} chipset, supporting {ram_type} RAM up to {max_ram_gb}GB across "
        "{ram_slots} slots, {m2_slots} M.2 slot(s), and {sata_ports} SATA port(s)."
        "{wifi_clause}"
    ),
    "ram": (
        "{manufacturer} {model} is a {capacity_gb}GB {ddr_gen} memory kit ({sticks} sticks) "
        "rated at {speed_mhz}MHz, CL{cas_latency}, {voltage}V."
    ),
    "storage": (
        "{manufacturer} {model} is a {capacity_gb}GB {type} drive ({interface}) rated at "
        "{read_mb_s}MB/s read and {write_mb_s}MB/s write."
    ),
    "psu": (
        "{manufacturer} {model} is a {wattage}W {efficiency_rating} power supply ({atx_version})."
        "{connector_clause}"
    ),
    "cooler": (
        "{manufacturer} {model} is a{aio_clause} CPU cooler supporting sockets: {socket_support}."
        "{height_clause}"
    ),
    "case": (
        "{manufacturer} {model} is a case supporting {form_factor_support} motherboards, "
        "clearing GPUs up to {max_gpu_length_mm}mm long and coolers up to "
        "{max_cooler_height_mm}mm tall."
    ),
}

TIER_BLURBS = {
    "entry": "Positioned as an entry-level / budget-tier part based on its specifications.",
    "mid": "Positioned as a mid-tier part based on its specifications.",
    "high": "Positioned as a high-tier part based on its specifications.",
    "enthusiast": "Positioned as an enthusiast/flagship-tier part based on its specifications.",
}


def build_product_document(product: dict) -> str:
    category = product["category"]
    template = CATEGORY_TEMPLATES[category]

    fields = dict(product)
    if category == "cpu":
        fields["igpu_clause"] = " Includes integrated graphics." if product.get("has_igpu") else " No integrated graphics."
    if category == "motherboard":
        wifi_bits = []
        if product.get("wifi"):
            wifi_bits.append("Wi-Fi")
        if product.get("bluetooth"):
            wifi_bits.append("Bluetooth")
        fields["wifi_clause"] = f" Includes {' and '.join(wifi_bits)}." if wifi_bits else " No built-in Wi-Fi/Bluetooth."
    if category == "psu":
        fields["connector_clause"] = (
            " Supports 12VHPWR/12V-2x6." if product.get("supports_12vhpwr") else ""
        )
    if category == "cooler":
        fields["aio_clause"] = "n AIO liquid" if product.get("type") == "aio" else "n air"
        if product.get("type") == "aio" and product.get("radiator_mm"):
            fields["height_clause"] = f" Radiator size: {product['radiator_mm']}mm."
        elif product.get("height_mm"):
            fields["height_clause"] = f" Height: {product['height_mm']}mm."
        else:
            fields["height_clause"] = ""

    text = template.format(**fields)

    price = f" Estimated price: {product.get('price_type', 'estimated')} ₱{product['price_php']:,.0f} PHP (source: {product['source']})."
    text += price

    tier = product.get("performance_tier")
    if tier and tier in TIER_BLURBS:
        text += " " + TIER_BLURBS[tier]

    return text
