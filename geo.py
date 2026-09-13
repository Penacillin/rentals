from __future__ import annotations

import argparse
import json
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API = "https://geosearch.planninglabs.nyc/v2/search"


def geocode(address: str) -> dict | None:
    url = f"{API}?{urlencode({'text': address, 'size': 1})}"
    request = Request(url, headers={"User-Agent": "rentals/0.1"})
    with urlopen(request, timeout=20) as response:
        payload = json.load(response)
    features = payload.get("features") or []
    if not features:
        return None
    feature = features[0]
    props = feature.get("properties") or {}
    pad = (props.get("addendum") or {}).get("pad") or {}
    coordinates = feature.get("geometry", {}).get("coordinates") or [None, None]
    return {
        "bbl": pad.get("bbl"),
        "bin": pad.get("bin"),
        "borough": props.get("borough"),
        "neighborhood": props.get("neighbourhood"),
        "lat": coordinates[1] if len(coordinates) > 1 else None,
        "lon": coordinates[0] if coordinates else None,
        "label": props.get("label"),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("address")
    args = parser.parse_args()
    print(json.dumps(geocode(args.address), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
