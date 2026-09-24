from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import db
import geo

ROOT = Path(__file__).parent
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
API = "https://otp-mta-prod.camsys-apps.com/otp/routers/default/plan"
TZ = ZoneInfo("America/New_York")




def next_weekday(today: date | None = None) -> date:
    value = today or datetime.now(TZ).date()
    value += timedelta(days=1)
    while value.weekday() >= 5:
        value += timedelta(days=1)
    return value


def coordinates(value: object) -> tuple[float, float] | None:
    if isinstance(value, dict):
        for lat_key, lon_key in (("lat", "lon"), ("latitude", "longitude"), ("lat", "lng")):
            lat, lon = value.get(lat_key), value.get(lon_key)
            if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
                return float(lat), float(lon)
        for child in value.values():
            found = coordinates(child)
            if found:
                return found
    elif isinstance(value, list):
        if len(value) >= 2 and all(isinstance(item, (int, float)) for item in value[:2]):
            first, second = map(float, value[:2])
            return (second, first) if abs(first) > 90 else (first, second)
        for child in value:
            found = coordinates(child)
            if found:
                return found
    return None


def listing_coordinates(row, buildings: dict) -> tuple[float, float] | None:
    try:
        raw = json.loads(row["raw"] or "null")
    except json.JSONDecodeError:
        raw = None
    found = coordinates(raw)
    if found:
        return found
    building = buildings.get(row["building_bbl"])
    if building and building["lat"] is not None and building["lon"] is not None:
        return float(building["lat"]), float(building["lon"])
    address = db.building_address(row["address"])
    result = geo.geocode(f"{address}, New York, NY") if address else None
    if result and result["lat"] is not None and result["lon"] is not None:
        return float(result["lat"]), float(result["lon"])
    return None


def plan(
    origin: tuple[float, float],
    destination: tuple[float, float],
    travel_date: date,
    mode: str = "WALK,TRANSIT",
) -> dict:
    params = {
        "fromPlace": f"{origin[0]},{origin[1]}",
        "toPlace": f"{destination[0]},{destination[1]}",
        "date": travel_date.isoformat(),
        "time": "09:30am",
        "mode": mode,
        "arriveBy": "false",
    }
    request = Request(f"{API}?{urlencode(params)}", headers={"User-Agent": "rentals/0.1"})
    try:
        with urlopen(request, timeout=30) as response:
            return json.load(response)
    except HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"MTA OTP HTTP {error.code}: {body[:200]}") from error


def route_minutes(payload: dict) -> tuple[int | None, int | None]:
    itineraries = (payload.get("plan") or {}).get("itineraries") or []
    walk, transit = [], []
    for itinerary in itineraries:
        duration = itinerary.get("duration")
        if not isinstance(duration, (int, float)):
            continue
        legs = itinerary.get("legs") or []
        has_transit = any(leg.get("transitLeg") or leg.get("mode") not in {None, "WALK"} for leg in legs)
        if has_transit or itinerary.get("transitTime", 0) > 0:
            transit.append(duration)
        else:
            walk.append(duration)
    return (
        round(min(walk) / 60) if walk else None,
        round(min(transit) / 60) if transit else None,
    )


def calculate(
    limit: int | None = None,
    travel_date: date | None = None,
    refresh: bool = False,
    source_ids: set[str] | None = None,
) -> int:
    travel_date = travel_date or next_weekday()
    destination = (
        float(CONFIG["center"]["lat"]),
        float(CONFIG["center"]["lng"]),
    )
    db.init()
    with db.connect() as conn:
        listings = conn.execute("SELECT * FROM listings ORDER BY source, source_id").fetchall()
        if limit is not None and source_ids is None:
            listings = listings[:limit]
        buildings = {row["bbl"]: row for row in conn.execute("SELECT * FROM buildings")}
        groups: dict[str, list] = {}
        for row in listings:
            group_key = row["building_bbl"] or db.building_address(row["address"]) or row["source_id"]
            groups.setdefault(group_key, []).append(row)
        targets = [
            (rows, [row for row in rows if source_ids is None or row["source_id"] in source_ids])
            for rows in groups.values()
        ]
        targets = [(all_rows, selected) for all_rows, selected in targets if selected]

        def fetch(group):
            all_rows, selected = group
            cached = next(
                (
                    row for row in all_rows
                    if row["commute_date"]
                    and (row["walk_minutes"] is not None or row["transit_minutes"] is not None)
                ),
                None,
            )
            if cached and not refresh:
                return selected, (
                    cached["walk_minutes"],
                    cached["transit_minutes"],
                    cached["commute_date"],
                ), None
            origin = listing_coordinates(selected[0], buildings)
            if not origin:
                return selected, None, f"{selected[0]['address']}: no coordinates"
            try:
                walk, transit = route_minutes(plan(origin, destination, travel_date))
                if walk is None:
                    walk, _ = route_minutes(plan(origin, destination, travel_date, mode="WALK"))
                return selected, (walk, transit, travel_date.isoformat()), None
            except Exception as error:
                return selected, None, f"{selected[0]['address']}: {error}"

        done = 0
        with ThreadPoolExecutor(max_workers=8) as pool:
            for rows, result, error in pool.map(fetch, targets):
                if error:
                    print(f"skipped {error}")
                    continue
                walk, transit, commute_date = result
                for row in rows:
                    db.upsert_commute(
                        conn, row["source"], row["source_id"], commute_date, walk, transit
                    )
                    done += 1
        conn.commit()
    return done


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, help="maximum source rows; default all")
    parser.add_argument("--date", help="YYYY-MM-DD; default next weekday")
    parser.add_argument("--refresh", action="store_true", help="replace existing building routes")
    parser.add_argument("--source-id", action="append", help="update only these source IDs; repeatable")
    args = parser.parse_args()
    travel_date = date.fromisoformat(args.date) if args.date else None
    done = calculate(args.limit, travel_date, args.refresh, set(args.source_id) if args.source_id else None)
    print(f"saved commute times for {done} listings on {travel_date or next_weekday()} at 09:30 America/New_York")


if __name__ == "__main__":
    main()
