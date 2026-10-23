# Databricks notebook source
# MAGIC %md
# MAGIC # 01 · Land raw files
# MAGIC Downloads every source in `src/dengue_ingest/sources.py` into the `landing` Volume:
# MAGIC
# MAGIC | Source | How |
# MAGIC |---|---|
# MAGIC | data.gov.sg (NEA, MOH, HDB, URA, weather) | metadata API → `poll-download` / `initiate-download`; a new folder is landed only when `lastUpdatedAt` changes |
# MAGIC | SGCharts Outbreak 2013–2020 | `sgcharts.zip` → one CSV per snapshot |
# MAGIC | Scientific Data 2020 clusters | figshare file |
# MAGIC | MSS daily weather | one CSV per station per month from weather.gov.sg, landed one folder per month |
# MAGIC | Wolbachia start dates | `data/seed/wolbachia_release_start_dates.csv` from this repo |
# MAGIC
# MAGIC data.gov.sg only serves the *current* version of a dataset, so running this on a schedule is what
# MAGIC builds the version history. Every attempt is logged to the `landing_log` table.
# MAGIC
# MAGIC Optional: put a data.gov.sg API key in secret scope `datagov`, key `api_key`, for higher rate limits.

# COMMAND ----------

dbutils.widgets.text("catalog", "workspace")
dbutils.widgets.text("schema", "dengue")
dbutils.widgets.text("only_sources", "", "Only these sources (comma-separated, blank = all)")
dbutils.widgets.dropdown("include_large", "false", ["false", "true"], "Include ~18 GB station history")
dbutils.widgets.dropdown("force", "false", ["false", "true"])

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")
only = [s.strip() for s in dbutils.widgets.get("only_sources").split(",") if s.strip()] or None
include_large = dbutils.widgets.get("include_large") == "true"
force = dbutils.widgets.get("force") == "true"

landing_root = f"/Volumes/{catalog}/{schema}/landing"

# COMMAND ----------

import os
import sys

repo_root = os.path.abspath("..")
sys.path.insert(0, os.path.join(repo_root, "src"))

from dengue_ingest.landing import Lander
from dengue_ingest.sources import select

try:
    api_key = dbutils.secrets.get("datagov", "api_key")
except Exception:
    api_key = None

lander = Lander(landing_root, repo_root=repo_root, api_key=api_key)

# COMMAND ----------

from datetime import datetime, timezone

run_at = datetime.now(timezone.utc)
results = []
for src in select(names=only, include_large=include_large):
    try:
        r = lander.land(src, force=force)
        err = None
    except Exception as e:
        r, err = {"status": "failed"}, f"{type(e).__name__}: {e}"
    print(f"{r['status']:9} {src.name:40} {src.dataset_id or ''} {r.get('version') or err}")
    results.append({
        "run_at": run_at,
        "source": src.name,
        "dataset_id": src.dataset_id,
        "status": r["status"],
        "version": r.get("version"),
        "last_updated_at": r.get("last_updated_at"),
        "sha256": r.get("sha256"),
        "bytes": r.get("bytes"),
        "error": err,
    })

# COMMAND ----------

log_schema = ("run_at TIMESTAMP, source STRING, dataset_id STRING, status STRING, version STRING, "
              "last_updated_at STRING, sha256 STRING, bytes BIGINT, error STRING")
(spark.createDataFrame(results, log_schema)
 .write.mode("append").saveAsTable(f"{catalog}.{schema}.landing_log"))

display(spark.table(f"{catalog}.{schema}.landing_log").filter(f"run_at = '{run_at.isoformat()}'"))

failed = [r["source"] for r in results if r["status"] == "failed"]
if failed:
    raise RuntimeError(f"{len(failed)} source(s) failed to land: {failed}")
