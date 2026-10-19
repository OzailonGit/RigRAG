"""Open Icecat client: single-product lookups via the Live JSON API and
index-file downloads for enumerating whole categories.

Open Icecat only serves brands that sponsor it (ASUS, MSI, Gigabyte,
Kingston, Samsung, be quiet!, Seagate, among others). Products from
non-sponsoring brands (AMD, Intel, Corsair, Noctua, ...) come back as
`LookupStatus.restricted`; they need paid Full Icecat access.

Fair-use terms require crediting "Specs Icecat" wherever the data is shown
and prohibit using it to train AI models.
https://iceclog.com/manual-for-icecat-json-product-requests/
"""

import enum
import json
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from app.config import get_settings

LIVE_API_URL = "https://live.icecat.biz/api"
INDEX_URL = "https://data.icecat.biz/export/freexml/EN/files.index.xml"
REPO_ROOT = Path(__file__).resolve().parents[4]

# StatusCode values observed in Live API error bodies.
_STATUS_RESTRICTED = 9


class LookupStatus(str, enum.Enum):
    ok = "ok"
    not_found = "not_found"
    restricted = "restricted"
    error = "error"


@dataclass
class LookupResult:
    status: LookupStatus
    data: dict | None = None
    message: str = ""


def classify_response(status_code: int, body: dict) -> LookupResult:
    """Maps a Live API response onto a LookupResult.

    Success bodies are `{"msg": "OK", "data": {...}}`. Error bodies are
    `{"StatusCode": n, "Message": "..."}`, with StatusCode 9 meaning the brand
    is only available through paid Full Icecat.
    """
    if body.get("msg") == "OK" and body.get("data"):
        return LookupResult(LookupStatus.ok, body["data"])

    message = body.get("Message") or body.get("msg") or f"HTTP {status_code}"
    if body.get("StatusCode") == _STATUS_RESTRICTED or "Full Icecat" in message:
        return LookupResult(LookupStatus.restricted, message=message)
    if status_code == 404 or body.get("Code") == 404 or "not present" in message:
        return LookupResult(LookupStatus.not_found, message=message)
    return LookupResult(LookupStatus.error, message=message)


class IcecatClient:
    def __init__(
        self,
        username: str | None = None,
        password: str | None = None,
        cache_dir: Path | None = None,
        request_interval_s: float | None = None,
        http: httpx.Client | None = None,
    ):
        settings = get_settings()
        self.username = username or settings.icecat_username
        self.password = password if password is not None else settings.icecat_password
        self.cache_dir = cache_dir or Path(settings.icecat_cache_dir or REPO_ROOT / "data" / "raw" / "icecat")
        self.request_interval_s = settings.icecat_request_interval_s if request_interval_s is None else request_interval_s
        self.http = http or httpx.Client(timeout=30)
        self._last_request_at = 0.0

    def _product_cache_path(self, icecat_id: int) -> Path:
        return self.cache_dir / "products" / f"{icecat_id}.json"

    def _throttle(self) -> None:
        wait = self._last_request_at + self.request_interval_s - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_request_at = time.monotonic()

    def get_product(
        self,
        *,
        icecat_id: int | None = None,
        gtin: str | None = None,
        brand: str | None = None,
        product_code: str | None = None,
        use_cache: bool = True,
    ) -> LookupResult:
        """Fetches one product datasheet. Exactly one identifier form is required:
        `icecat_id`, `gtin`, or `brand` + `product_code`.

        Successful responses are cached on disk by Icecat ID, so re-running an
        import does not refetch unchanged products.
        """
        if icecat_id is not None:
            params = {"icecat_id": str(icecat_id)}
            cached = self._product_cache_path(icecat_id)
            if use_cache and cached.exists():
                return LookupResult(LookupStatus.ok, json.loads(cached.read_text(encoding="utf-8")))
        elif gtin:
            params = {"GTIN": gtin}
        elif brand and product_code:
            params = {"Brand": brand, "ProductCode": product_code}
        else:
            raise ValueError("Provide icecat_id, gtin, or brand + product_code.")

        self._throttle()
        try:
            response = self.http.get(LIVE_API_URL, params={"UserName": self.username, "Language": "en", **params})
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            return LookupResult(LookupStatus.error, message=str(exc))

        result = classify_response(response.status_code, body)
        if result.status is LookupStatus.ok:
            resolved_id = int(result.data["GeneralInfo"]["IcecatId"])
            path = self._product_cache_path(resolved_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(result.data), encoding="utf-8")
        return result

    def download_index(self, url: str = INDEX_URL, force: bool = False) -> Path:
        """Streams an index file to the cache directory and returns its path.

        Index files can be several GB, so they are written in chunks rather
        than loaded into memory. Requires a registered account; the demo
        account gets HTTP 401.
        """
        if not self.password:
            raise RuntimeError("Index downloads need ICECAT_USERNAME and ICECAT_PASSWORD for a registered Open Icecat account.")

        target = self.cache_dir / "index" / url.rsplit("/", 1)[-1]
        if target.exists() and not force:
            return target

        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_suffix(target.suffix + ".part")
        with self.http.stream("GET", url, auth=(self.username, self.password), timeout=None) as response:
            response.raise_for_status()
            with partial.open("wb") as fh:
                for chunk in response.iter_bytes(chunk_size=1 << 20):
                    fh.write(chunk)
        partial.replace(target)
        return target
