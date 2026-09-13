from __future__ import annotations

import argparse
import json
import re
from contextlib import contextmanager
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from invisible_playwright import InvisiblePlaywright

import db

ROOT = Path(__file__).parent
CONFIG = json.loads((ROOT / "config.json").read_text())
NUMBER = r"(-?\d+(?:\.\d+)?)"



def number(value: object) -> float | None:
    if value is None:
        return None
    match = re.search(NUMBER, str(value).replace(",", ""))
    return float(match.group(1)) if match else None


def integer(value: object) -> int | None:
    result = number(value)
    return int(result) if result is not None else None


def matches_search(row: dict) -> bool:
    if row["price"] is not None and not CONFIG["min_price"] <= row["price"] <= CONFIG["max_price"]:
        return False
    if row["beds"] is not None and row["beds"] not in CONFIG["beds"]:
        return False
    if row["source"] == "zillow":
        point = (row.get("raw") or {}).get("latLong") or {}
        lat, lon = number(point.get("latitude")), number(point.get("longitude"))
        bounds = CONFIG["zillow"]["map_bounds"]
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
        attrs = dict(attrs)
        if tag == "article" and self._article is None:
            self._article = {"text": [], "links": []}
            self._depth = 1
        elif self._article is not None:
            self._depth += 1
        if tag == "a" and attrs.get("href", "").startswith("/building/"):
            self._anchor = {"href": attrs["href"], "text": []}
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


def _listing_from_card(card: dict, base_url: str = "https://streeteasy.com") -> dict | None:
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
    address = address.strip() or None
    return {
        "source": "streeteasy",
        "source_id": urlsplit(url).path,
        "address": address,
        "unit": db.unit_from_address(address),
        "price": values["price"],
        "beds": values["beds"],
        "baths": values["baths"],
        "sqft": values["sqft"],
        "url": url,
        "status": "search-card",
        "raw": card,
    }


def parse_streeteasy_html(html: str) -> list[dict]:
    parser = _Cards()
    parser.feed(html)
    cards = parser.articles
    if not cards:
        cards = [{"text": " ".join(anchor["text"]), "links": [anchor]} for anchor in parser.anchors]
    return [row for card in cards if (row := _listing_from_card(card))]


def parse_streeteasy_api(payload: dict) -> list[dict]:
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
        rows.append({
            "source": "streeteasy",
            "source_id": str(node["id"]),
            "address": address,
            "unit": unit,
            "price": integer(node.get("price") or node.get("totalMonthlyPrice")),
            "beds": number(node.get("bedroomCount")),
            "baths": baths,
            "sqft": None,
            "url": urljoin("https://streeteasy.com", node.get("urlPath") or ""),
            "status": node.get("status") or "api-search",
            "raw": node,
        })
    return rows


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


def zillow_listing(item: dict, unit: dict | None = None, index: int = 0) -> dict:
    values = unit or item
    address = item.get("address") or item.get("streetAddress")
    url = item.get("detailUrl") or item.get("hdpUrl")
    return {
        "source": "zillow",
        "source_id": f"{item['zpid']}:{index}" if unit is not None else str(item["zpid"]),
        "address": address,
        "unit": values.get("unit") or db.unit_from_address(item.get("addressStreet")),
        "price": integer(values.get("price")),
        "beds": number(values.get("beds")),
        "baths": number(values.get("baths")),
        "sqft": integer(values.get("area")),
        "url": urljoin("https://www.zillow.com", url) if url else None,
        "status": "search-list",
        "raw": item,
    }


def zillow_listings(item: dict) -> list[dict]:
    units = item.get("units")
    if isinstance(units, list) and units:
        return [zillow_listing(item, unit, index) for index, unit in enumerate(units)]
    return [zillow_listing(item)]


def save(rows: list[dict]) -> int:
    with db.connect() as conn:
        for row in rows:
            db.upsert_listing(conn, row)
        conn.commit()
    return len(rows)


def saved_files(source: str, directory: str, limit: int) -> int:
    rows: list[dict] = []
    for path in sorted(Path(directory).glob("*.html")):
        parsed = parse_streeteasy_html(path.read_text()) if source == "streeteasy" else [
            row for item in parse_zillow_html(path.read_text()) for row in zillow_listings(item)
        ]
        rows.extend(parsed)
        if len(rows) >= limit:
            break
    return save(rows[:limit])


def captcha(page) -> bool:
    title = page.title()
    body = page.locator("body").inner_text(timeout=10000)
    return (
        "Access to this page has been denied" in title
        or "Access to this page has been denied" in body
        or "Press & Hold to confirm you are" in body
        or page.locator("px-captcha").count() > 0
    )


def street_cards(page) -> list[dict]:
    cards = page.locator("article").evaluate_all(
        """els => els.map(el => ({
          text: el.innerText,
          links: [...el.querySelectorAll('a[href^="/building/"]')].slice(0, 1).map(a => ({href: a.getAttribute('href'), text: [a.innerText]}))
        }))"""
    )
    if cards:
        return cards
    return page.locator('a[href^="/building/"]').evaluate_all(
        """els => els.map(el => ({text: el.innerText, links: [{href: el.getAttribute('href'), text: [el.innerText]}]}))"""
    )


def scrape_streeteasy(limit: int, headless: bool) -> int:
    config = CONFIG["streeteasy"]
    rows: list[dict] = []
    with browser_context(headless) as context:
        page = context.new_page()
        api_rows: list[dict] = []
        api_responses = []

        def collect_api(response) -> None:
            if "api-v6.streeteasy.com" in response.url and response.request.method == "POST":
                api_responses.append(response)

        page.on("response", collect_api)
        for page_number in range(1, 51):
            if page_number > 1:
                page.wait_for_timeout(500)
            api_rows.clear()
            api_responses.clear()
            url = config["search_url"] if page_number == 1 else f"{config['search_url']}&{config['page_param']}={page_number}"
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(500)
            for response in api_responses:
                try:
                    api_rows.extend(parse_streeteasy_api(response.json()))
                except Exception:
                    pass
            for _ in range(3):
                if not captcha(page):
                    break
                input("Solve captcha in browser window, then press Enter")
                page.wait_for_timeout(500)
            if captcha(page):
                raise RuntimeError("StreetEasy captcha unresolved; use --from-files DIR")
            cards = api_rows or street_cards(page)
            if not cards:
                break
            before = len(rows)
            for card in cards:
                row = card if api_rows else _listing_from_card(card)
                if row and matches_search(row):
                    rows.append(row)
                    if len(rows) >= limit:
                        return save(rows[:limit])
            if len(rows) == before:
                break
    return save(rows[:limit])


def zillow_url() -> str:
    # ponytail: stable rental landing page; query-state URLs trigger PerimeterX.
    return "https://www.zillow.com/homes/for_rent/"

def scrape_zillow(limit: int, headless: bool) -> int:
    rows: list[dict] = []
    with browser_context(headless) as context:
        page = context.new_page()
        url = zillow_url()
        for page_number in range(50):
            if page_number:
                page.wait_for_timeout(500)
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            for _ in range(3):
                if not captcha(page):
                    break
                input("Solve captcha in browser window, then press Enter")
                page.wait_for_timeout(500)
            if captcha(page):
                raise RuntimeError("Zillow captcha unresolved; use --from-files DIR")
            data = page.locator("#__NEXT_DATA__").inner_text()
            payload = json.loads(data)
            items = _zillow_items(payload)
            if not items:
                break
            before = len(rows)
            seen = {row["source_id"] for row in rows}
            for item in items:
                for row in zillow_listings(item):
                    if row["source_id"] not in seen and matches_search(row):
                        rows.append(row)
                        seen.add(row["source_id"])
                    if len(rows) >= limit:
                        return save(rows[:limit])
            next_url = payload.get("props", {}).get("pageProps", {}).get("searchPageState", {}).get("cat1", {}).get("searchList", {}).get("pagination", {}).get("nextUrl")
            if not next_url or len(rows) == before:
                break
            url = urljoin("https://www.zillow.com", next_url)
    return save(rows[:limit])


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="source", required=True)
    for source in ("streeteasy", "zillow"):
        command = sub.add_parser(source)
        command.add_argument("--limit", type=int, default=100)
        command.add_argument("--from-files")
        command.add_argument("--headless", action="store_true")
        command.set_defaults(source=source)
    args = parser.parse_args()
    db.init()
    if args.from_files:
        count = saved_files(args.source, args.from_files, args.limit)
    elif args.source == "streeteasy":
        count = scrape_streeteasy(args.limit, args.headless)
    else:
        count = scrape_zillow(args.limit, args.headless)
    print(f"saved {count} {args.source} listings")


if __name__ == "__main__":
    main()
