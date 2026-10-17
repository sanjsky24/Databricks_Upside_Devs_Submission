"""Parse NEA's dengue cluster web page into one record per cluster.

https://www.nea.gov.sg/dengue-zika/dengue/dengue-clusters lists every active cluster
with its alert colour, cases with onset in the last 2 weeks, cases since the cluster
started, and a street-by-street case breakdown. The data.gov.sg layer only has the
total, so this page is what gives live data the same fields as the Internet Archive
captures the model is trained on.

Records have the same shape as data/seed/nea_dengue_clusters_wayback_2018_2026.ndjson
(see scripts/build_wayback_seed.py). The parsing logic is the prototype's wb_parse.py,
which handled every page layout from 2018 to 2026.
"""

import gzip
import html
import re
from datetime import datetime

PAGE_URL = "https://www.nea.gov.sg/dengue-zika/dengue/dengue-clusters"


def _clean(x):
    x = re.sub(r"<(?!br)[^>]+>", "", x)
    x = re.sub(r"<br\s*/?>", " ", x)
    return html.unescape(re.sub(r"\s+", " ", x)).strip()


def decode(raw: bytes) -> str:
    if raw[:2] == b"\x1f\x8b":  # some captures arrive gzip-compressed
        raw = gzip.decompress(raw)
    return raw.decode("utf-8", "ignore")


def parse(page: str, snap: str, capture_url: str = PAGE_URL):
    """Return (as_at_date or None, [cluster records]). ``snap`` is the fetch time, yyyyMMddHHmmss UTC."""
    m = re.search(r"accurate as at\s*([0-9]{1,2} \w+ \d{4})", page)
    asat = m.group(1) if m else None
    asat_d = datetime.strptime(asat, "%d %b %Y").date().isoformat() if asat else None

    rows = []
    parts = re.split(r"<a onclick='zoomLocation\(", page)
    for k in range(1, len(parts)):
        prev, seg = parts[k - 1], parts[k]
        alerts = re.findall(r'<div class="(red|yellow|green)"', prev[-800:])
        loc = re.search(r'href="#onemap">(.*?)</a>', seg, re.S)
        if not loc:
            continue
        tds = [_clean(t) for t in re.findall(r"<td[^>]*>(.*?)</td>", seg[loc.end():], re.S)]
        # Cells after the locality: (street, cases) pairs from the breakdown, then
        # recent and total. Trailing cells belong to the next row and are ignored.
        streets, nums, i = [], [], 0
        while i < len(tds):
            t = tds[i]
            if re.fullmatch(r"\d+", t):
                nums.append(int(t))
                i += 1
            elif i + 1 < len(tds) and t and re.fullmatch(r"\d+", tds[i + 1]):
                streets.append({"street": t, "cases": int(tds[i + 1])})
                i += 2
            else:
                i += 1
        rows.append({
            "snap": snap,
            "capture_url": capture_url,
            "asat": asat,
            "asat_d": asat_d,
            "alert": alerts[-1] if alerts else None,
            "locality": _clean(loc.group(1)),
            "recent": nums[0] if nums else None,
            "total": nums[1] if len(nums) > 1 else None,
            "street_sum": sum(s["cases"] for s in streets),
            "streets": streets,
        })
    return asat_d, rows
