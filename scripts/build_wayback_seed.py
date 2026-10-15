"""Turn the prototype's parsed Internet Archive cluster rows into a repo seed file.

    python scripts/build_wayback_seed.py ~/Desktop/daisi-hackathon/dengue/outputs/wb_clusters.json

The input is one JSON list of clusters parsed from 74 Internet Archive captures of
NEA's dengue cluster page (Dec 2018 - Sep 2026, made by the prototype's wb_parse.py).
The output is newline-delimited JSON, one cluster per line, which Auto Loader reads
into bronze_nea_dengue_clusters_wayback. Street pairs become {street, cases} objects
so the table gets a clean array<struct> column.
"""

import argparse
import json
import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO, "data", "seed", "nea_dengue_clusters_wayback_2018_2026.ndjson")
PAGE = "https://www.nea.gov.sg/dengue-zika/dengue/dengue-clusters"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("wb_clusters_json")
    a = ap.parse_args()

    with open(os.path.expanduser(a.wb_clusters_json)) as f:
        rows = json.load(f)

    rows.sort(key=lambda r: (r["snap"], r["locality"]))
    with open(OUT, "w") as f:
        for r in rows:
            f.write(json.dumps({
                "snap": r["snap"],  # Internet Archive capture time, yyyyMMddHHmmss (UTC)
                "capture_url": f"https://web.archive.org/web/{r['snap']}/{PAGE}",
                "asat": r["asat"],  # "as at" date printed on NEA's page
                "asat_d": r["asat_d"][:10],
                "alert": r["alert"],  # red (>=10 cases) / yellow (<10)
                "locality": r["locality"],
                "recent": r["recent"],  # cases with onset in the last 2 weeks
                "total": r["total"],  # cases since the cluster started
                "street_sum": r["street_sum"],
                "streets": [{"street": s, "cases": n} for s, n in r["streets"]],
            }, ensure_ascii=False) + "\n")
    print(f"{len(rows)} clusters, {len({r['snap'] for r in rows})} captures -> {OUT}")


if __name__ == "__main__":
    main()
