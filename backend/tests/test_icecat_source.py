"""Icecat Live API response handling, on-disk caching, and index parsing."""

import gzip
import json

import httpx

from app.ingestion.parsers.icecat_index import iter_index
from app.ingestion.sources.icecat import IcecatClient, LookupStatus, classify_response

INDEX_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<ICECAT-interface>
  <files.index Generated="20261019">
    <file path="export/freexml.int/EN/100.xml" Product_ID="100" Catid="170" Supplier_id="1"
          Prod_ID="GPU-1" Model_Name="Card One" On_Market="1" Quality="ICECAT">
      <EAN_UPCS><EAN_UPC Value="4711387223734"/></EAN_UPCS>
      <Country_Markets><Country_Market Value="PH"/><Country_Market Value="SG"/></Country_Markets>
    </file>
    <file path="export/freexml.int/EN/101.xml" Product_ID="101" Catid="170" Supplier_id="1"
          Prod_ID="GPU-2" Model_Name="Card Two" On_Market="1" Quality="ICECAT">
      <Country_Markets><Country_Market Value="DE"/></Country_Markets>
    </file>
    <file path="export/freexml.int/EN/102.xml" Product_ID="102" Catid="151" Supplier_id="1"
          Prod_ID="LAPTOP" Model_Name="Laptop" On_Market="1" Quality="ICECAT"/>
    <file path="export/freexml.int/EN/103.xml" Product_ID="103" Catid="170" Supplier_id="2"
          Prod_ID="GPU-3" Model_Name="Card Three" On_Market="0" Quality="REMOVED"/>
    <file path="export/freexml.int/EN/104.xml" Product_ID="104" Catid="164" Supplier_id="2"
          Prod_ID="MB-1" Model_Name="Board" On_Market="1" Quality="SUPPLIER"/>
  </files.index>
</ICECAT-interface>
"""


def test_classify_success_restricted_and_missing():
    ok = classify_response(200, {"msg": "OK", "data": {"GeneralInfo": {"IcecatId": 1}}})
    assert ok.status is LookupStatus.ok

    restricted = classify_response(403, {"StatusCode": 9, "Message": "To access Full Icecat content, an app_key is required."})
    assert restricted.status is LookupStatus.restricted

    missing = classify_response(404, {"StatusCode": 17, "Code": 404, "Message": "Missing information about product"})
    assert missing.status is LookupStatus.not_found


def test_get_product_caches_by_icecat_id(tmp_path):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url)
        return httpx.Response(200, json={"msg": "OK", "data": {"GeneralInfo": {"IcecatId": 555, "Title": "Card"}}})

    client = IcecatClient(username="demo", password="", cache_dir=tmp_path, request_interval_s=0,
                          http=httpx.Client(transport=httpx.MockTransport(handler)))

    first = client.get_product(gtin="4711387223734")
    assert first.status is LookupStatus.ok
    assert calls[0].params["GTIN"] == "4711387223734"
    assert json.loads((tmp_path / "products" / "555.json").read_text())["GeneralInfo"]["Title"] == "Card"

    cached = client.get_product(icecat_id=555)
    assert cached.status is LookupStatus.ok
    assert len(calls) == 1


def test_index_filters_by_category_country_and_removed(tmp_path):
    path = tmp_path / "files.index.xml"
    path.write_bytes(INDEX_XML)

    gpus = list(iter_index(path, category_ids=[170]))
    assert [e.icecat_id for e in gpus] == [100, 101]
    assert gpus[0].gtins == ["4711387223734"]
    assert gpus[0].country_markets == ["PH", "SG"]

    ph = list(iter_index(path, category_ids=[170, 164], country="PH"))
    # 101 is DE-only; 104 lists no markets, so it is kept.
    assert [e.icecat_id for e in ph] == [100, 104]

    with_removed = list(iter_index(path, category_ids=[170], include_removed=True))
    assert 103 in [e.icecat_id for e in with_removed]


def test_index_reads_gzip(tmp_path):
    path = tmp_path / "files.index.xml.gz"
    path.write_bytes(gzip.compress(INDEX_XML))
    assert len(list(iter_index(path))) == 4
