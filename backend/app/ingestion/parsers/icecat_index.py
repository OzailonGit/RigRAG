"""Streaming parser for Open Icecat index files (files.index.xml / daily.index.xml).

Each <file> element describes one product:

    <file path="export/freexml.int/EN/112655354.xml" Product_ID="112655354"
          Catid="170" Supplier_id="1" Prod_ID="90YV0JC0-M0NA00"
          Model_Name="Dual -RTX4060-O8G" On_Market="1" Quality="ICECAT" ...>
      <EAN_UPCS><EAN_UPC Value="4711387223734"/></EAN_UPCS>
      <Country_Markets><Country_Market Value="PH"/></Country_Markets>
    </file>

The full index is several GB, so elements are cleared as soon as they are
read. https://iceclog.com/manual-icecat-product-xmls-batch-processing/
"""

import gzip
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree.ElementTree import iterparse


@dataclass
class IndexEntry:
    icecat_id: int
    category_id: int
    supplier_id: int | None
    product_code: str
    model_name: str
    on_market: bool
    quality: str
    gtins: list[str] = field(default_factory=list)
    country_markets: list[str] = field(default_factory=list)


def _open(path: Path):
    return gzip.open(path, "rb") if path.suffix == ".gz" else path.open("rb")


def _int_or_none(value: str | None) -> int | None:
    return int(value) if value and value.isdigit() else None


def iter_index(
    path: Path,
    category_ids: Iterable[int] | None = None,
    country: str | None = None,
    include_removed: bool = False,
) -> Iterator[IndexEntry]:
    """Yields index entries, optionally filtered by Icecat category ID and
    country market (e.g. "PH").

    Entries with Quality="REMOVED" (delisted in a daily index) are skipped
    unless `include_removed` is set. Entries that list no country markets are
    kept when filtering by country, since many datasheets omit market data.
    """
    wanted_categories = set(category_ids) if category_ids is not None else None

    with _open(path) as fh:
        # Parent stack, so each finished <file> is detached from its parent;
        # clearing the element alone would leave millions of empty children.
        stack = []
        for event, elem in iterparse(fh, events=("start", "end")):
            if event == "start":
                stack.append(elem)
                continue
            stack.pop()
            if elem.tag != "file":
                continue
            try:
                category_id = _int_or_none(elem.get("Catid"))
                if wanted_categories is not None and category_id not in wanted_categories:
                    continue

                quality = elem.get("Quality", "")
                if quality == "REMOVED" and not include_removed:
                    continue

                markets = [m.get("Value") for m in elem.iter("Country_Market") if m.get("Value")]
                if country and markets and country not in markets:
                    continue

                icecat_id = _int_or_none(elem.get("Product_ID"))
                if icecat_id is None or category_id is None:
                    continue

                yield IndexEntry(
                    icecat_id=icecat_id,
                    category_id=category_id,
                    supplier_id=_int_or_none(elem.get("Supplier_id")),
                    product_code=elem.get("Prod_ID", ""),
                    model_name=elem.get("Model_Name", ""),
                    on_market=elem.get("On_Market") == "1",
                    quality=quality,
                    gtins=[e.get("Value") for e in elem.iter("EAN_UPC") if e.get("Value")],
                    country_markets=markets,
                )
            finally:
                elem.clear()
                if stack:
                    stack[-1].remove(elem)
