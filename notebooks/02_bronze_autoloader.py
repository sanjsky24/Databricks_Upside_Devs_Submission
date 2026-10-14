# Databricks notebook source
# MAGIC %md
# MAGIC # 02 · Bronze Delta tables with Auto Loader
# MAGIC For each source, Auto Loader (`cloudFiles`) picks up any newly landed files, infers the schema
# MAGIC (CSV header / flattened GeoJSON properties) and appends to `bronze_<source>`.
# MAGIC
# MAGIC Every row carries lineage columns:
# MAGIC
# MAGIC | column | meaning |
# MAGIC |---|---|
# MAGIC | `_version`, `_version_ts_sgt` | the source version (data.gov.sg `lastUpdatedAt`, or landing time for non-data.gov.sg sources) |
# MAGIC | `_dataset_id` | data.gov.sg dataset id |
# MAGIC | `_part` | region / year for tables built from several datasets |
# MAGIC | `_source_file`, `_ingested_at` | file in the landing Volume and load time |
# MAGIC | `_rescued_data` | values that did not fit the inferred schema |
# MAGIC
# MAGIC `bronze_<source>_latest` views show only the newest version of each dataset.

# COMMAND ----------

dbutils.widgets.text("catalog", "workspace")
dbutils.widgets.text("schema", "dengue")
dbutils.widgets.text("only_sources", "", "Only these sources (comma-separated, blank = all)")
dbutils.widgets.dropdown("include_large", "false", ["false", "true"], "Include ~18 GB station history")

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")
only = [s.strip() for s in dbutils.widgets.get("only_sources").split(",") if s.strip()] or None
include_large = dbutils.widgets.get("include_large") == "true"

landing_root = f"/Volumes/{catalog}/{schema}/landing"
checkpoint_root = f"/Volumes/{catalog}/{schema}/checkpoints"

# COMMAND ----------

import os
import sys

sys.path.insert(0, os.path.abspath("../src"))

from dengue_ingest.bronze import ingest
from dengue_ingest.sources import select, table_names

# COMMAND ----------

failed = {}
for src in table_names(select(names=only, include_large=include_large)):
    if not os.path.isdir(f"{landing_root}/{src.name}"):
        print(f"skip     {src.name} (nothing landed yet)")
        continue
    try:
        table = ingest(spark, src, landing_root, catalog, schema, checkpoint_root)
        n = spark.table(table).count()
        print(f"ok       {table:70} {n:>10,} rows")
    except Exception as e:
        failed[src.name] = str(e)[:500]
        print(f"FAILED   {src.name}: {failed[src.name]}")

if failed:
    raise RuntimeError(f"bronze failed for: {list(failed)}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## What landed

# COMMAND ----------

display(spark.sql(f"""
  SELECT table_name, comment
  FROM {catalog}.information_schema.tables
  WHERE table_schema = '{schema}' AND table_name LIKE 'bronze_%'
  ORDER BY table_name
"""))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Version history example – NEA dengue clusters
# MAGIC Each `_version` is one publication of the dataset on data.gov.sg. The table grows by one
# MAGIC version every day NEA updates the clusters (as long as the job runs daily).

# COMMAND ----------

display(spark.sql(f"""
  SELECT _version_ts_sgt AS published_sgt,
         count(*)        AS clusters,
         sum(case_size)  AS total_cases
  FROM {catalog}.{schema}.bronze_nea_dengue_clusters
  GROUP BY 1 ORDER BY 1 DESC
"""))
