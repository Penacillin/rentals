from __future__ import annotations

import json
import re
from pathlib import Path

import db

ROOT = Path(__file__).parent
TEMPLATE = ROOT / "catalog_template.html"
OUTPUT = ROOT / "nyc-rent-catalog-360-park-ave-south.html"
FIELDS = (
    "address", "area", "baths", "beds", "built", "listedAt", "notes", "ppsf", "price",
    "scope", "source", "sqft", "streeteasy", "url", "verification", "yearSource",
    "zillow",
)


def norm(address: str | None) -> str:
    return re.sub(r"\s+", " ", (address or "").strip()).casefold()


def nested(row, note: str | None = None) -> dict:
    return {
        "price": row["price"], "beds": row["beds"], "baths": row["baths"],
        "sqft": row["sqft"], "url": row["url"], "status": row["status"],
        "listedAt": row["listed_at"], "note": note,
    }


def format_value(key: str, value: object) -> str:
    if key == "price":
        return f"${int(value):,}"
    return f"{value:g}" if isinstance(value, float) else str(value)


def scraped_row(rows: list, buildings: dict, hpd: dict) -> dict:
    street = next((row for row in rows if row["source"] == "streeteasy"), None)
    zillow = next((row for row in rows if row["source"] == "zillow"), None)
    primary = street or zillow
    address = primary["address"] or primary["url"] or primary["source_id"]
    building = buildings.get(primary["building_bbl"])
    report = hpd.get(primary["building_bbl"])
    price = street["price"] if street and street["price"] is not None else zillow["price"] if zillow else None
    listed = street["listed_at"] if street and street["listed_at"] else zillow["listed_at"] if zillow else None
    sqft = street["sqft"] if street and street["sqft"] is not None else zillow["sqft"] if zillow else None
    beds = street["beds"] if street and street["beds"] is not None else zillow["beds"] if zillow else None
    baths = street["baths"] if street and street["baths"] is not None else zillow["baths"] if zillow else None
    conflicts = []
    if street and zillow:
        for key, label in (("price", "Price"), ("beds", "Beds"), ("baths", "Baths"), ("sqft", "Sq ft")):
            left, right = street[key], zillow[key]
            if left is None or right is None:
                continue
            mismatch = abs(left - right) > 1 if key == "price" else left != right
            if mismatch:
                conflicts.append(f"{label}: StreetEasy {format_value(key, left)} vs Zillow {format_value(key, right)}")
    if street and zillow:
        comparable = any(street[key] is not None and zillow[key] is not None for key in ("price", "beds", "baths", "sqft"))
        verification = "Exact both — discrepancy" if conflicts else "Exact both — values agree" if comparable else "Exact both — fields incomplete"
    else:
        verification = "StreetEasy only" if street else "Zillow only"
    area = building["neighborhood"] if building else None
    notes = f"FIELD CONFLICT: {'; '.join(conflicts)}" if conflicts else ""
    if report:
        hpd_note = f"HPD: {report['violations_open']} open violations, {report['complaints_total']} complaints"
        notes = f"{notes}; {hpd_note}" if notes else hpd_note
    source_note = notes if conflicts else None
    empty = {"price": None, "beds": None, "baths": None, "sqft": None, "url": None, "status": None, "listedAt": None, "note": None}
    return {
        "address": address,
        "area": area,
        "baths": baths,
        "beds": beds,
        "built": None,
        "listedAt": listed,
        "notes": notes,
        "ppsf": round(price / sqft, 2) if price is not None and sqft else None,
        "price": price,
        "scope": "Configured search area",
        "source": "StreetEasy" if street else "Zillow",
        "sqft": sqft,
        "streeteasy": nested(street, source_note) if street else empty,
        "url": primary["url"],
        "verification": verification,
        "yearSource": None,
        "zillow": nested(zillow, source_note) if zillow else empty,
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
