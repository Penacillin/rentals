import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import scrape


FIXTURES = ROOT / "tests" / "fixtures"


class ScraperParserTests(unittest.TestCase):
    def test_streeteasy_card_fields(self):
        html = (FIXTURES / "streeteasy-search.html").read_text(encoding="utf-8")

        rows = scrape.parse_streeteasy_html(html)

        self.assertEqual(len(rows), 1)
        self.assertEqual(
            rows[0],
            {
                "source": "streeteasy",
                "source_id": "/building/123-main-st",
                "address": "123 Main St #7B",
                "unit": "7B",
                "price": 5500,
                "beds": 2.0,
                "baths": 1.5,
                "sqft": 800,
                "url": "https://streeteasy.com/building/123-main-st",
                "status": "search-card",
                "raw": mock.ANY,
            },
        )

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
                            "urlPath": "/building/123-main-st/7b",
                            "status": "ACTIVE",
                        }
                    }]
                }
            }
        }
        rows = scrape.parse_streeteasy_api(payload)
        self.assertEqual(rows[0]["address"], "123 Main St #7B")
        self.assertEqual(rows[0]["baths"], 1.5)
        self.assertEqual(rows[0]["url"], "https://streeteasy.com/building/123-main-st/7b")

    def test_zillow_units_expand_to_distinct_rows(self):
        html = (FIXTURES / "zillow-search.html").read_text(encoding="utf-8")

        items = scrape.parse_zillow_html(html)
        rows = [row for item in items for row in scrape.zillow_listings(item)]

        self.assertEqual(len(items), 1)
        self.assertEqual([row["source_id"] for row in rows], ["98765:0", "98765:1"])
        self.assertEqual([row["price"] for row in rows], [3100, 3300])
        self.assertEqual([row["beds"] for row in rows], [1.0, 2.0])
        self.assertEqual([row["unit"] for row in rows], ["1A", "2B"])
        self.assertEqual(
            [row["url"] for row in rows],
            ["https://www.zillow.com/b/123-main-st/98765_zpid/"] * 2,
        )

    def test_saved_files_streeteasy_uses_fixture_without_network(self):
        with mock.patch.object(scrape, "save", return_value=1) as save:
            count = scrape.saved_files("streeteasy", str(FIXTURES), 100)

        self.assertEqual(count, 1)
        rows = save.call_args.args[0]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["address"], "123 Main St #7B")

    def test_saved_files_zillow_expands_units_without_network(self):
        with mock.patch.object(scrape, "save", return_value=2) as save:
            count = scrape.saved_files("zillow", str(FIXTURES), 100)

        self.assertEqual(count, 2)
        rows = save.call_args.args[0]
        self.assertEqual([row["source_id"] for row in rows], ["98765:0", "98765:1"])


if __name__ == "__main__":
    unittest.main()
