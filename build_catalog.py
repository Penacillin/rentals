from __future__ import annotations

import json
import re
from pathlib import Path

import db

ROOT = Path(__file__).parent
TEMPLATE = ROOT / "catalog_template.html"
OUTPUT = ROOT / "nyc-rent-catalog-360-park-ave-south.html"
FIELDS = (
    "address", "area", "baths", "beds", "built", "notes", "ppsf", "price",
    "scope", "source", "sqft", "streeteasy", "url", "verification", "yearSource",
    "zillow",
)


def norm(address: str | None) -> str:
    return re.sub(r"\s+", " ", (address or "").strip()).casefold()


def nested(row) -> dict:
    return {
        "price": row["price"], "beds": row["beds"], "baths": row["baths"],
        "sqft": row["sqft"], "url": row["url"], "status": row["status"],
    }


def scraped_row(rows: list, buildings: dict, hpd: dict) -> dict:
    street = next((row for row in rows if row["source"] == "streeteasy"), None)
    zillow = next((row for row in rows if row["source"] == "zillow"), None)
    primary = street or zillow
    address = primary["address"] or primary["url"] or primary["source_id"]
    building = buildings.get(primary["building_bbl"])
    report = hpd.get(primary["building_bbl"])
    price = street["price"] if street and street["price"] is not None else zillow["price"] if zillow else None
    sqft = street["sqft"] if street and street["sqft"] is not None else zillow["sqft"] if zillow else None
    beds = street["beds"] if street and street["beds"] is not None else zillow["beds"] if zillow else None
    baths = street["baths"] if street and street["baths"] is not None else zillow["baths"] if zillow else None
    if street and zillow:
        verification = "Exact both — values agree" if street["price"] is not None and abs(street["price"] - (zillow["price"] or 0)) <= 1 else "Exact both — discrepancy"
    else:
        verification = "StreetEasy only" if street else "Zillow only"
    area = building["neighborhood"] if building else None
    notes = ""
    if report:
        notes = f"HPD: {report['violations_open']} open violations, {report['complaints_total']} complaints"
    return {
        "address": address,
        "area": area,
        "baths": baths,
        "beds": beds,
        "built": None,
        "notes": notes,
        "ppsf": round(price / sqft, 2) if price is not None and sqft else None,
        "price": price,
        "scope": "StreetEasy live",
        "source": "StreetEasy" if street else "Zillow",
        "sqft": sqft,
        "streeteasy": nested(street) if street else {"price": None, "beds": None, "baths": None, "sqft": None, "url": None, "status": None},
        "url": primary["url"],
        "verification": verification,
        "yearSource": None,
        "zillow": nested(zillow) if zillow else {"price": None, "beds": None, "baths": None, "sqft": None, "url": None, "status": None},
    }


def build_rows() -> list[dict]:
    with db.connect() as conn:
        listings = conn.execute("SELECT * FROM listings ORDER BY source, source_id").fetchall()
        buildings = {row["bbl"]: row for row in conn.execute("SELECT * FROM buildings")}
        hpd = {row["bbl"]: row for row in conn.execute("SELECT * FROM hpd")}
    scraped: dict[str, list] = {}
    for row in listings:
        scraped.setdefault(norm(row["address"]), []).append(row)
    return [
        {key: scraped_row(rows, buildings, hpd).get(key) for key in FIELDS}
        for rows in scraped.values()
    ]


def main() -> None:
    rows = build_rows()
    template = TEMPLATE.read_text(encoding="utf-8")
    if template.count("{{DATA}}") != 1:
        raise ValueError("catalog template must contain one {{DATA}} placeholder")
    data = json.dumps(rows, ensure_ascii=False).replace("<", "\\u003c")
    OUTPUT.write_text(template.replace("{{DATA}}", data), encoding="utf-8")
    print(f"wrote {len(rows)} rows to {OUTPUT}")


if __name__ == "__main__":
    main()
