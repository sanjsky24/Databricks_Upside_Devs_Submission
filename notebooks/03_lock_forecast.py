# Databricks notebook source
# MAGIC %md
# MAGIC # 03 · Train, test and lock a forecast
# MAGIC 1. Reads `gold_area_fortnight`.
# MAGIC 2. **Honest test:** chooses feature sets (with and without weather) and decision thresholds on a backtest of
# MAGIC    2017, 2018 and 2019, then tests every year from 2020, each predicted only from the years before it, against
# MAGIC    the baselines, with 90% bootstrap intervals. Everything goes to the MLflow experiment `dengueradar`.
# MAGIC 3. Retrains both models on every labelled row and registers them in Unity Catalog.
# MAGIC 4. Scores all 55 planning areas on the newest NEA list and **appends** them to `gold_sealed_forecasts`, an
# MAGIC    append-only Delta table (`delta.appendOnly = true`: rows can be added, never changed or deleted).
# MAGIC    Skipped if that list is already locked; steps 1-3 still run, so this also just retrains.
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
from dengue_model.forecast import (CHOOSE_YEARS, LMH_SETS, NEW_SETS, TEST_YEARS, by_year, choose_lmh, choose_new_set,
                                   development, fit_lmh, fit_new, forecast, labelled, lmh_scores, new_scores)

df = spark.table(f"{catalog}.{schema}.gold_area_fortnight").toPandas()
basis = df.snapshot_date.max()
basis_source = df.loc[df.snapshot_date == basis, "list_source"].iloc[0]
sealed = f"{catalog}.{schema}.gold_sealed_forecasts"
print(f"newest NEA list: {basis} ({basis_source}); {len(df):,} rows, {df.y.notna().sum():,} with a known outcome")

# COMMAND ----------

def key(s):
    """MLflow metric names allow letters, digits, _ - . / and spaces only."""
    return re.sub(r"[^0-9A-Za-z_\-./ ]", "", s).strip().replace("  ", " ")


user = spark.sql("SELECT current_user()").first()[0]
mlflow.set_experiment(f"/Users/{user}/dengueradar")

lab = labelled(df)
dev = development(lab)
with mlflow.start_run(run_name=f"forecast {basis}") as run:
    mlflow.log_params({"basis_date": str(basis), "basis_source": basis_source, "git_commit": git_commit,
                       "choose_on": f"backtest {', '.join(map(str, CHOOSE_YEARS))} (each from the years before)",
                       "test": f"every year {TEST_YEARS[0]}-{TEST_YEARS[-1]}, each from the years before",
                       "first_list": str(lab.snapshot_date.min().date()), "rows_labelled": len(lab)})

    # choose feature sets and thresholds on the development years only
    lmh_set, t_high, t_any, v_lmh = choose_lmh(dev)
    new_set, v_new = choose_new_set(dev)
    mlflow.log_params({"risk_level_features": lmh_set, "high_threshold": round(t_high, 4),
                       "medium_threshold": round(t_any, 4), "new_cluster_features": new_set})
    for r in v_lmh.itertuples():
        mlflow.log_metric(key(f"backtest high_f1 {r.name}"), r.high_f1)
    for r in v_new.itertuples():
        mlflow.log_metric(key(f"backtest caught_at_5 {r.name}"), r.caught_at_5)

    # rolling test of every year from 2020 with those choices fixed
    lmh_feats, new_feats = LMH_SETS[lmh_set], NEW_SETS[new_set]
    lmh, lmh_ci, lmh_test = lmh_scores(lab, lmh_feats, t_high, t_any)
    new, new_ci, new_test = new_scores(lab, new_set)
    years = by_year(lmh_test, new_test)
    mlflow.log_params({"test_lists": lmh_test.snapshot_date.nunique(), "test_high_outcomes": int((lmh_test.y == 2).sum()),
                       "test_new_clusters": int(new_test.e.sum())})
    for r in lmh.itertuples():
        for m in ["high_precision", "high_recall", "high_f1", "macro_f1", "top10_capture"]:
            mlflow.log_metric(key(f"test {m} {r.model}"), getattr(r, m))
    for r in new.itertuples():
        for m in ["caught_at_5", "precision_at_5", "roc_auc", "pr_auc"]:
            mlflow.log_metric(key(f"test {m} {r.score}"), getattr(r, m))
    mlflow.log_metrics({"test high_f1 minus same-as-now p05": lmh_ci[0], "test high_f1 minus same-as-now p95": lmh_ci[1],
                        "test caught_at_5 minus past-clusters p05": new_ci[0],
                        "test caught_at_5 minus past-clusters p95": new_ci[1]})
    mlflow.log_text(lmh.to_csv(index=False), "test_2020_26_risk_level.csv")
    mlflow.log_text(new.to_csv(index=False), "test_2020_26_new_clusters.csv")
    mlflow.log_text(years.to_csv(index=False), "test_2020_26_by_year.csv")
    mlflow.log_text(v_lmh.to_csv(index=False), "backtest_risk_level_feature_sets.csv")
    mlflow.log_text(v_new.to_csv(index=False), "backtest_new_cluster_feature_sets.csv")

    # final models: every labelled row
    lmh_model = fit_lmh(lab, lmh_feats)
    new_model = fit_new(lab, new_feats)
    mlflow.sklearn.log_model(lmh_model, "area_risk_level", input_example=lab[lmh_feats].head(5))
    mlflow.sklearn.log_model(new_model, "new_cluster_score", input_example=lab[new_feats].head(5))
    run_id = run.info.run_id

display(v_lmh.round(3))
display(v_new.round(3))
display(lmh.round(3))
display(new.round(3))
display(years.round(3))
print(f"chosen on the backtest: risk level = {lmh_set} (High if P(High) >= {t_high:.3f}), new clusters = {new_set}")
print(f"test {TEST_YEARS[0]}-26, 90% interval: High F1 minus same-as-now [{lmh_ci[0]:+.3f}, {lmh_ci[1]:+.3f}]; "
      f"caught@5 minus past clusters [{new_ci[0]:+.3f}, {new_ci[1]:+.3f}]")

# COMMAND ----------

# The test results for the dashboard, replaced on every run (MLflow keeps every run)
headline = pd.DataFrame([
    {"question": "High-risk areas (F1)", "model": float(lmh.set_index("model").high_f1["Logistic regression"]),
     "baseline_name": "Same as now", "baseline": float(lmh.set_index("model").high_f1["Same as now"]),
     "diff_p05": lmh_ci[0], "diff_p95": lmh_ci[1]},
    {"question": "New clusters caught by top 5", "model": float(new.set_index("score").caught_at_5["Logistic: " + new_set]),
     "baseline_name": "Past clusters here", "baseline": float(new.set_index("score").caught_at_5["Past clusters here"]),
     "diff_p05": new_ci[0], "diff_p95": new_ci[1]},
    {"question": "New clusters caught by top 5", "model": float(new.set_index("score").caught_at_5["Logistic: " + new_set]),
     "baseline_name": "Chance (expected)", "baseline": float(new.set_index("score").caught_at_5["Chance (expected)"]),
     "diff_p05": None, "diff_p95": None},
])
for name, d in [("gold_model_test_headline", headline), ("gold_model_test_risk", lmh),
                ("gold_model_test_new_clusters", new), ("gold_model_test_by_year", years)]:
    d = d.assign(test=f"{TEST_YEARS[0]}-{TEST_YEARS[-1]}", test_lists=lmh_test.snapshot_date.nunique(),
                 mlflow_run_id=run_id, git_commit=git_commit, basis_date=basis)
    (spark.createDataFrame(d).write.mode("overwrite").option("overwriteSchema", "true")
     .saveAsTable(f"{catalog}.{schema}.{name}"))

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

# Training, testing and registering always run; locking is skipped if this NEA list is already locked
if spark.catalog.tableExists(sealed) and not allow_relock:
    done = spark.sql(f"SELECT count(*) FROM {sealed} WHERE basis_date = DATE'{basis}'").first()[0]
    if done:
        dbutils.notebook.exit(f"Models retrained and registered (MLflow run {run_id}); a forecast for basis date "
                              f"{basis} is already locked, so nothing new was locked.")

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
    predicted_level STRING COMMENT 'Low / Medium / High: High if p_high clears the backtest-tuned threshold (MLflow param high_threshold), else Medium if p_medium + p_high clears medium_threshold',
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

_, fc = forecast(df, lmh_model, lmh_feats, t_high, t_any, new_model, new_feats)
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
