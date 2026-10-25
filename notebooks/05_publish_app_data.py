# Databricks notebook source
# MAGIC %md
# MAGIC # 05 · Publish the app's data to Lakebase
# MAGIC The DengueRadar app (`app/`) reads from a Lakebase Postgres instance, not from the warehouse, so the
# MAGIC Monday screen loads instantly and works without a running SQL warehouse. This notebook copies what the app
# MAGIC shows from the gold tables into Postgres schema `dengueradar`:
# MAGIC
# MAGIC | Postgres table | From |
# MAGIC |---|---|
# MAGIC | `areas` | `gold_forecast_latest` + simplified planning-area shapes |
# MAGIC | `wolbachia_zones` | `silver_wolbachia_zones` (simplified shapes) |
# MAGIC | `high_aedes` | high-Aedes areas (centre points) |
# MAGIC | `clusters` | active clusters on the newest NEA list (`silver_clusters`) |
# MAGIC | `blocks`, `team_plan` | `gold_block_priority`, `gold_team_plan` |
# MAGIC | `track_record`, `test_headline`, `test_by_year`, `weekly_cases` | gold track record, test results, national weekly cases |
# MAGIC | `meta` | dates the screen shows |
# MAGIC
# MAGIC Each table is replaced inside one transaction, so the app never sees a half-written table.

# COMMAND ----------

# MAGIC %pip install -q -U "databricks-sdk>=0.60" "psycopg[binary]>=3.2"

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

dbutils.widgets.text("catalog", "workspace")
dbutils.widgets.text("schema", "dengue")
dbutils.widgets.text("lakebase_instance", "dengueradar")

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")
instance = dbutils.widgets.get("lakebase_instance")
T = f"{catalog}.{schema}"

# COMMAND ----------

QUERIES = {
    "areas": f"""
        SELECT f.planning_area, f.area, f.region, f.lon, f.lat, f.level_today, f.level_today_name, f.predicted_level,
               f.predicted_level_num, f.direction, f.p_low, f.p_medium, f.p_high, f.same_as_now_level,
               CAST(f.new_cluster_rank AS INT) AS new_cluster_rank, f.new_cluster_score, f.new_cluster_watch, f.why,
               f.n_clusters, f.recent_cases, f.largest_cluster, f.recent_next_door, f.wolbachia_share,
               st_asgeojson(st_simplify(st_geomfromgeojson(p.geometry_geojson), 0.0001)) AS geometry
        FROM {T}.gold_forecast_latest f JOIN {T}.silver_planning_areas p USING (planning_area)""",
    "wolbachia_zones": f"""
        SELECT zone, start_date, st_asgeojson(st_simplify(st_geomfromgeojson(geometry_geojson), 0.0001)) AS geometry
        FROM {T}.silver_wolbachia_zones""",
    "high_aedes": f"""
        SELECT st_x(c) AS lon, st_y(c) AS lat, name
        FROM (SELECT name, st_centroid(st_geomfromgeojson(geometry_geojson)) AS c FROM {T}.bronze_nea_high_aedes_areas_latest)""",
    "clusters": f"""
        SELECT snapshot_date, coalesce(locality, cluster_id) AS locality, planning_area, lon, lat,
               CAST(coalesce(total, recent) AS INT) AS total, CAST(recent AS INT) AS recent, alert
        FROM {T}.silver_clusters
        WHERE snapshot_date = (SELECT max(snapshot_date) FROM {T}.silver_clusters) AND is_active AND lon IS NOT NULL""",
    "blocks": f"""
        SELECT blk_no, street, planning_area, homes, lon, lat, x, y, weight, near_cluster, near_high_aedes,
               in_flagged_area, in_wolbachia, reason
        FROM {T}.gold_block_priority""",
    "team_plan": f"SELECT * FROM {T}.gold_team_plan ORDER BY team",
    "track_record": f"""
        SELECT forecast_id, basis_date, CAST(min(locked_at_utc) AS DATE) AS locked_on, min(score_from) AS score_from,
               min(score_until) AS score_until, max(status) AS status, max(outcome_date) AS outcome_date,
               count(*) AS areas, count_if(model_right) AS model_right, count_if(same_as_now_right) AS same_as_now_right,
               count_if(predicted_level = 'High' AND outcome_level = 'High') AS high_caught,
               count_if(outcome_level = 'High') AS high_outcomes,
               count_if(new_cluster_rank <= 5 AND new_cluster_appeared) AS new_clusters_in_top5,
               count_if(new_cluster_appeared) AS new_clusters, left(max(git_commit), 7) AS code
        FROM {T}.gold_track_record GROUP BY forecast_id, basis_date""",
    "test_headline": f"SELECT * FROM {T}.gold_model_test_headline",
    "test_by_year": f"SELECT * FROM {T}.gold_model_test_by_year",
    "weekly_cases": f"SELECT week_end, cases FROM {T}.gold_national_weekly WHERE week_end >= '2022-01-01'",
    "meta": f"""
        SELECT max(basis_date) AS basis_date, CAST(max(locked_at_utc) AS DATE) AS locked_on,
               max(score_from) AS score_from, max(score_until) AS score_until, max(forecast_id) AS forecast_id,
               (SELECT max(snapshot_date) FROM {T}.silver_clusters) AS cluster_list_date,
               current_timestamp() AS published_at
        FROM {T}.gold_forecast_latest""",
}
frames = {name: spark.sql(q).toPandas() for name, q in QUERIES.items()}
for name, d in frames.items():
    print(f"{name:16} {len(d):6} rows")

# COMMAND ----------

import uuid

import numpy as np
import pandas as pd
import psycopg
from databricks.sdk import WorkspaceClient

w = WorkspaceClient()
db = w.database.get_database_instance(name=instance)
cred = w.database.generate_database_credential(request_id=str(uuid.uuid4()), instance_names=[instance])
conn = psycopg.connect(host=db.read_write_dns, dbname="databricks_postgres", user=w.current_user.me().user_name,
                       password=cred.token, sslmode="require", autocommit=True)


def pg_type(s):
    k = s.dtype.kind
    if k == "b":
        return "boolean"
    if k in "iu":
        return "bigint"
    if k == "f":
        return "double precision"
    if k == "M":
        return "timestamp"
    v = s.dropna()
    if len(v) and all(hasattr(x, "isoformat") and not hasattr(x, "hour") for x in v.head(20)):
        return "date"
    if len(v) and v.map(lambda x: isinstance(x, (bool, np.bool_))).all():
        return "boolean"
    return "text"


def clean(v):
    if v is None or (isinstance(v, float) and np.isnan(v)) or v is pd.NaT:
        return None
    if isinstance(v, np.generic):
        return v.item()
    return v


with conn.transaction():
    conn.execute("CREATE SCHEMA IF NOT EXISTS dengueradar")
    conn.execute("GRANT USAGE ON SCHEMA dengueradar TO PUBLIC")
for name, d in frames.items():
    cols = ", ".join(f'"{c}" {pg_type(d[c])}' for c in d.columns)
    with conn.transaction():  # readers see the old table until this commits
        conn.execute(f"DROP TABLE IF EXISTS dengueradar.{name}")
        conn.execute(f"CREATE TABLE dengueradar.{name} ({cols})")
        with conn.cursor() as cur, cur.copy(f"COPY dengueradar.{name} FROM STDIN") as cp:
            for row in d.itertuples(index=False):
                cp.write_row([clean(v) for v in row])
        conn.execute(f"GRANT SELECT ON dengueradar.{name} TO PUBLIC")
    print(f"published dengueradar.{name}: {len(d)} rows")
conn.close()
