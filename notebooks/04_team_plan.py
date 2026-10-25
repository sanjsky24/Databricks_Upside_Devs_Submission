# Databricks notebook source
# MAGIC %md
# MAGIC # 04 · Team planner
# MAGIC Turns this week's priority HDB blocks (`gold_block_priority`: near a cluster on the newest NEA list, near a
# MAGIC high-Aedes area, or in an area the newest locked forecast flags; halved inside Wolbachia zones) into one
# MAGIC inspection site per team. Sites don't overlap: each team covers the blocks within `radius_m` of its anchor block.
# MAGIC
# MAGIC Writes (overwritten every run):
# MAGIC * `gold_team_plan` – one row per team
# MAGIC * `gold_team_plan_blocks` – every block a team covers
# MAGIC * `gold_team_plan_summary` – homes reached by the plan vs the simple plan (top-weight blocks, spread out so sites don't overlap)

# COMMAND ----------

dbutils.widgets.text("catalog", "workspace")
dbutils.widgets.text("schema", "dengue")
dbutils.widgets.text("teams", "6")
dbutils.widgets.text("radius_m", "300")

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")
teams = int(dbutils.widgets.get("teams"))
radius = float(dbutils.widgets.get("radius_m"))

# COMMAND ----------

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath("../src"))
from dengue_model.planner import plan

blocks = spark.table(f"{catalog}.{schema}.gold_block_priority").toPandas()
list_date = blocks.cluster_list_date.max()
basis = spark.sql(f"SELECT max(basis_date) FROM {catalog}.{schema}.gold_forecast_latest").first()[0]
sites, assigned, summary = plan(blocks, teams=teams, radius=radius)

made = datetime.now(timezone.utc).replace(tzinfo=None)
stamp = {"cluster_list_date": list_date, "forecast_basis_date": basis, "made_at_utc": made}
sites, assigned, summary = (d.assign(**stamp) for d in (sites, assigned, summary))
assigned = assigned[["team", "postal_code", "blk_no", "street", "planning_area", "homes", "lon", "lat",
                     "near_cluster", "near_high_aedes", "in_flagged_area", "in_wolbachia", "weight", "reason",
                     *stamp]]

for name, d, comment in [
    ("gold_team_plan", sites, "This week's inspection plan: one non-overlapping site per team (anchor block + blocks within radius_m)."),
    ("gold_team_plan_blocks", assigned, "Every HDB block covered by this week's team plan, with the team and why it is a priority."),
    ("gold_team_plan_summary", summary, "Priority homes reached by the team plan vs the simple plan (each team to the highest-weight block at least 2 x radius from the others)."),
]:
    (spark.createDataFrame(d).write.mode("overwrite").option("overwriteSchema", "true")
     .saveAsTable(f"{catalog}.{schema}.{name}"))
    spark.sql(f"COMMENT ON TABLE {catalog}.{schema}.{name} IS '{comment}'")

display(sites)
display(summary)
