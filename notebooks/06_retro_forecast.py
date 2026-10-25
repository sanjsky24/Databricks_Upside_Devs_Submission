# Databricks notebook source
# MAGIC %md
# MAGIC # 06 · The 14 Oct forecast, checked against NEA today
# MAGIC Our own daily captures start on 15 Oct, so there is no locked forecast from before then. This notebook makes
# MAGIC one **afterwards**, as it would have been made on the basis date (default: NEA's list as at 14 Oct, from the
# MAGIC Internet Archive), and follows how it is doing:
# MAGIC
# MAGIC * the models are fitted only on lists whose outcome was known by the basis date (`next_date <= basis`),
# MAGIC   with the feature sets and thresholds chosen on the 2017–19 backtest, as in `03_lock_forecast`;
# MAGIC * it is **not** written to `gold_sealed_forecasts`: it was made after the fact, so it is labelled as such;
# MAGIC * it is made once per basis date (`gold_retro_forecast`); every run then compares it with NEA's newest list
# MAGIC   (`gold_retro_check`), and switches to the proper two-week check once NEA publishes a list 14–35 days later.

# COMMAND ----------

# MAGIC %pip install -q scikit-learn

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

dbutils.widgets.text("catalog", "workspace")
dbutils.widgets.text("schema", "dengue")
dbutils.widgets.text("basis_date", "2026-10-14")
dbutils.widgets.text("git_commit", "")

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")
basis_date = dbutils.widgets.get("basis_date")
git_commit = dbutils.widgets.get("git_commit")
T = f"{catalog}.{schema}"

# COMMAND ----------

import os
import sys
from datetime import datetime, timezone

import pandas as pd

sys.path.insert(0, os.path.abspath("../src"))
from dengue_model.forecast import (LMH_SETS, NEW_SETS, choose_lmh, choose_new_set, development, fit_lmh, fit_new,
                                   forecast, labelled)

made = spark.catalog.tableExists(f"{T}.gold_retro_forecast") and spark.sql(
    f"SELECT count(*) FROM {T}.gold_retro_forecast WHERE basis_date = DATE'{basis_date}'").first()[0] > 0

if not made:
    df = spark.table(f"{T}.gold_area_fortnight").toPandas()
    df["snapshot_date"] = pd.to_datetime(df.snapshot_date)
    basis = pd.Timestamp(basis_date)
    if basis not in set(df.snapshot_date):
        raise ValueError(f"no NEA list dated {basis_date} in gold_area_fortnight")
    lab = labelled(df)
    lmh_set, t_high, t_any, _ = choose_lmh(development(lab))  # choices from the 2017-19 backtest, as in 03
    new_set, _ = choose_new_set(development(lab))
    known = lab[lab.next_date <= basis]  # outcomes NEA had published by the basis date
    lmh_model, new_model = fit_lmh(known, LMH_SETS[lmh_set]), fit_new(known, NEW_SETS[new_set])
    _, fc = forecast(df[df.snapshot_date <= basis], lmh_model, LMH_SETS[lmh_set], t_high, t_any,
                     new_model, NEW_SETS[new_set])
    fc = fc.assign(basis_date=basis.date(), made_at_utc=datetime.now(timezone.utc).replace(tzinfo=None),
                   trained_on_outcomes_until=known.next_date.max().date(), training_rows=len(known),
                   risk_level_features=lmh_set, high_threshold=t_high, medium_threshold=t_any,
                   new_cluster_features=new_set, git_commit=git_commit)
    (spark.createDataFrame(fc).write.mode("overwrite").option("overwriteSchema", "true")
     .saveAsTable(f"{T}.gold_retro_forecast"))
    spark.sql(f"COMMENT ON TABLE {T}.gold_retro_forecast IS 'A forecast made afterwards from the NEA list of "
              f"{basis_date}, using only outcomes known by then. Not a locked forecast.'")
    print(f"made the {basis_date} forecast from {len(known):,} rows known by then")
else:
    print(f"the {basis_date} forecast already exists; comparing it with NEA's newest list")

# COMMAND ----------

# Compare with NEA's newest list, or with the first list 14-35 days later once it exists (the proper check)
spark.sql(f"""
CREATE OR REPLACE TABLE {T}.gold_retro_check
COMMENT 'The forecast made afterwards from the NEA list of the basis date, next to the newest NEA list (final = false) or, once it exists, the first list 14-35 days later (final = true).'
AS
WITH f AS (SELECT * FROM {T}.gold_retro_forecast WHERE basis_date = DATE'{basis_date}'),
later AS (SELECT DISTINCT snapshot_date FROM {T}.gold_area_levels WHERE snapshot_date > DATE'{basis_date}'),
pick AS (
  SELECT coalesce(
           (SELECT min(snapshot_date) FROM later
            WHERE snapshot_date BETWEEN date_add(DATE'{basis_date}', 14) AND date_add(DATE'{basis_date}', 35)),
           (SELECT max(snapshot_date) FROM later)) AS compare_date,
         (SELECT min(snapshot_date) FROM later
          WHERE snapshot_date BETWEEN date_add(DATE'{basis_date}', 14) AND date_add(DATE'{basis_date}', 35)) IS NOT NULL AS final
)
SELECT
  f.planning_area, initcap(f.planning_area) AS area, f.basis_date, f.made_at_utc, f.trained_on_outcomes_until,
  CASE f.level_today WHEN 2 THEN 'High' WHEN 1 THEN 'Medium' ELSE 'Low' END AS level_then,
  f.predicted_level, f.same_as_now_level, f.p_high, CAST(f.new_cluster_rank AS INT) AS new_cluster_rank,
  p.compare_date, p.final, datediff(p.compare_date, f.basis_date) AS days_later,
  date_add(f.basis_date, 14) AS final_check_from,
  CASE lv.level WHEN 2 THEN 'High' WHEN 1 THEN 'Medium' ELSE 'Low' END AS level_now,
  lv.n_clusters AS clusters_now,
  f.predicted_level = CASE lv.level WHEN 2 THEN 'High' WHEN 1 THEN 'Medium' ELSE 'Low' END AS model_right,
  f.same_as_now_level = CASE lv.level WHEN 2 THEN 'High' WHEN 1 THEN 'Medium' ELSE 'Low' END AS same_as_now_right,
  CASE WHEN f.level_today = 0 THEN lv.level > 0 END AS new_cluster_appeared
FROM f CROSS JOIN pick p
LEFT JOIN {T}.gold_area_levels lv ON lv.snapshot_date = p.compare_date AND lv.planning_area = f.planning_area
""")
display(spark.sql(f"""
  SELECT compare_date, final, days_later, count(*) AS areas, count_if(model_right) AS model_right,
         count_if(same_as_now_right) AS same_as_now_right, count_if(new_cluster_appeared) AS new_clusters,
         count_if(new_cluster_appeared AND new_cluster_rank <= 5) AS new_clusters_in_top5
  FROM {T}.gold_retro_check GROUP BY ALL"""))
