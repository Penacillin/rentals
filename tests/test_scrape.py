import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import scrape
import build_catalog


FIXTURES = ROOT / "tests" / "fixtures"


class ScraperParserTests(unittest.TestCase):
    def test_streeteasy_card_fields(self):
        html = (FIXTURES / "streeteasy-search.html").read_text(encoding="utf-8")

        rows = scrape.parse_streeteasy_html(html)

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertIsInstance(row, scrape.db.Listing)
        self.assertEqual(row.source, "streeteasy")
        self.assertEqual(row.source_id, "/building/123-main-st")
        self.assertEqual(row.address, "123 Main St #7B")
        self.assertEqual(row.unit, "7B")
        self.assertEqual(row.price, 5500)
        self.assertEqual(row.beds, 2.0)
        self.assertEqual(row.baths, 1.5)
        self.assertEqual(row.sqft, 800)
        self.assertEqual(row.listed_at, "2 days ago")
        self.assertEqual(row.url, "https://streeteasy.com/building/123-main-st")
        self.assertEqual(row.status, "search-card")

    def test_streeteasy_api_response_fields(self):
        payload = {
            "data": {
                "searchRentals": {
                    "edges": [{
                        "node": {
                            "id": "api-7b",
                            "street": "123 Main St",
                            "unit": "7B",
                            "price": 5500,
                            "bedroomCount": 2,
                            "fullBathroomCount": 1,
                            "halfBathroomCount": 1,
                            "listedDate": "2026-09-10",
                            "urlPath": "/building/123-main-st/7b",
                        }
                    }]
                }
            }
        }
        rows = scrape.parse_streeteasy_api(payload)
        self.assertEqual(rows[0].address, "123 Main St #7B")
        self.assertEqual(rows[0].baths, 1.5)
        self.assertEqual(rows[0].listed_at, "2026-09-10")
        self.assertEqual(rows[0].url, "https://streeteasy.com/building/123-main-st/7b")
    def test_catalog_surfaces_all_source_field_conflicts(self):
        def row(source, price, beds, baths, sqft):
            return {
                "source": source,
                "source_id": source,
                "address": "123 Main St #7B",
                "unit": "7B",
                "building_bbl": None,
                "price": price,
                "beds": beds,
                "baths": baths,
                "sqft": sqft,
                "url": f"https://{source}.example/7b",
                "status": "search-list",
                "listed_at": "today",
            }

        result = build_catalog.scraped_row(
            [row("streeteasy", 5000, 1.0, 1.0, 700), row("zillow", 5100, 2.0, 1.5, 800)],
            {},
            {},
        )

        self.assertEqual(result["verification"], "Exact both — discrepancy")
        self.assertEqual(result["listedAt"], "today")
        self.assertEqual(result["streeteasy"]["listedAt"], "today")
        self.assertIn("Price:", result["notes"])
        self.assertIn("Beds:", result["notes"])
        self.assertIn("Baths:", result["notes"])
        self.assertIn("Sq ft:", result["notes"])
        self.assertEqual(result["streeteasy"]["price"], 5000)
        self.assertEqual(result["zillow"]["price"], 5100)
    def test_commute_route_selects_walk_and_transit_minutes(self):
        import commute

        payload = {
            "plan": {
                "itineraries": [
                    {"duration": 1800, "transitTime": 0, "legs": [{"mode": "WALK"}]},
                    {"duration": 2100, "transitTime": 1500, "legs": [{"mode": "SUBWAY", "transitLeg": True}]},
                ]
            }
        }

        self.assertEqual(commute.route_minutes(payload), (30, 35))

    def test_listing_address_matches_building_and_unit_forms(self):
        self.assertEqual(
            build_catalog.norm(
                build_catalog.listing_address(
                    {"address": "185 York Street", "unit": "3B", "url": None, "source_id": "x"}
                )
            ),
            build_catalog.norm("185 York St #3B"),
        )

    def test_streeteasy_detail_metadata_extracts_year_and_features(self):
        year, features = scrape.detail_metadata(
            """
            <main><h2>About the building</h2>
            <p>Central air Dishwasher Washer/dryer Doorman Elevator</p>
            <p>2012 built</p></main>
            """
        )

        self.assertEqual(year, 2012)
        self.assertEqual(
            features,
            {
                "centralAir": True,
                "dishwasher": True,
                "washerDryer": True,
                "doorman": True,
                "elevator": True,
            },
        )

    def test_streeteasy_detail_metadata_is_unit_specific(self):
        rows = [
            scrape.db.Listing(
                source="streeteasy",
                source_id="4a",
                building_bbl="1001230001",
                address="778 Madison Avenue #4A",
                url="https://streeteasy.com/building/778-madison-avenue_new_york/4a",
            ),
            scrape.db.Listing(
                source="streeteasy",
                source_id="9ab",
                building_bbl="1001230001",
                address="778 Madison Avenue #9AB",
                url="https://streeteasy.com/building/778-madison-avenue_new_york/9ab",
            ),
        ]
        fixtures = {
            rows[0].url: FIXTURES / "streeteasy-778-madison-4a.html",
            rows[1].url: FIXTURES / "streeteasy-778-madison-9ab.html",
        }

        class Locator:
            def __init__(self, page):
                self.page = page

            def inner_text(self, timeout=None):
                return self.page.html

            def wait_for(self, **kwargs):
                return None

            def count(self):
                return 0

        class Page:
            def __init__(self):
                self.html = ""
                self.urls = []

            def wait_for_timeout(self, _milliseconds):
                pass

            def goto(self, url, **kwargs):
                self.urls.append(url)
                self.html = fixtures[url].read_text(encoding="utf-8")

            def title(self):
                return "fixture"

            def evaluate(self, script):
                if "fetch(" in script:
                    url = script.split("fetch(", 1)[1].split(",", 1)[0].strip('"')
                    self.urls.append(url)
                    self.html = fixtures[url].read_text(encoding="utf-8")
                return self.html

            def get_by_role(self, _role):
                return Locator(self)

            def locator(self, _selector):
                return Locator(self)
        page = Page()
        enriched = {}

        def collect(row, year, features):
            enriched[row.source_id] = (year, features)

        years = scrape.enrich_streeteasy_years(rows, page, collect)

        self.assertEqual(page.urls, [rows[0].url, rows[1].url])
        self.assertEqual(years, {"1001230001": 1908})
        self.assertIsNone(rows[0].raw)
        self.assertIsNone(rows[1].raw)
        self.assertFalse(enriched["4a"][1]["washerDryer"])
        self.assertTrue(enriched["9ab"][1]["washerDryer"])

    def test_zillow_units_expand_to_distinct_rows(self):
        html = (FIXTURES / "zillow-search.html").read_text(encoding="utf-8")

        items = scrape.parse_zillow_html(html)
        rows = [row for item in items for row in scrape.zillow_listings(item)]

        self.assertEqual(len(items), 1)
        self.assertEqual([row.source_id for row in rows], ["98765:0", "98765:1"])
        self.assertEqual([row.price for row in rows], [3100, 3300])
        self.assertEqual([row.beds for row in rows], [1.0, 2.0])
        self.assertEqual([row.unit for row in rows], ["1A", "2B"])
        self.assertEqual([row.listed_at for row in rows], ["Sep 10, 2026"] * 2)
        self.assertEqual(
            [row.url for row in rows],
            ["https://www.zillow.com/b/123-main-st/98765_zpid/"] * 2,
        )

    def test_saved_files_streeteasy_uses_fixture_without_network(self):
        with mock.patch.object(scrape, "save", return_value=1) as save:
            count = scrape.saved_files("streeteasy", str(FIXTURES), 100)

        self.assertEqual(count, 1)
        rows = save.call_args.args[0]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].address, "123 Main St #7B")

    def test_saved_files_zillow_expands_units_without_network(self):
        with mock.patch.object(scrape, "save", return_value=2) as save:
            count = scrape.saved_files("zillow", str(FIXTURES), 100)

        self.assertEqual(count, 2)
        rows = save.call_args.args[0]
        self.assertEqual([row.source_id for row in rows], ["98765:0", "98765:1"])


if __name__ == "__main__":
    unittest.main()
