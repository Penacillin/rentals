from __future__ import annotations

import argparse
import json
import re
from contextlib import contextmanager
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen

from invisible_playwright import InvisiblePlaywright

import db

ROOT = Path(__file__).parent
CONFIG = json.loads((ROOT / "config.json").read_text())
NUMBER = r"(-?\d+(?:\.\d+)?)"
class CaptchaRequired(RuntimeError):
    pass





def number(value: object) -> float | None:
    if value is None:
        return None
    match = re.search(NUMBER, str(value).replace(",", ""))
    return float(match.group(1)) if match else None


def integer(value: object) -> int | None:
    result = number(value)
    return int(result) if result is not None else None
BUILT_YEAR_RE = re.compile(
    r"(?:built(?:\s+in)?\s*:?\s*((?:18|19|20)\d{2})|((?:18|19|20)\d{2})\s+built)",
    re.I,
)
FEATURE_PATTERNS = {
    "centralAir": r"\bcentral\s+air\b",
    "dishwasher": r"\bdishwasher\b",
    "washerDryer": r"\bwasher\s*/\s*dryer\b|\bwasher\s+and\s+dryer\b",
    "doorman": r"\bdoorman\b",
    "elevator": r"\belevator\b",
}
STREET_API_CAPTURE_SCRIPT = """(() => {
  const calls = [];
  window.__streeteasyApiRequests = calls;
  const fetch = window.fetch;
  window.fetch = function(input, init) {
    const url = typeof input === "string" ? input : input?.url;
    if (url?.includes("api-v6.streeteasy.com")) {
      calls.push({url, body: init?.body == null ? null : String(init.body)});
    }
    return fetch.apply(this, arguments);
  };
})();"""


def built_year(text: str) -> int | None:
    match = BUILT_YEAR_RE.search(text)
    return int(match.group(1) or match.group(2)) if match else None


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def detail_metadata(html: str) -> tuple[int | None, dict[str, bool], int | None, str | None]:
    parser = _Text()
    parser.feed(html)
    text = " ".join(parser.parts)
    sqft = re.search(r"([\d,]+)\s*(?:ft²|sq\s?ft|square feet)\b", text, re.I)
    history = text.split("Property history", 1)[-1]
    listed = re.search(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b", history)
    return built_year(text), {
        name: bool(re.search(pattern, text, re.I))
        for name, pattern in FEATURE_PATTERNS.items()
    }, integer(sqft.group(1)) if sqft else None, listed.group(0) if listed else None


def street_detail_metadata(url: str) -> tuple[int | None, dict[str, bool], int | None, str | None]:
    request = Request(url, headers={"User-Agent": "rentals/0.1"})
    with urlopen(request, timeout=30) as response:
        return detail_metadata(response.read().decode("utf-8", errors="replace"))


def street_detail_metadata_page(page, url: str) -> tuple[int | None, dict[str, bool], int | None, str | None] | None:


    html = page.evaluate(
        f"""async () => {{
          const response = await fetch({json.dumps(url)}, {{credentials: "include"}});
          return await response.text();
        }}"""
    )
    if (
        "Access to this page has been denied" in html
        or "Press & Hold to confirm you are" in html
        or "px-captcha" in html
    ):
        raise CaptchaRequired(f"StreetEasy CAPTCHA at {url}")
    if not html:
        print(f"StreetEasy detail text unavailable: {url}")
        return None
    return detail_metadata(html)


LISTING_DATE_KEYS = {
    "listedat", "listeddate", "datelisted", "dateposted", "datepostedstring",
    "timeonzillow", "daysonzillow", "listingdate", "dateonmarket", "dateadded", "daysonmarket",
}
def listed_at(value: object) -> str | None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key.casefold() in LISTING_DATE_KEYS and isinstance(child, (str, int, float)):
                return str(child)
        for child in value.values():
            result = listed_at(child)
            if result:
                return result
    elif isinstance(value, list):
        for child in value:
            result = listed_at(child)
            if result:
                return result
    return None


def card_listed_at(text: str) -> str | None:
    match = re.search(
        r"\b(?:listed|posted)\s*:?\s*(today|yesterday|\d+\s+(?:hours?|days?|weeks?|months?)\s+ago|\d{1,2}/\d{1,2}/\d{2,4})\b",
        text,
        re.I,
    )
    return match.group(1) if match else None



def matches_search(row: db.Listing) -> bool:
    if row.price is not None and not CONFIG["min_price"] <= row.price <= CONFIG["max_price"]:
        return False
    if row.beds is not None and row.beds not in CONFIG["beds"]:
        return False
    bounds = CONFIG.get(row.source, {}).get("map_bounds")
    if bounds:
        raw = row.raw if isinstance(row.raw, dict) else {}
        point = raw.get("latLong") or raw.get("geoPoint") or {}
        lat, lon = number(point.get("latitude")), number(point.get("longitude"))
        if lat is not None and lon is not None and not (
            bounds["south"] <= lat <= bounds["north"] and bounds["west"] <= lon <= bounds["east"]
        ):
            return False
    return True


@contextmanager
def browser_context(headless: bool):
    with InvisiblePlaywright(
        headless=headless,
        profile_dir=ROOT / "data" / "invisible-profile",
        seed=360,
        locale="en-US",
        timezone="America/New_York",
    ) as context:
        yield context
def goto_with_retries(page, url: str, label: str, attempts: int = 3) -> bool:
    for attempt in range(1, attempts + 1):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            return True
        except Exception as exc:
            print(f"{label} navigation error {attempt}/{attempts}: {exc}")
            if attempt < attempts:
                page.wait_for_timeout(2000)
    return False




def card_values(text: str) -> dict:
    price = re.search(r"\$\s?([\d,]+)", text)
    beds = re.search(r"(\d+(?:\.\d+)?)\s*(?:bd|bed)s?\b", text, re.I)
    baths = re.search(r"(\d+(?:\.\d+)?)\s*(?:ba|bath)s?\b", text, re.I)
    sqft = re.search(r"([\d,]+)\s*(?:ft²|sq\s?ft|square feet)\b", text, re.I)
    return {
        "price": integer(price.group(1)) if price else None,
        "beds": number(beds.group(1)) if beds else None,
        "baths": number(baths.group(1)) if baths else None,
        "sqft": integer(sqft.group(1)) if sqft else None,
        "listed_at": card_listed_at(text),
    }


class _Cards(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.articles: list[dict] = []
        self._article: dict | None = None
        self._depth = 0
        self._anchor: dict | None = None
        self.anchors: list[dict] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "article" and self._article is None:
            self._article = {"text": [], "links": []}
            self._depth = 1
        elif self._article is not None:
            self._depth += 1
        href = attributes.get("href")
        if tag == "a" and href and href.startswith("/building/"):
            self._anchor = {"href": href, "text": []}
            self.anchors.append(self._anchor)
            if self._article is not None:
                self._article["links"].append(self._anchor)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            self._anchor = None
        if self._article is not None:
            self._depth -= 1
            if tag == "article" and self._depth == 0:
                self._article["text"] = " ".join(self._article["text"])
                self.articles.append(self._article)
                self._article = None

    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if not text:
            return
        if self._article is not None:
            self._article["text"].append(text)
        if self._anchor is not None:
            self._anchor["text"].append(text)


def _listing_from_card(card: dict, base_url: str = "https://streeteasy.com") -> db.Listing | None:
    links = card.get("links") or []
    link = links[0] if links else card.get("link")
    href = link.get("href") if isinstance(link, dict) else link
    if not href:
        return None
    href = href.split("#", 1)[0]
    url = urljoin(base_url, href)
    text = card.get("text") or ""
    values = card_values(text)
    address = " ".join(link.get("text", [])) if isinstance(link, dict) else card.get("address")
    address = address.strip() if isinstance(address, str) else None
    return db.Listing(
        source="streeteasy",
        source_id=urlsplit(url).path,
        address=address,
        unit=db.unit_from_address(address),
        price=values["price"],
        beds=values["beds"],
        baths=values["baths"],
        sqft=values["sqft"],
        listed_at=values["listed_at"],
        url=url,
        status="search-card",
        raw=card,
    )


def parse_streeteasy_html(html: str) -> list[db.Listing]:
    parser = _Cards()
    parser.feed(html)
    cards = parser.articles
    if not cards:
        cards = [{"text": " ".join(anchor["text"]), "links": [anchor]} for anchor in parser.anchors]
    return [row for card in cards if (row := _listing_from_card(card))]


def parse_streeteasy_api(payload: dict) -> list[db.Listing]:
    search = (payload.get("data") or {}).get("searchRentals") or {}
    rows = []
    for edge in search.get("edges") or []:
        node = edge.get("node") or {}
        if not node.get("id") or not node.get("street"):
            continue
        unit = node.get("unit")
        address = f"{node['street']} #{unit}" if unit else node["street"]
        full = node.get("fullBathroomCount")
        half = node.get("halfBathroomCount")
        baths = (
            (full or 0) + (half or 0) / 2
            if full is not None or half is not None
            else None
        )
        rows.append(db.Listing(
            source="streeteasy",
            source_id=str(node["id"]),
            address=address,
            unit=unit,
            price=integer(node.get("price") or node.get("totalMonthlyPrice")),
            beds=number(node.get("bedroomCount")),
            baths=baths,
            listed_at=listed_at(node),
            url=urljoin("https://streeteasy.com", node.get("urlPath") or ""),
            status=node.get("status") or "api-search",
            raw=node,
        ))
    return rows
def _request_payload(response) -> dict | None:
    request = response.request
    try:
        payload = request.post_data_json
        return payload if isinstance(payload, dict) else None
    except (AttributeError, TypeError, json.JSONDecodeError):
        try:
            raw = request.post_data
            if callable(raw):
                raw = raw()
            payload = json.loads(raw) if isinstance(raw, str) else raw
            return payload if isinstance(payload, dict) else None
        except (AttributeError, TypeError, json.JSONDecodeError):
            return None
def _captured_api_payload(page) -> tuple[str, dict] | None:
    for call in page.evaluate("window.__streeteasyApiRequests || []"):
        try:
            payload = json.loads(call.get("body") or "")
        except (AttributeError, TypeError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            return call["url"], payload
    return None


def _page_payload(payload: object, page_number: int) -> object:
    if isinstance(payload, dict):
        return {
            key: page_number if key == "page" else _page_payload(value, page_number)
            for key, value in payload.items()
        }
    if isinstance(payload, list):
        return [_page_payload(value, page_number) for value in payload]
    return payload


def streeteasy_api_page(page, url: str, payload: dict) -> dict:
    return page.evaluate(
        f"""async () => {{
          const response = await fetch({json.dumps(url)}, {{
            method: "POST",
            headers: {{"content-type": "application/json"}},
            credentials: "include",
            body: JSON.stringify({json.dumps(payload)})
          }});
          return await response.json();
        }}"""
    )


def _zillow_items(value: object) -> list[dict]:
    if isinstance(value, dict):
        canonical = value.get("props", {}).get("pageProps", {}).get("searchPageState", {})
        cat1 = canonical.get("cat1", {}) if isinstance(canonical, dict) else {}
        results = cat1.get("searchResults", {}).get("listResults", []) if isinstance(cat1, dict) else []
        if isinstance(results, list) and any(isinstance(item, dict) and item.get("zpid") for item in results):
            return [item for item in results if isinstance(item, dict) and item.get("zpid")]
        for child in value.values():
            found = _zillow_items(child)
            if found:
                return found
    elif isinstance(value, list):
        if any(isinstance(item, dict) and item.get("zpid") for item in value):
            return [item for item in value if isinstance(item, dict) and item.get("zpid")]
        for child in value:
            found = _zillow_items(child)
            if found:
                return found
    return []


class _NextData(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_next = False
        self.text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script" and dict(attrs).get("id") == "__NEXT_DATA__":
            self.in_next = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self.in_next:
            self.in_next = False

    def handle_data(self, data: str) -> None:
        if self.in_next:
            self.text.append(data)


def parse_zillow_html(html: str) -> list[dict]:
    parser = _NextData()
    parser.feed(html)
    if not parser.text:
        return []
    return _zillow_items(json.loads("".join(parser.text)))


def zillow_listing(item: dict, unit: dict | None = None, index: int = 0) -> db.Listing:
    values = unit or item
    address = item.get("address") or item.get("streetAddress")
    url = item.get("detailUrl") or item.get("hdpUrl")
    return db.Listing(
        source="zillow",
        source_id=f"{item['zpid']}:{index}" if unit is not None else str(item["zpid"]),
        address=address,
        unit=values.get("unit") or db.unit_from_address(item.get("addressStreet")),
        price=integer(values.get("price")),
        beds=number(values.get("beds")),
        baths=number(values.get("baths")),
        sqft=integer(values.get("area")),
        listed_at=listed_at(item),
        url=urljoin("https://www.zillow.com", url) if url else None,
        status="search-list",
        raw=item,
    )


def zillow_listings(item: dict) -> list[db.Listing]:
    units = item.get("units")
    if isinstance(units, list) and units:
        return [zillow_listing(item, unit, index) for index, unit in enumerate(units)]
    return [zillow_listing(item)]


def save(rows: list[db.Listing], source: str) -> int:
    with db.connect() as conn:
        for row in rows:
            db.upsert_listing(conn, row)
        conn.commit()
    return len(rows)


def saved_files(source: str, directory: str, limit: int) -> int:
    rows: list[db.Listing] = []
    for path in sorted(Path(directory).glob("*.html")):
        parsed = parse_streeteasy_html(path.read_text()) if source == "streeteasy" else [
            row for item in parse_zillow_html(path.read_text()) for row in zillow_listings(item)
        ]
        rows.extend(parsed)
        if len(rows) >= limit:
            break
    return save(rows[:limit], source)


def captcha(page) -> bool:
    title = page.title()
    body = page.evaluate("document.body ? document.body.innerText : ''")
    return (
        "Access to this page has been denied" in title
        or "Access to this page has been denied" in body
        or "Press & Hold to confirm you are" in body
        or page.locator("px-captcha").count() > 0
    )




def enrich_streeteasy_years(rows: list[db.Listing], page=None, on_enriched=None) -> dict[str, int]:
    metadata: dict[str, tuple[int | None, dict[str, bool], int | None, str | None] | None] = {}
    years: dict[str, int] = {}
    for row in rows:
        if not row.url:
            continue
        detail_url = urljoin("https://streeteasy.com", row.url)
        if detail_url not in metadata:
            metadata[detail_url] = None
            attempt = 1
            while True:
                try:
                    if page:
                        page.wait_for_timeout(2000)
                    result = (
                        street_detail_metadata_page(page, detail_url)
                        if page
                        else street_detail_metadata(detail_url)
                    )
                    if result is not None:
                        metadata[detail_url] = result
                        print(f"StreetEasy detail {len(metadata)}: {detail_url}")
                    else:
                        print(f"StreetEasy detail unavailable {detail_url}; keeping metadata unchanged")
                    break
                except CaptchaRequired:
                    input("Solve CAPTCHA in browser window, then press Enter")
                except Exception as exc:
                    print(f"StreetEasy detail error {detail_url} attempt {attempt}: {exc}")
                    break
                finally:
                    attempt += 1
        if result:
            year, features, sqft, listed_at = result
            row.features = features
            if sqft is not None:
                row.sqft = sqft
            if listed_at is not None:
                row.listed_at = listed_at
            if year and row.building_bbl:
                years[row.building_bbl] = year
            if on_enriched:
                on_enriched(row, year, features)
    return years


def enrich_saved_years(limit: int) -> int:
    db.init()
    with db.connect() as conn:
        source_rows = conn.execute(
            """SELECT * FROM listings
               WHERE source = 'streeteasy'
                 AND (sqft IS NULL OR listed_at IS NULL OR central_air IS NULL
                      OR dishwasher IS NULL OR washer_dryer IS NULL
                      OR doorman IS NULL OR elevator IS NULL)
               ORDER BY source_id LIMIT ?""",
            (limit,),
        ).fetchall()
        feature_columns = {
            "centralAir": "central_air",
            "dishwasher": "dishwasher",
            "washerDryer": "washer_dryer",
            "doorman": "doorman",
            "elevator": "elevator",
        }
        rows = []
        for source_row in source_rows:
            features = {
                key: bool(source_row[column])
                for key, column in feature_columns.items()
            } if all(source_row[column] is not None for column in feature_columns.values()) else None
            if features is not None and source_row["sqft"] is not None:
                continue
            rows.append(
                db.Listing(
                    source=source_row["source"],
                    source_id=source_row["source_id"],
                    address=source_row["address"],
                    unit=source_row["unit"],
                    building_bbl=source_row["building_bbl"],
                    price=source_row["price"],
                    beds=source_row["beds"],
                    baths=source_row["baths"],
                    sqft=source_row["sqft"],
                    url=source_row["url"],
                    status=source_row["status"],
                    listed_at=source_row["listed_at"],
                    features=features,
                    raw=json.loads(source_row["raw"] or "null"),
                )
            )

        def persist(row, year, features):
            db.upsert_features(conn, row.source, row.source_id, features)
            conn.execute(
                """UPDATE listings
                   SET sqft = COALESCE(?, sqft),
                       listed_at = COALESCE(?, listed_at)
                   WHERE source = ? AND source_id = ?""",
                (row.sqft, row.listed_at, row.source, row.source_id),
            )
            if row.building_bbl and year:
                conn.execute(
                    "UPDATE buildings SET year_built = ? WHERE bbl = ?",
                    (year, row.building_bbl),
                )
            conn.commit()

        with browser_context(False) as context:
            page = context.new_page()
            page.goto("https://streeteasy.com/", wait_until="commit", timeout=15000)
            while captcha(page):
                input("Solve CAPTCHA in browser window, then press Enter")
            years = enrich_streeteasy_years(rows, page, persist)
    return len(years)




def scrape_streeteasy(limit: int, headless: bool) -> int:
    config = CONFIG["streeteasy"]
    rows: list[db.Listing] = []
    seen: set[str] = set()
    with browser_context(headless) as context:
        page = context.new_page()
        page.add_init_script(STREET_API_CAPTURE_SCRIPT)
        api_rows: list[db.Listing] = []
        api_responses = []

        def collect_api(response) -> None:
            if "api-v6.streeteasy.com" in response.url and response.request.method == "POST":
                api_responses.append(response)

        page.on("response", collect_api)
        api_endpoint = None
        api_payload = None
        for page_number in range(1, 51):
            if page_number > 1:
                page.wait_for_timeout(2000)
            api_rows.clear()
            api_responses.clear()
            if api_endpoint and api_payload and page_number > 1:
                try:
                    payload = _page_payload(api_payload, page_number)
                    if not isinstance(payload, dict):
                        raise TypeError("StreetEasy API payload must be an object")
                    api_rows.extend(parse_streeteasy_api(streeteasy_api_page(page, api_endpoint, payload)))
                    print(f"StreetEasy API page {page_number}: {len(api_rows)} rows")
                except Exception as exc:
                    print(f"StreetEasy API page {page_number} error: {exc}")
                    break
            else:
                url = config["search_url"] if page_number == 1 else f"{config['search_url']}&{config['page_param']}={page_number}"
                if not goto_with_retries(page, url, f"StreetEasy page {page_number}"):
                    continue
                page.wait_for_timeout(2000)
                for _ in range(3):
                    if not captcha(page):
                        break
                    input("Solve captcha in browser window, then press Enter")
                    page.wait_for_timeout(2000)
                if captcha(page):
                    raise RuntimeError("StreetEasy captcha unresolved; use --from-files DIR")
                if api_payload is None:
                    captured = _captured_api_payload(page)
                    if captured:
                        api_endpoint, api_payload = captured
                        print(f"StreetEasy API request captured for pagination: {api_endpoint}")
                for response in api_responses:
                    if api_payload is None:
                        candidate = _request_payload(response)
                        if candidate:
                            api_payload = candidate
                            api_endpoint = response.url
                            print(f"StreetEasy API request captured for pagination: {api_endpoint}")
                    try:
                        api_rows.extend(parse_streeteasy_api(response.json()))
                    except Exception as exc:
                        print(f"StreetEasy API response parse error: {exc}")
            cards = api_rows
            if not cards:
                break
            page_rows = []
            for row in cards:
                if row.source_id not in seen:
                    seen.add(row.source_id)
                    page_rows.append(row)
                    if matches_search(row):
                        rows.append(row)
                        if len(rows) >= limit:
                            return save(rows[:limit], "streeteasy")
            print(f"StreetEasy page {page_number}: {len(page_rows)} new, {len(rows)} accepted")
            if not page_rows:
                break
    return save(rows[:limit], "streeteasy")


def zillow_url() -> str:
    # ponytail: stable URL plus native newest sort; query-state URLs trigger PerimeterX.
    return CONFIG["zillow"]["search_url"]

def scrape_zillow(limit: int, headless: bool) -> int:
    rows: list[db.Listing] = []
    with browser_context(headless) as context:
        page = context.new_page()
        url = zillow_url()
        for page_number in range(50):
            if page_number:
                page.wait_for_timeout(2000)
            if not goto_with_retries(page, url, f"Zillow page {page_number + 1}"):
                continue
            for _ in range(3):
                if not captcha(page):
                    break
                input("Solve captcha in browser window, then press Enter")
                page.wait_for_timeout(2000)
            if captcha(page):
                raise RuntimeError("Zillow captcha unresolved; use --from-files DIR")
            try:
                data = page.locator("#__NEXT_DATA__").inner_text()
                payload = json.loads(data)
            except Exception as exc:
                print(f"Zillow page {page_number + 1} parse error: {exc}")
                continue
            items = _zillow_items(payload)
            if not items:
                break
            before = len(rows)
            seen = {row.source_id for row in rows}
            for item in items:
                for row in zillow_listings(item):
                    if row.source_id not in seen and matches_search(row):
                        rows.append(row)
                        seen.add(row.source_id)
                    if len(rows) >= limit:
                        return save(rows[:limit], "zillow")
            next_url = payload.get("props", {}).get("pageProps", {}).get("searchPageState", {}).get("cat1", {}).get("searchList", {}).get("pagination", {}).get("nextUrl")
            if not next_url or len(rows) == before:
                break
            url = urljoin("https://www.zillow.com", next_url)
    return save(rows[:limit], "zillow")


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="source", required=True)
    for source in ("streeteasy", "zillow"):
        command = sub.add_parser(source)
        command.add_argument("--limit", type=int, default=100)
        command.add_argument("--from-files")
        command.add_argument("--headless", action="store_true")
        command.set_defaults(source=source)
    detail = sub.add_parser("enrich-years")
    detail.add_argument("--limit", type=int, default=1000)
    args = parser.parse_args()
    db.init()
    if args.source == "enrich-years":
        count = enrich_saved_years(args.limit)
        print(f"enriched {count} StreetEasy building years")
        return
    if args.from_files:
        count = saved_files(args.source, args.from_files, args.limit)
    elif args.source == "streeteasy":
        count = scrape_streeteasy(args.limit, args.headless)
    else:
        count = scrape_zillow(args.limit, args.headless)
    print(f"saved {count} {args.source} listings")


if __name__ == "__main__":
    main()
