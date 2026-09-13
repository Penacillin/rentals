from __future__ import annotations

import argparse
import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).parent
DB_PATH = ROOT / "data" / "rentals.sqlite3"
UNIT_RE = re.compile(
    r"\s+(?:APT|UNIT|FLOOR|PENTHOUSE|PH)\s+#?([A-Za-z0-9-]+)\s*$",
    re.IGNORECASE,
)
HASH_UNIT_RE = re.compile(r"\s+#([A-Za-z0-9-]+)\s*$")

@dataclass(slots=True)
class Listing:
    source: str
    source_id: str
    address: str | None = None
    unit: str | None = None
    building_bbl: str | None = None
    price: int | None = None
    beds: float | None = None
    baths: float | None = None
    sqft: int | None = None
    url: str | None = None
    status: str | None = None
    listed_at: str | None = None
    raw: object = None


def building_address(address: str | None) -> str:
    if not address:
        return ""
    return HASH_UNIT_RE.sub("", UNIT_RE.sub("", address)).strip()


def unit_from_address(address: str | None) -> str | None:
    if not address:
        return None
    match = UNIT_RE.search(address) or HASH_UNIT_RE.search(address)
    return match.group(1) if match else None

SCHEMA = """
CREATE TABLE IF NOT EXISTS buildings (
  bbl TEXT PRIMARY KEY,
  bin TEXT,
  address TEXT NOT NULL,
  borough TEXT,
  neighborhood TEXT,
  lat REAL,
  lon REAL,
  year_built INTEGER,
  year_source TEXT,
  hpd_building_id TEXT,
  hpd_class TEXT,
  hpd_units INTEGER
);
CREATE TABLE IF NOT EXISTS listings (
  source TEXT NOT NULL,
  source_id TEXT NOT NULL,
  address TEXT,
  unit TEXT,
  building_bbl TEXT,
  price INTEGER,
  beds REAL,
  baths REAL,
  sqft INTEGER,
  url TEXT,
  status TEXT,
  listed_at TEXT,
  commute_date TEXT,
  walk_minutes INTEGER,
  transit_minutes INTEGER,
  commute_fetched_at TEXT,
  scraped_at TEXT NOT NULL DEFAULT (datetime('now')),
  raw TEXT,
  PRIMARY KEY (source, source_id)
);
CREATE TABLE IF NOT EXISTS hpd (
  bbl TEXT PRIMARY KEY,
  complaints_total INTEGER,
  complaints_open INTEGER,
  violations_open INTEGER,
  violations_total INTEGER,
  vacate_orders INTEGER,
  bedbug_filings INTEGER,
  last_registration TEXT,
  owner TEXT,
  mgmt_agent TEXT,
  fetched_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_listings_bbl ON listings(building_bbl);
CREATE INDEX IF NOT EXISTS idx_listings_addr ON listings(address);
"""


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    return db


def init() -> None:
    with connect() as db:
        db.executescript(SCHEMA)
        for table, columns_to_add in (
            ("listings", (
                ("listed_at", "TEXT"),
                ("commute_date", "TEXT"),
                ("walk_minutes", "INTEGER"),
                ("transit_minutes", "INTEGER"),
                ("commute_fetched_at", "TEXT"),
            )),
            ("buildings", (
                ("year_built", "INTEGER"),
                ("year_source", "TEXT"),
            )),
        ):
            columns = {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}
            for name, definition in columns_to_add:
                if name not in columns:
                    db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
        db.commit()


def upsert_building(db: sqlite3.Connection, building: dict) -> None:
    db.execute(
        """INSERT INTO buildings
        (bbl, bin, address, borough, neighborhood, lat, lon, year_built, year_source,
         hpd_building_id, hpd_class, hpd_units)
        VALUES (:bbl, :bin, :address, :borough, :neighborhood, :lat, :lon, :year_built,
                :year_source, :hpd_building_id, :hpd_class, :hpd_units)
        ON CONFLICT(bbl) DO UPDATE SET
          bin=excluded.bin, address=excluded.address, borough=excluded.borough,
          neighborhood=COALESCE(excluded.neighborhood, buildings.neighborhood),
          lat=excluded.lat, lon=excluded.lon,
          year_built=COALESCE(excluded.year_built, buildings.year_built),
          year_source=COALESCE(excluded.year_source, buildings.year_source),
          hpd_building_id=COALESCE(excluded.hpd_building_id, buildings.hpd_building_id),
          hpd_class=COALESCE(excluded.hpd_class, buildings.hpd_class),
          hpd_units=COALESCE(excluded.hpd_units, buildings.hpd_units)""",
        {key: building.get(key) for key in (
            "bbl", "bin", "address", "borough", "neighborhood", "lat", "lon",
            "year_built", "year_source", "hpd_building_id", "hpd_class", "hpd_units",
        )},
    )


def upsert_listing(db: sqlite3.Connection, listing: Listing) -> None:
    values = {key: getattr(listing, key) for key in (
        "source", "source_id", "address", "unit", "building_bbl", "price", "beds",
        "baths", "sqft", "url", "status", "listed_at",
    )}
    values["raw"] = json.dumps(listing.raw, ensure_ascii=False)
    db.execute(
        """INSERT INTO listings
        (source, source_id, address, unit, building_bbl, price, beds, baths, sqft,
         url, status, listed_at, raw)
        VALUES (:source, :source_id, :address, :unit, :building_bbl, :price, :beds,
                :baths, :sqft, :url, :status, :listed_at, :raw)
        ON CONFLICT(source, source_id) DO UPDATE SET
          address=excluded.address, unit=excluded.unit, building_bbl=excluded.building_bbl,
          price=excluded.price, beds=excluded.beds, baths=excluded.baths,
          sqft=excluded.sqft, url=excluded.url, status=excluded.status,
          listed_at=excluded.listed_at, scraped_at=datetime('now'), raw=excluded.raw""",
        values,
    )


def upsert_commute(
    db: sqlite3.Connection,
    source: str,
    source_id: str,
    commute_date: str,
    walk_minutes: int | None,
    transit_minutes: int | None,
) -> None:
    db.execute(
        """UPDATE listings
        SET commute_date = ?, walk_minutes = ?, transit_minutes = ?,
            commute_fetched_at = datetime('now')
        WHERE source = ? AND source_id = ?""",
        (commute_date, walk_minutes, transit_minutes, source, source_id),
    )


def upsert_hpd(db: sqlite3.Connection, report: dict) -> None:
    keys = (
        "bbl", "complaints_total", "complaints_open", "violations_open",
        "violations_total", "vacate_orders", "bedbug_filings", "last_registration",
        "owner", "mgmt_agent",
    )
    db.execute(
        """INSERT INTO hpd (bbl, complaints_total, complaints_open, violations_open,
          violations_total, vacate_orders, bedbug_filings, last_registration, owner,
          mgmt_agent) VALUES (:bbl, :complaints_total, :complaints_open,
          :violations_open, :violations_total, :vacate_orders, :bedbug_filings,
          :last_registration, :owner, :mgmt_agent)
        ON CONFLICT(bbl) DO UPDATE SET
          complaints_total=excluded.complaints_total, complaints_open=excluded.complaints_open,
          violations_open=excluded.violations_open, violations_total=excluded.violations_total,
          vacate_orders=excluded.vacate_orders, bedbug_filings=excluded.bedbug_filings,
          last_registration=excluded.last_registration, owner=excluded.owner,
          mgmt_agent=excluded.mgmt_agent, fetched_at=datetime('now')""",
        {key: report.get(key) for key in keys},
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("init",))
    parser.parse_args()
    init()
    print(DB_PATH)


if __name__ == "__main__":
    main()
