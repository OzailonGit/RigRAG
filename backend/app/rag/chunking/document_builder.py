"""Turns a structured product dict into the natural-language document that
gets embedded for semantic search.

Only facts present in the structured row are stated as fact, and fields
listed in `assumed_fields` are given as minimums ("at least 1 M.2 slot"). Use-case
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
        "{manufacturer} {model} is a{chip_clause} graphics card with {vram_gb}GB {vram_type} VRAM, "
        "{tdp_w}W TDP, {pcie_gen} interface, {length_mm}mm long. "
        "Manufacturer-recommended PSU: {recommended_psu_w}W."
    ),
    "motherboard": (
        "{manufacturer} {model} is a {form_factor} motherboard for {socket} socket CPUs "
        "with the {chipset} chipset, supporting {ram_type} RAM up to {max_ram_gb}GB across "
        "{ram_slots} slots, {m2_clause}{m2_slots} M.2 slot(s), and {sata_clause}{sata_ports} SATA port(s)."
        "{wifi_clause}"
    ),
    "ram": (
        "{manufacturer} {model} is a {capacity_gb}GB {ddr_gen} memory kit ({sticks} sticks) "
        "rated at {speed_mhz}MHz{timing_clause}."
    ),
    "storage": (
        "{manufacturer} {model} is a {capacity_gb}GB {type} drive ({interface}){speed_clause}."
    ),
    "psu": (
        "{manufacturer} {model} is a {wattage}W{efficiency_clause} power supply{atx_clause}."
        "{connector_clause}"
    ),
    "cooler": (
        "{manufacturer} {model} is a{aio_clause} CPU cooler{socket_clause}."
        "{height_clause}"
    ),
    "case": (
        "{manufacturer} {model} is a case supporting {form_factor_support} motherboards{clearance_clause}."
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
    if category == "gpu":
        chip = product.get("chip")
        mention_chip = chip and chip.lower() not in product.get("model", "").lower()
        fields["chip_clause"] = f" {chip}" if mention_chip else ""
    if category == "ram":
        timing = []
        if product.get("cas_latency"):
            timing.append(f"CL{product['cas_latency']}")
        if product.get("voltage"):
            timing.append(f"{product['voltage']}V")
        fields["timing_clause"] = ", " + ", ".join(timing) if timing else ""
    if category == "storage":
        if product.get("read_mb_s") and product.get("write_mb_s"):
            fields["speed_clause"] = f" rated at {product['read_mb_s']}MB/s read and {product['write_mb_s']}MB/s write"
        else:
            fields["speed_clause"] = ""
    if category == "psu":
        fields["efficiency_clause"] = f" {product['efficiency_rating']}" if product.get("efficiency_rating") else ""
        fields["atx_clause"] = f" ({product['atx_version']})" if product.get("atx_version") else ""
    if category == "cpu":
        fields["igpu_clause"] = " Includes integrated graphics." if product.get("has_igpu") else " No integrated graphics."
    if category == "motherboard":
        wifi_bits = []
        if product.get("wifi"):
            wifi_bits.append("Wi-Fi")
        if product.get("bluetooth"):
            wifi_bits.append("Bluetooth")
        assumed = product.get("assumed_fields") or []
        fields["m2_clause"] = "at least " if "m2_slots" in assumed else ""
        fields["sata_clause"] = "at least " if "sata_ports" in assumed else ""
        fields["wifi_clause"] = f" Includes {' and '.join(wifi_bits)}." if wifi_bits else " No built-in Wi-Fi/Bluetooth."
    if category == "psu":
        fields["connector_clause"] = (
            " Supports 12VHPWR/12V-2x6." if product.get("supports_12vhpwr") else ""
        )
    if category == "cooler":
        sockets = product.get("socket_support")
        fields["socket_clause"] = (
            f" supporting sockets: {sockets}" if sockets else "; its supported sockets aren't listed by any source"
        )
        fields["aio_clause"] = "n AIO liquid" if product.get("type") == "aio" else "n air"
        if product.get("type") == "aio" and product.get("radiator_mm"):
            fields["height_clause"] = f" Radiator size: {product['radiator_mm']}mm."
        elif product.get("height_mm"):
            fields["height_clause"] = f" Height: {product['height_mm']}mm."
        else:
            fields["height_clause"] = ""

    if category == "case":
        gpu, cooler = product.get("max_gpu_length_mm"), product.get("max_cooler_height_mm")
        fields["clearance_clause"] = (
            f", clearing GPUs up to {gpu}mm long and coolers up to {cooler}mm tall"
            if gpu is not None and cooler is not None
            else "; its GPU and cooler clearances aren't listed by any source"
        )

    text = template.format(**fields)

    if product.get("price_php") is None:
        text += f" No retailer price yet (specs source: {product['source']})."
    elif product.get("price_type") == "observed":
        text += (
            f" Price: ₱{product['price_php']:,.0f} PHP, the median of current Shopee/Lazada listings"
            f" (specs source: {product['source']})."
        )
    else:
        text += f" Estimated price: {product.get('price_type', 'estimated')} ₱{product['price_php']:,.0f} PHP (source: {product['source']})."

    tier = product.get("performance_tier")
    if tier and tier in TIER_BLURBS:
        text += " " + TIER_BLURBS[tier]

    return text
