"""Add more Internet Archive captures of NEA's cluster page to the wayback seed.

    python scripts/add_wayback_captures.py 20240611131457 20240915150933 ...

The seed came from captures of one exact URL. The archive also holds captures saved
under other spellings of it (a trailing backslash, ?ref=..., email tracking links),
which are the same page. For each timestamp this script looks up the original URL in
the archive's index, downloads the raw capture, parses the cluster table (same rules as
the prototype's wb_parse.py) and appends one row per cluster to the seed. Timestamps
already in the seed are skipped.
"""

import argparse
import gzip
import html
import json
import os
import re
import time
from datetime import datetime

import requests

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEED = os.path.join(REPO, "data", "seed", "nea_dengue_clusters_wayback_2018_2026.ndjson")
CDX = "https://web.archive.org/cdx/search/cdx"
PAGE = "nea.gov.sg/dengue-zika/dengue/dengue-clusters"


def get(url, **kw):
    """The archive often times out (502/504); try four times, waiting longer each time."""
    for attempt in range(4):
        try:
            r = requests.get(url, timeout=180, **kw)
            r.raise_for_status()
            return r
        except requests.RequestException:
            if attempt == 3:
                raise
            time.sleep(15 * (attempt + 1))


def original_url(ts):
    r = get(CDX, params={"url": PAGE, "matchType": "prefix", "from": ts, "to": ts,
                         "fl": "timestamp,original", "filter": "statuscode:200"})
    for line in r.text.splitlines():
        t, url = line.split()[:2]
        if t == ts and "under-surveillance" not in url:
            return "https:" + url if url.startswith("//") else url
    raise SystemExit(f"{ts}: no 200 capture of the cluster page in the archive index")


def clean(x):
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<br\s*/?>", " ", re.sub(r"<(?!br)[^>]+>", "", x)))).strip()


def parse(s):
    """(as-at text, clusters) from one capture of the page."""
    m = re.search(r"accurate as at\s*([0-9]{1,2} \w+ \d{4})", s)
    asat = m.group(1) if m else None
    parts = re.split(r"<a onclick='zoomLocation\(", s)
    clusters = []
    for k in range(1, len(parts)):
        alert = re.findall(r'<div class="(red|yellow|green)"', parts[k - 1][-800:])
        loc = re.search(r'href="#onemap">(.*?)</a>', parts[k], re.S)
        if not loc:
            continue
        tds = [clean(t) for t in re.findall(r"<td[^>]*>(.*?)</td>", parts[k][loc.end():], re.S)]
        streets, nums, i = [], [], 0
        while i < len(tds):
            t = tds[i]
            if re.fullmatch(r"\d+", t):
                nums.append(int(t))
                i += 1
            elif i + 1 < len(tds) and re.fullmatch(r"\d+", tds[i + 1]) and t:
                streets.append({"street": t, "cases": int(tds[i + 1])})
                i += 2
            else:
                i += 1
        clusters.append({"alert": alert[-1] if alert else None, "locality": clean(loc.group(1)),
                         "recent": nums[0] if nums else None, "total": nums[1] if len(nums) > 1 else None,
                         "street_sum": sum(x["cases"] for x in streets), "streets": streets})
    return asat, clusters


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("timestamps", nargs="+")
    a = ap.parse_args()

    with open(SEED) as f:
        rows = [json.loads(line) for line in f]
    have = {r["snap"] for r in rows}

    for ts in a.timestamps:
        if ts in have:
            print(f"{ts}: already in the seed")
            continue
        url = original_url(ts)
        capture = f"https://web.archive.org/web/{ts}/{url}"
        b = get(f"https://web.archive.org/web/{ts}id_/{url}").content
        if b[:2] == b"\x1f\x8b":
            b = gzip.decompress(b)
        asat, clusters = parse(b.decode("utf-8", "ignore"))
        if not asat or not clusters:
            raise SystemExit(f"{ts}: could not find the as-at date or any clusters in {capture}")
        asat_d = datetime.strptime(asat, "%d %b %Y").date().isoformat()
        rows += [{"snap": ts, "capture_url": capture, "asat": asat, "asat_d": asat_d, **c} for c in clusters]
        print(f"{ts}: {len(clusters)} clusters, as at {asat_d}, {url}")

    rows.sort(key=lambda r: (r["snap"], r["locality"]))
    with open(SEED, "w") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"{len(rows)} clusters, {len({r['snap'] for r in rows})} captures -> {SEED}")


if __name__ == "__main__":
    main()
