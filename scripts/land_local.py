"""Run the landing step on your laptop (no Databricks needed).

    python scripts/land_local.py --root ./_landing
    python scripts/land_local.py --root ./_landing --include-large
    python scripts/land_local.py --root ./_landing --only nea_dengue_clusters
"""

import argparse
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "src"))

from dengue_ingest.landing import Lander  # noqa: E402
from dengue_ingest.sources import select  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--group", nargs="*")
    ap.add_argument("--include-large", action="store_true")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    lander = Lander(a.root, repo_root=REPO, api_key=os.environ.get("DATAGOV_API_KEY"))
    failed = 0
    for s in select(groups=a.group, names=a.only, include_large=a.include_large):
        try:
            r = lander.land(s, force=a.force)
            print(f"{r['status']:9} {s.name:40} {s.dataset_id or ''} v={r.get('version')}")
        except Exception as e:  # keep going; report at the end
            failed += 1
            print(f"FAILED    {s.name:40} {s.dataset_id or ''} {e}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
