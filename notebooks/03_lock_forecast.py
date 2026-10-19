# Databricks notebook source
# MAGIC %md
# MAGIC # 03 · Train, test and lock a forecast
# MAGIC 1. Reads `gold_area_fortnight`.
# MAGIC 2. **Honest test:** trains on 2015–21, picks the new-cluster feature set on 2022–23, retrains on 2015–23 and
# MAGIC    tests once on 2024–26, against the baselines. Everything goes to the MLflow experiment `dengueradar`.
# MAGIC 3. Retrains both models on every labelled row and registers them in Unity Catalog.
# MAGIC 4. Scores all 55 planning areas on the newest NEA list and **appends** them to `gold_sealed_forecasts`, an
# MAGIC    append-only Delta table (`delta.appendOnly = true`: rows can be added, never changed or deleted).
# MAGIC
# MAGIC Each forecast predicts an area's level on the first NEA list 14–35 days after its basis date.

# COMMAND ----------

# MAGIC %pip install -q scikit-learn mlflow

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

dbutils.widgets.text("catalog", "workspace")
dbutils.widgets.text("schema", "dengue")
dbutils.widgets.text("git_commit", "")
dbutils.widgets.dropdown("allow_relock", "false", ["false", "true"], "Lock again for the same basis date")

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")
git_commit = dbutils.widgets.get("git_commit")
allow_relock = dbutils.widgets.get("allow_relock") == "true"

# COMMAND ----------

import os
import re
import sys
import uuid
import warnings
from datetime import datetime, timedelta, timezone

import mlflow
import pandas as pd

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.abspath("../src"))
from dengue_model.forecast import (LMH_FEATURES, NEW_SETS, choose_new_set, fit_lmh, fit_new, forecast,
                                   lmh_scores, new_scores, split)

df = spark.table(f"{catalog}.{schema}.gold_area_fortnight").toPandas()
basis = df.snapshot_date.max()
basis_source = df.loc[df.snapshot_date == basis, "list_source"].iloc[0]
sealed = f"{catalog}.{schema}.gold_sealed_forecasts"
print(f"newest NEA list: {basis} ({basis_source}); {len(df):,} rows, {df.y.notna().sum():,} with a known outcome")

if spark.catalog.tableExists(sealed) and not allow_relock:
    done = spark.sql(f"SELECT count(*) FROM {sealed} WHERE basis_date = DATE'{basis}'").first()[0]
    if done:
        dbutils.notebook.exit(f"A forecast for basis date {basis} is already locked; nothing to do.")

# COMMAND ----------

def key(s):
    """MLflow metric names allow letters, digits, _ - . / and spaces only."""
    return re.sub(r"[^0-9A-Za-z_\-./ ]", "", s).strip().replace("  ", " ")


user = spark.sql("SELECT current_user()").first()[0]
mlflow.set_experiment(f"/Users/{user}/dengueradar")

train, valid, test = split(df)
with mlflow.start_run(run_name=f"forecast {basis}") as run:
    mlflow.log_params({"basis_date": str(basis), "basis_source": basis_source, "git_commit": git_commit,
                       "train": "2015-2021", "valid": "2022-2023", "test": "2024-2026",
                       "rows_train": len(train), "rows_valid": len(valid), "rows_test": len(test)})

    # choose the new-cluster feature set on the validation years only
    new_set, v = choose_new_set(train, valid)
    mlflow.log_param("new_cluster_features", new_set)
    for r in v.itertuples():
        mlflow.log_metric(key(f"valid caught_at_5 {r.name}"), r.caught_at_5)

    # one honest test on 2024-26, trained on everything before it
    train_valid = pd.concat([train, valid])
    lmh = lmh_scores(train_valid, test)
    new = new_scores(train_valid, test)
    for r in lmh.itertuples():
        for m in ["high_recall", "high_precision", "macro_f1", "top10_capture"]:
            mlflow.log_metric(key(f"test {m} {r.model}"), getattr(r, m))
    for r in new.itertuples():
        mlflow.log_metric(key(f"test caught_at_5 {r.score}"), r.caught_at_5)
        mlflow.log_metric(key(f"test roc_auc {r.score}"), r.roc_auc)
    mlflow.log_text(lmh.to_csv(index=False), "test_2024_26_risk_level.csv")
    mlflow.log_text(new.to_csv(index=False), "test_2024_26_new_clusters.csv")
    mlflow.log_text(v.to_csv(index=False), "valid_2022_23_feature_sets.csv")

    # final models: every labelled row
    labelled = pd.concat([train, valid, test])
    lmh_model = fit_lmh(labelled)
    new_feats = NEW_SETS[new_set]
    new_model = fit_new(labelled, new_feats)
    mlflow.sklearn.log_model(lmh_model, "area_risk_level", input_example=labelled[LMH_FEATURES].head(5))
    mlflow.sklearn.log_model(new_model, "new_cluster_score", input_example=labelled[new_feats].head(5))
    run_id = run.info.run_id

display(lmh.round(3))
display(new.round(3))
print("new-cluster feature set chosen on 2022-23:", new_set)

# COMMAND ----------

# Register both models in Unity Catalog (the lock below does not depend on this)
mlflow.set_registry_uri("databricks-uc")
for name in ["area_risk_level", "new_cluster_score"]:
    try:
        mv = mlflow.register_model(f"runs:/{run_id}/{name}", f"{catalog}.{schema}.dengueradar_{name}")
        print(f"registered {catalog}.{schema}.dengueradar_{name} v{mv.version}")
    except Exception as e:
        print(f"could not register {name}: {e}")

# COMMAND ----------

spark.sql(f"""
  CREATE TABLE IF NOT EXISTS {sealed} (
    forecast_id STRING COMMENT 'One id per locked forecast (all 55 areas share it)',
    locked_at_utc TIMESTAMP COMMENT 'When the forecast was written',
    basis_date DATE COMMENT 'Date of the NEA list the forecast starts from',
    basis_source STRING COMMENT 'nea_page / live / wayback',
    score_from DATE COMMENT 'Outcome = level on the first NEA list on or after this date (basis + 14 days) ...',
    score_until DATE COMMENT '... and no later than this date (basis + 35 days)',
    planning_area STRING,
    level_today INT COMMENT '0 Low, 1 Medium, 2 High on the basis list',
    recent_cases_today INT,
    predicted_level STRING COMMENT 'Low / Medium / High, most likely level',
    p_low DOUBLE, p_medium DOUBLE, p_high DOUBLE,
    same_as_now_level STRING COMMENT 'Baseline: the level today, carried forward',
    new_cluster_score DOUBLE COMMENT 'Chance of a new cluster, for areas clear today (else NULL)',
    new_cluster_rank DOUBLE COMMENT '1 = most likely new cluster',
    past_cluster_score DOUBLE COMMENT 'Baseline: past cases here, recent weighted more (log scale)',
    new_cluster_features STRING,
    mlflow_run_id STRING,
    git_commit STRING
  )
  COMMENT 'Locked DengueRadar forecasts. Append-only: rows can be added but never updated or deleted, so a forecast cannot be edited after the outcome is known.'
  TBLPROPERTIES ('delta.appendOnly' = 'true')
""")

_, fc = forecast(df, lmh_model, new_model, new_feats)
fc = fc.assign(
    forecast_id=str(uuid.uuid4()),
    locked_at_utc=datetime.now(timezone.utc).replace(tzinfo=None),
    basis_date=basis,
    basis_source=basis_source,
    score_from=basis + timedelta(days=14),
    score_until=basis + timedelta(days=35),
    new_cluster_features=new_set,
    mlflow_run_id=run_id,
    git_commit=git_commit,
)
cols = [f.name for f in spark.table(sealed).schema.fields]
fc = fc[cols].astype(object).where(fc[cols].notna(), None)
(spark.createDataFrame(fc, schema=spark.table(sealed).schema)
 .write.mode("append").saveAsTable(sealed))

version = spark.sql(f"DESCRIBE HISTORY {sealed} LIMIT 1").first()["version"]
with mlflow.start_run(run_id=run_id):
    mlflow.log_params({"sealed_table": sealed, "sealed_table_version": version,
                       "forecast_id": fc.forecast_id.iloc[0]})
print(f"locked forecast {fc.forecast_id.iloc[0]} as {sealed} version {version}")

# COMMAND ----------

display(spark.sql(f"""
  SELECT planning_area, level_today, predicted_level, round(p_high, 2) AS p_high,
         same_as_now_level, round(new_cluster_score, 3) AS new_cluster_score, new_cluster_rank
  FROM {sealed} WHERE forecast_id = '{fc.forecast_id.iloc[0]}'
  ORDER BY p_high DESC, new_cluster_score DESC
"""))
