"""Bronze layer: Auto Loader from the landing Volume into Delta tables.

One table per source: <catalog>.<schema>.bronze_<source_name>. Tables are
append-only and every row carries the version it came from, so

    SELECT * FROM bronze_nea_dengue_clusters WHERE _version = '20261014T100643'

reproduces exactly what data.gov.sg published at that time. A companion view
bronze_<source_name>_latest keeps only the newest version of each dataset.
"""

import re

from pyspark.sql import functions as F

from .sources import Source, part_labels

# Path pieces written by landing.py: .../<source>/<dataset_id|file>/v=<version>/<file>
_DATASET_RE = r"/(d_[0-9a-f]+)/v="
_VERSION_RE = r"/v=([0-9T]+)/"
_SNAPSHOT_RE = r"(\d{6})-clusters\.csv$"  # SGCharts file names end YYMMDD-clusters.csv


def clean_name(name, taken):
    if name.startswith("_"):  # _rescued_data etc.
        out = name
    else:
        out = re.sub(r"[^0-9a-z]+", "_", name.lower()).strip("_") or "col"
        if out[0].isdigit():
            out = "c_" + out
    base, i = out, 2
    while out in taken:
        out, i = f"{base}_{i}", i + 1
    taken.add(out)
    return out


def clean_columns(df):
    taken = set()
    return df.toDF(*[clean_name(c, taken) for c in df.columns])


def reader(spark, src: Source, landing_root, schema_location, streaming=True):
    """Return a (streaming) DataFrame over every landed version of ``src``.

    streaming=False reads the same files in batch with plain Spark, which is
    how the pipeline is tested off-Databricks (Auto Loader is Databricks-only).
    """
    path = f"{landing_root}/{src.name}/*/v=*/"
    fmt = "json" if src.fmt == "geojson" else "csv"
    glob = "features.ndjson" if fmt == "json" else "*.csv"

    if streaming:
        r = (spark.readStream.format("cloudFiles")
             .option("cloudFiles.format", fmt)
             .option("cloudFiles.schemaLocation", schema_location)
             # v=<version> is not a partition column; we parse it from the path
             .option("cloudFiles.partitionColumns", ""))
        if src.csv_schema:
            r = r.schema(src.csv_schema)
        else:
            r = (r.option("cloudFiles.inferColumnTypes", "true")
                 .option("cloudFiles.schemaEvolutionMode", "addNewColumns"))
            if src.large:
                # Every file has the same header; don't scan ~18 GB to infer it
                r = r.option("cloudFiles.schemaInference.sampleSize.numFiles", "2")
    else:
        r = spark.read.format(fmt)
        r = r.schema(src.csv_schema) if src.csv_schema else r.option("inferSchema", "true")

    r = r.option("pathGlobFilter", glob)
    if fmt == "csv":
        r = r.option("header", str(src.csv_header).lower()).option("escape", '"')
    return r.load(path)


def shape(df, src: Source):
    """Flatten GeoJSON features, tidy column names, add lineage columns."""
    if src.fmt == "geojson":
        df = df.select("feature_index", "properties.*", "geometry_type", "geometry_geojson",
                       *[c for c in df.columns if c == "_rescued_data"], "_metadata")
    path = F.col("_metadata.file_path")
    df = df.withColumns({
        "_dataset_id": F.when(F.regexp_extract(path, _DATASET_RE, 1) != "",
                              F.regexp_extract(path, _DATASET_RE, 1)),
        "_version": F.regexp_extract(path, _VERSION_RE, 1),
        "_source_file": path,
        "_ingested_at": F.current_timestamp(),
    })
    df = df.withColumn("_version_ts_sgt", F.to_timestamp("_version", "yyyyMMdd'T'HHmmss"))
    labels = part_labels(src.name)
    if labels:
        m = F.create_map(*[F.lit(x) for kv in labels.items() for x in kv])
        df = df.withColumn("_part", m[F.col("_dataset_id")])
    if src.name.startswith("sgcharts_"):
        df = df.withColumns({
            "snapshot_date": F.to_date(F.regexp_extract(path, _SNAPSHOT_RE, 1), "yyMMdd"),
            "latlon_flagged_incorrect": path.contains("/flagged_latlon__"),
        })
    return clean_columns(df.drop("_metadata"))


def ingest(spark, src: Source, landing_root, catalog, schema, checkpoint_root):
    """Incrementally load new landed files for one source with Auto Loader."""
    table = f"{catalog}.{schema}.bronze_{src.name}"
    ckpt = f"{checkpoint_root}/{src.name}"
    for attempt in range(3):
        df = shape(reader(spark, src, landing_root, f"{ckpt}/schema"), src)
        q = (df.writeStream
             .option("checkpointLocation", f"{ckpt}/checkpoint")
             .option("mergeSchema", "true")
             .trigger(availableNow=True)
             .toTable(table))
        try:
            q.awaitTermination()
            break
        except Exception as e:
            # With addNewColumns, Auto Loader records the new column in the schema
            # location and stops once; restarting picks it up.
            if "UnknownFieldException" not in str(e) or attempt == 2:
                raise
    if src.description:
        spark.sql(f"COMMENT ON TABLE {table} IS {_sql_str(src.description)}")
    create_latest_view(spark, table)
    return table


def _sql_str(s):
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"


def create_latest_view(spark, table):
    spark.sql(f"""
        CREATE OR REPLACE VIEW {table}_latest AS
        SELECT * FROM {table}
        QUALIFY _version = max(_version) OVER (PARTITION BY coalesce(_dataset_id, ''))
    """)
