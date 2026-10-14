# Databricks notebook source
# MAGIC %md
# MAGIC # 00 · Setup
# MAGIC Creates the schema and the two Unity Catalog Volumes the pipeline uses:
# MAGIC * `landing` – raw files exactly as downloaded, one folder per source version (never overwritten)
# MAGIC * `checkpoints` – Auto Loader schema + checkpoint state
# MAGIC
# MAGIC Set `create_catalog = true` only if you have permission to create catalogs
# MAGIC (Free Edition / most hackathon workspaces: keep the default `workspace` catalog).

# COMMAND ----------

dbutils.widgets.text("catalog", "workspace")
dbutils.widgets.text("schema", "dengue")
dbutils.widgets.dropdown("create_catalog", "false", ["false", "true"])

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")

# COMMAND ----------

if dbutils.widgets.get("create_catalog") == "true":
    spark.sql(f"CREATE CATALOG IF NOT EXISTS `{catalog}`")

spark.sql(f"CREATE SCHEMA IF NOT EXISTS `{catalog}`.`{schema}` "
          "COMMENT 'Dengue / Wolbachia / weather open data for Singapore (bronze layer)'")
spark.sql(f"CREATE VOLUME IF NOT EXISTS `{catalog}`.`{schema}`.landing "
          "COMMENT 'Raw downloads, one immutable folder per source version'")
spark.sql(f"CREATE VOLUME IF NOT EXISTS `{catalog}`.`{schema}`.checkpoints "
          "COMMENT 'Auto Loader schema and checkpoint state'")

display(spark.sql(f"SHOW VOLUMES IN `{catalog}`.`{schema}`"))
