from __future__ import annotations

import argparse
import json
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import db
import geo

SOCRATA = "https://data.cityofnewyork.us/resource"
JUSTFIX = "https://api.justfix.org/api/address/wowza"


def justfix_report(bbl: str) -> dict | None:
    boro, block, lot = bbl_parts(bbl)
    url = f"{JUSTFIX}?{urlencode({'block': block, 'lot': lot, 'borough': boro})}"
    try:
        request = Request(url, headers={"User-Agent": "rentals/0.1"})
        with urlopen(request, timeout=30) as response:
            payload = json.load(response)
    except (OSError, ValueError):
        return None
    return next((row for row in payload.get("addrs", []) if row.get("bbl") == bbl), None)


def justfix_contact(row: dict, title: str) -> str | None:
    for contact in row.get("ownernames") or []:
        if contact.get("title", "").casefold() == title.casefold():
            return contact.get("value")
    return None




def fetch(dataset: str, params: list[tuple[str, str]]) -> list[dict]:
    url = f"{SOCRATA}/{dataset}.json?{urlencode(params)}"
    request = Request(url, headers={"User-Agent": "rentals/0.1"})
    with urlopen(request, timeout=30) as response:
        return json.load(response)
PLUTO = "64uk-42ks"


def pluto_year(bbl: str) -> int | None:
    rows = fetch(
        PLUTO,
        [("$select", "yearbuilt"), ("$where", f"bbl = '{bbl}'"), ("$limit", "1")],
    )
    value = rows[0].get("yearbuilt") if rows else None
    return int(value) if value else None


def count(dataset: str, filters: list[tuple[str, str]], column: str) -> int:
    rows = fetch(dataset, [*filters, ("$select", f"count({column})")])
    if not rows:
        return 0
    return int(next(iter(rows[0].values())))


def bbl_parts(bbl: str) -> tuple[str, str, str]:
    if len(bbl) != 10 or not bbl.isdigit():
        raise ValueError(f"invalid 10-digit BBL: {bbl}")
    return bbl[0], bbl[1:6], bbl[6:10]


def contact_name(contact: dict) -> str | None:
    return (contact.get("corporationname") or " ".join(
        part for part in (contact.get("firstname"), contact.get("lastname")) if part
    )).strip() or None


def hpd_report(bbl: str) -> dict:
    boro, block, lot = bbl_parts(bbl)
    fast = justfix_report(bbl)
    if fast:
        return {
            "bbl": bbl,
            "complaints_total": int(fast.get("totalcomplaints") or 0),
            "complaints_open": count(
                "ygpa-z7cr",
                [("bbl", bbl), ("$where", "complaint_status != 'CLOSE'")],
                "complaint_id",
            ),
            "violations_total": int(fast.get("totalviolations") or 0),
            "violations_open": int(fast.get("openviolations") or 0),
            "vacate_orders": count("tb8q-a3ar", [("bbl", bbl)], "building_id"),
            "bedbug_filings": count("wz6d-d3jb", [("bbl", bbl)], "building_id"),
            "owner": (fast.get("corpnames") or [None])[0],
            "mgmt_agent": justfix_contact(fast, "Agent") or justfix_contact(fast, "SiteManager"),
            "year_built": fast.get("yearbuilt"),
            "hpd_building_id": fast.get("hpdbuildingid"),
        }
    block_lot = [("boroid", boro), ("block", block), ("lot", lot)]
    report = {
        "bbl": bbl,
        "complaints_total": count("ygpa-z7cr", [("bbl", bbl)], "complaint_id"),
        "complaints_open": count(
            "ygpa-z7cr",
            [("bbl", bbl), ("$where", "complaint_status != 'CLOSE'")],
            "complaint_id",
        ),
        "violations_total": count("wvxf-dwi5", block_lot, "violationid"),
        "violations_open": count(
            "wvxf-dwi5",
            [*block_lot,
             ("$where", "currentstatus not in ('VIOLATION CLOSED','VIOLATION DISMISSED')")],
            "violationid",
        ),
        "vacate_orders": count("tb8q-a3ar", [("bbl", bbl)], "building_id"),
        "bedbug_filings": count("wz6d-d3jb", [("bbl", bbl)], "building_id"),
        "last_registration": None,
        "owner": None,
        "mgmt_agent": None,
    }
    registrations = fetch(
        "tesw-yqqr",
        [*block_lot, ("$select", "registrationid,lastregistrationdate"),
         ("$order", "lastregistrationdate DESC"), ("$limit", "1")],
    )
    if registrations:
        registration = registrations[0]
        report["last_registration"] = registration.get("lastregistrationdate")
        registration_id = registration.get("registrationid")
        if registration_id:
            contacts = fetch("feu5-w2e2", [("registrationid", registration_id)])
            owners = [c for c in contacts if c.get("type", "").lower() != "agent"]
            agents = [c for c in contacts if c.get("type", "").lower() == "agent"]
            report["owner"] = contact_name(owners[0]) if owners else None
            report["mgmt_agent"] = contact_name(agents[0]) if agents else None
    return report


def jurisdiction(bbl: str) -> dict | None:
    boro, block, lot = bbl_parts(bbl)
    rows = fetch("kj4p-ruqc", [("boroid", boro), ("block", block), ("lot", lot)])
    if not rows:
        return None
    row = rows[0]
    return {
        "hpd_building_id": row.get("buildingid"),
        "hpd_class": row.get("lifecycle"),
        "hpd_units": None,
    }


def enrich() -> None:
    db.init()
    with db.connect() as conn:
        addresses = conn.execute(
            "SELECT DISTINCT address FROM listings WHERE building_bbl IS NULL AND address IS NOT NULL"
        ).fetchall()
        for row in addresses:
            result = geo.geocode(db.building_address(row["address"]))
            if not result or not result.get("bbl"):
                continue
            db.upsert_building(conn, {**result, "address": db.building_address(row["address"])})
            conn.execute(
                "UPDATE listings SET building_bbl=? WHERE address=? AND building_bbl IS NULL",
                (result["bbl"], row["address"]),
            )
        conn.commit()
        buildings = conn.execute(
            "SELECT bbl FROM buildings WHERE year_built IS NULL AND bbl NOT IN (SELECT bbl FROM hpd)"
        ).fetchall()
        for row in buildings:
            year = pluto_year(row["bbl"])
            if year:
                conn.execute(
                    "UPDATE buildings SET year_built=?, year_source=? WHERE bbl=?",
                    (year, "NYC PLUTO", row["bbl"]),
                )
        conn.commit()

        buildings = conn.execute(
            "SELECT bbl FROM buildings WHERE bbl NOT IN (SELECT bbl FROM hpd)"
        ).fetchall()
        for row in buildings:
            report = hpd_report(row["bbl"])
            db.upsert_hpd(conn, report)
            if report.get("year_built") or report.get("hpd_building_id"):
                conn.execute(
                    """UPDATE buildings
                       SET year_built=COALESCE(?, year_built),
                           year_source=COALESCE(?, year_source),
                           hpd_building_id=COALESCE(?, hpd_building_id)
                       WHERE bbl=?""",
                    (
                        report.get("year_built"),
                        "JustFix" if report.get("year_built") else None,
                        report.get("hpd_building_id"),
                        row["bbl"],
                    ),
                )
        conn.commit()

        buildings = conn.execute(
            "SELECT bbl FROM buildings WHERE hpd_building_id IS NULL"
        ).fetchall()
        for row in buildings:
            info = jurisdiction(row["bbl"])
            if info:
                conn.execute(
                    "UPDATE buildings SET hpd_building_id=?, hpd_class=?, hpd_units=? WHERE bbl=?",
                    (info["hpd_building_id"], info["hpd_class"], info["hpd_units"], row["bbl"]),
                )
        conn.commit()


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    report = sub.add_parser("report")
    report.add_argument("bbl")
    sub.add_parser("enrich")
    args = parser.parse_args()
    if args.command == "report":
        print(json.dumps(hpd_report(args.bbl), indent=2))
    else:
        enrich()


if __name__ == "__main__":
    main()
