"""Build the road-centre list used to place archive streets on the map.

    python scripts/build_osm_roads_seed.py                      # downloads from Overpass
    python scripts/build_osm_roads_seed.py --from osm_roads.json

Cluster lists name streets ("Bedok Reservoir Road (Blk 136)"). Streets with an HDB block
number are placed on that block; the rest go to the middle of the named road. This
script asks OpenStreetMap (via the Overpass API) for every named road in Singapore and
keeps one row per road name: the median of its segments' centre points.

Data (c) OpenStreetMap contributors, ODbL 1.0 - https://www.openstreetmap.org/copyright
"""

import argparse
import csv
import json
import os
import statistics
from collections import defaultdict
from datetime import date

import requests

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO, "data", "seed", "osm_road_centroids.csv")
QUERY = '[out:json][timeout:180];way(1.15,103.6,1.48,104.1)["highway"]["name"];out center tags;'
# overpass.kumi.systems returned HTTP 500 on 17 Oct 2026; overpass-api.de works with a User-Agent
MIRRORS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]


def fetch():
    for url in MIRRORS:
        try:
            r = requests.post(url, data={"data": QUERY}, timeout=300,
                              headers={"User-Agent": "upside-devs-dengue/1.0"})
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as e:
            print(f"{url}: {e}")
    raise SystemExit("every Overpass mirror failed")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="src", help="saved Overpass JSON instead of downloading")
    a = ap.parse_args()

    if a.src:
        with open(a.src) as f:
            body = json.load(f)
    else:
        body = fetch()

    pts = defaultdict(list)
    for e in body["elements"]:
        c, name = e.get("center"), (e.get("tags") or {}).get("name")
        if c and name:
            pts[name.strip()].append((c["lon"], c["lat"]))

    osm_date = (body.get("osm3s") or {}).get("timestamp_osm_base", "")[:10] or date.today().isoformat()
    with open(OUT, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["road_name", "lon", "lat", "n_segments", "osm_date"])
        for name in sorted(pts):
            p = pts[name]
            w.writerow([name, round(statistics.median(x for x, _ in p), 6),
                        round(statistics.median(y for _, y in p), 6), len(p), osm_date])
    print(f"{len(pts)} road names from {sum(map(len, pts.values()))} segments -> {OUT}")


if __name__ == "__main__":
    main()
