"""Land raw source files into a directory (a Unity Catalog Volume on Databricks,
any local folder when testing).

Layout - every version of every source is kept, nothing is overwritten:

    <root>/<source_name>/<dataset_id or "file">/v=<version>/<files>
    <root>/<source_name>/<dataset_id or "file">/_latest.json

<version> is a Singapore-time timestamp (yyyyMMddTHHmmss). For data.gov.sg
datasets it is the dataset's lastUpdatedAt, so each run
only lands a new folder when NEA/URA/HDB actually publish a change. That is what
gives us a version history: data.gov.sg itself only serves the current version.

GeoJSON is also flattened to newline-delimited JSON (one feature per line,
geometry kept as a GeoJSON string) so Auto Loader can infer a flat schema.
"""

import fnmatch
import hashlib
import html
import io
import json
import os
import re
import shutil
import time
import zipfile
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests

from . import nea_page
from .sources import Source

META_URL = "https://api-production.data.gov.sg/v2/public/api/datasets/{id}/metadata"
INITIATE_URL = "https://api-open.data.gov.sg/v1/public/api/datasets/{id}/initiate-download"
POLL_URL = "https://api-open.data.gov.sg/v1/public/api/datasets/{id}/poll-download"

RATE_LIMITED = 24  # data.gov.sg "TOO_MANY_REQUESTS" code
SGT = ZoneInfo("Asia/Singapore")


class Lander:
    def __init__(self, root, repo_root=".", api_key=None, log=print):
        self.root = root.rstrip("/")
        self.repo_root = repo_root
        self.log = log
        self.http = requests.Session()
        self.http.headers["User-Agent"] = "upside-devs-dengue-ingest/1.0"
        if api_key:
            self.http.headers["x-api-key"] = api_key

    # ------------------------------------------------------------------ http
    def _get_json(self, url, attempts=8):
        for i in range(attempts):
            r = self.http.get(url, timeout=60)
            if r.status_code == 429:
                time.sleep(11)
                continue
            r.raise_for_status()
            body = r.json()
            if body.get("code") == RATE_LIMITED:
                # Anonymous limit is a handful of calls per 10s window
                time.sleep(11)
                continue
            return body
        raise RuntimeError(f"rate limited too many times: {url}")

    def _download(self, url):
        for i in range(5):
            try:
                r = self.http.get(url, timeout=600)
                r.raise_for_status()
                return r.content
            except requests.RequestException as e:
                if i == 4:
                    raise
                self.log(f"  retry download ({e})")
                time.sleep(5 * (i + 1))

    def _download_to(self, url, path):
        """Stream to disk (station history files are ~1 GB each); return sha256."""
        for i in range(5):
            try:
                h = hashlib.sha256()
                with self.http.get(url, timeout=600, stream=True) as r:
                    r.raise_for_status()
                    with open(path, "wb") as f:
                        for chunk in r.iter_content(chunk_size=8 << 20):
                            h.update(chunk)
                            f.write(chunk)
                return h.hexdigest()
            except requests.RequestException as e:
                if i == 4:
                    raise
                self.log(f"  retry download ({e})")
                time.sleep(5 * (i + 1))

    def _datagov_file_url(self, dataset_id):
        body = self._get_json(POLL_URL.format(id=dataset_id))
        if (body.get("data") or {}).get("url"):
            return body["data"]["url"]
        # Not prepared yet: ask data.gov.sg to build the file, then poll for it
        self._get_json(INITIATE_URL.format(id=dataset_id))
        for _ in range(30):
            time.sleep(3)
            body = self._get_json(POLL_URL.format(id=dataset_id))
            data = body.get("data") or {}
            if data.get("url"):
                return data["url"]
            if data.get("status") == "DOWNLOAD_FAILED":
                break
        raise RuntimeError(f"data.gov.sg could not prepare {dataset_id}: {body}")

    # ----------------------------------------------------------- bookkeeping
    def _dir(self, src, key):
        return f"{self.root}/{src.name}/{key}"

    def _latest(self, src, key):
        p = f"{self._dir(src, key)}/_latest.json"
        if os.path.exists(p):
            with open(p) as f:
                return json.load(f)
        return None

    def _staging(self, src, key):
        tmp = f"{self._dir(src, key)}/_staging"
        shutil.rmtree(tmp, ignore_errors=True)
        os.makedirs(tmp, exist_ok=True)
        return tmp

    def _commit(self, src, key, version, files, meta, staged=None):
        """Write files into v=<version>/ then point _latest.json at it.

        ``files`` maps file name -> bytes; ``staged`` is a directory already
        holding (some of) the files, which is renamed into place.
        """
        vdir = f"{self._dir(src, key)}/v={version}"
        tmp = staged or self._staging(src, key)
        for name, content in files.items():
            with open(f"{tmp}/{name}", "wb") as f:
                f.write(content)
        landed_at = datetime.now(timezone.utc).isoformat()
        record = {**meta, "source": src.name, "version": version, "landed_at": landed_at,
                  "files": sorted(os.listdir(tmp))}
        with open(f"{tmp}/manifest.json", "w") as f:
            json.dump(record, f, indent=2)
        # Volumes are object storage underneath: move file by file rather than
        # renaming the directory. _latest.json is written last as the commit marker.
        shutil.rmtree(vdir, ignore_errors=True)
        os.makedirs(vdir)
        for name in os.listdir(tmp):
            shutil.move(f"{tmp}/{name}", f"{vdir}/{name}")
        shutil.rmtree(tmp, ignore_errors=True)
        with open(f"{self._dir(src, key)}/_latest.json", "w") as f:
            json.dump(record, f, indent=2)
        return record

    # --------------------------------------------------------------- sources
    def land(self, src: Source, force=False):
        handler = {
            "datagov": self._land_datagov,
            "http_zip": self._land_zip,
            "http_file": self._land_file,
            "seed": self._land_seed,
            "nea_page": self._land_nea_page,
        }[src.kind]
        return handler(src, force)

    def _land_datagov(self, src, force):
        meta = self._get_json(META_URL.format(id=src.dataset_id))["data"]
        version = _compact_ts(meta["lastUpdatedAt"])
        prev = self._latest(src, src.dataset_id)
        if prev and prev["version"] == version and not force:
            return {"source": src.name, "dataset_id": src.dataset_id, "status": "unchanged", "version": version}

        tmp = self._staging(src, src.dataset_id)
        data_file = f"{tmp}/data.{'geojson' if src.fmt == 'geojson' else 'csv'}"
        sha = self._download_to(self._datagov_file_url(src.dataset_id), data_file)
        size = os.path.getsize(data_file)
        if prev and prev.get("sha256") == sha and not force:
            # Metadata timestamp moved but the bytes didn't: nothing new to keep
            shutil.rmtree(tmp, ignore_errors=True)
            return {"source": src.name, "dataset_id": src.dataset_id, "status": "unchanged", "version": prev["version"]}

        files = {}
        if src.fmt == "geojson":
            with open(data_file, "rb") as f:
                files["features.ndjson"] = geojson_to_ndjson(f.read())
        rec = self._commit(src, src.dataset_id, version, files, staged=tmp, meta={
            "dataset_id": src.dataset_id,
            "dataset_name": meta.get("name"),
            "managed_by": meta.get("managedBy"),
            "last_updated_at": meta.get("lastUpdatedAt"),
            "coverage_start": meta.get("coverageStart"),
            "coverage_end": meta.get("coverageEnd"),
            "sha256": sha,
            "bytes": size,
        })
        return {**rec, "status": "landed"}

    def _land_url(self, src, force, to_files):
        prev = self._latest(src, "file")
        if prev and not force:
            # sgcharts and Figshare are frozen archives: fetch once, then never again, so an
            # outage on their side (Figshare returned HTTP 500 on 19 Oct 2026) can't stop the daily run
            return {"source": src.name, "status": "unchanged", "version": prev["version"]}
        raw = self._download(src.url)
        sha = hashlib.sha256(raw).hexdigest()
        if prev and prev.get("sha256") == sha and not force:
            return {"source": src.name, "status": "unchanged", "version": prev["version"]}
        version = datetime.now(SGT).strftime("%Y%m%dT%H%M%S")
        rec = self._commit(src, "file", version, to_files(raw),
                           {"url": src.url, "sha256": sha, "bytes": len(raw)})
        return {**rec, "status": "landed"}

    def _land_zip(self, src, force):
        def members(raw):
            out = {}
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                for n in z.namelist():
                    for pattern, prefix in src.member_globs:
                        if fnmatch.fnmatch(n, pattern):
                            out[prefix + os.path.basename(n)] = z.read(n)
            if not out:
                raise RuntimeError(f"no archive members matched {src.member_globs}")
            return out
        return self._land_url(src, force, members)

    def _land_file(self, src, force):
        return self._land_url(src, force, lambda raw: {"data.csv": raw})

    def _land_nea_page(self, src, force):
        """Capture NEA's cluster page; keep a version only when the cluster list changes."""
        raw = self._download(src.url)
        snap = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        asat_d, rows = nea_page.parse(nea_page.decode(raw), snap)
        if asat_d is None:
            raise RuntimeError("NEA cluster page: no 'accurate as at' date found; the page layout may have changed")
        # Hash the parsed list without the fetch time, so an unchanged list isn't stored twice
        sha = hashlib.sha256(json.dumps([{k: v for k, v in r.items() if k != "snap"} for r in rows],
                                        sort_keys=True).encode()).hexdigest()
        prev = self._latest(src, "page")
        if prev and prev.get("sha256") == sha and not force:
            return {"source": src.name, "status": "unchanged", "version": prev["version"]}
        version = datetime.now(SGT).strftime("%Y%m%dT%H%M%S")
        ndjson = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows).encode("utf-8")
        rec = self._commit(src, "page", version, {"data.ndjson": ndjson, "page.html": raw}, {
            "url": src.url, "as_at": asat_d, "clusters": len(rows), "sha256": sha, "bytes": len(raw)})
        return {**rec, "status": "landed"}

    def _land_seed(self, src, force):
        path = os.path.join(self.repo_root, src.url)
        with open(path, "rb") as f:
            raw = f.read()
        sha = hashlib.sha256(raw).hexdigest()
        prev = self._latest(src, "file")
        if prev and prev.get("sha256") == sha and not force:
            return {"source": src.name, "status": "unchanged", "version": prev["version"]}
        version = datetime.now(SGT).strftime("%Y%m%dT%H%M%S")
        name = "data.ndjson" if src.fmt == "ndjson" else "data.csv"
        rec = self._commit(src, "file", version, {name: raw},
                           {"repo_path": src.url, "sha256": sha, "bytes": len(raw)})
        return {**rec, "status": "landed"}


# ---------------------------------------------------------------------- helpers
def _compact_ts(iso):
    # "2026-10-14T10:06:43+08:00" -> "20261014T100643" (SGT, as published)
    return re.sub(r"[-:]", "", iso)[:15]


_DESC_ROW = re.compile(r"<th>(.*?)</th>\s*<td>(.*?)</td>", re.S | re.I)


def parse_description(desc):
    """data.gov.sg GeoJSON converted from KML hides attributes in an HTML table."""
    if not desc or "<t" not in desc:
        return {}
    return {html.unescape(k).strip(): html.unescape(v).strip() for k, v in _DESC_ROW.findall(desc)}


def geojson_to_ndjson(raw: bytes) -> bytes:
    fc = json.loads(raw)
    lines = []
    for i, feat in enumerate(fc.get("features", [])):
        props = dict(feat.get("properties") or {})
        desc = props.pop("Description", None)
        for k, v in parse_description(desc).items():
            props.setdefault(k, v)
        geom = feat.get("geometry")
        row = {
            "feature_index": i,
            "properties": props,
            "geometry_type": geom.get("type") if geom else None,
            "geometry_geojson": json.dumps(geom, separators=(",", ":")) if geom else None,
        }
        lines.append(json.dumps(row, ensure_ascii=False))
    return ("\n".join(lines) + "\n").encode("utf-8")
