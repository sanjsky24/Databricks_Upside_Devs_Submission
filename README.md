# Databricks_Upside_Devs_Submission
This is a repo demonstrating the final submission by the Team The Upside Devs for Databricks 2026 hackathon

## Step 1 – Data ingestion (raw → bronze Delta)

Raw datasets are pulled from data.gov.sg and the historical dengue archives into a Unity Catalog
Volume, then loaded into bronze Delta tables with **Auto Loader** (schema inference + evolution).

```
data.gov.sg API ┐
SGCharts zip    ├─► 01_land_raw ──► /Volumes/<cat>/<schema>/landing/<source>/<dataset>/v=<version>/
figshare CSV    │                           (immutable, one folder per published version)
repo seed CSV   ┘                                        │
                                                         ▼
                                   02_bronze_autoloader (cloudFiles) ──► <cat>.<schema>.bronze_<source>
                                                                        + bronze_<source>_latest view
```

### Sources

| Bronze table | Source | data.gov.sg id / URL |
|---|---|---|
| `bronze_nea_dengue_clusters` | NEA Dengue Clusters (daily, versioned) | `d_dbfabf16158d1b0e1c420627c0819168` |
| `bronze_nea_dengue_cases_by_region` | NEA Dengue Cases – Central / NE / NW / SE / SW (`_part` = region) | `d_5f90…`, `d_e95e…`, `d_ce8c…`, `d_2c13…`, `d_e342…` |
| `bronze_nea_high_aedes_areas` | Areas with High Aedes Population | `d_5d060d8b7838a15e8906fb22c50dbf51` |
| `bronze_nea_wolbachia_release_sites` | Wolbachia Release Sites | `d_533d673fe769e58f71609c18080ea7a7` |
| `bronze_moh_weekly_infectious_disease` | MOH Weekly Infectious Disease Bulletin, 2012–2022 | `d_ca168b2cb763640d72c4600a68f9909e` |
| `bronze_moh_weekly_dengue_dhf` | Weekly dengue & DHF cases | `d_ac1eecf0886ff0bceefbc51556247015` |
| `bronze_weather_rainfall_monthly_*` | Monthly rainfall total / rain days / max daily, 1982– | `d_b16d…`, `d_1348…`, `d_53ff…` |
| `bronze_weather_temp_monthly_*` | Monthly temperature mean / mean daily max & min / extremes | `d_7552…`, `d_b0fb…`, `d_c92e…`, `d_72a4…`, `d_3b4b…` |
| `bronze_weather_humidity_monthly_mean` | Monthly mean relative humidity | `d_e3b1111a26b2aefef4f386c068d4ca5d` |
| `bronze_weather_station_rainfall_hist` | *(optional, ~9 GB)* 5-min station rainfall 2016–2024 (`_part` = year) | 9 yearly datasets |
| `bronze_weather_station_air_temp_hist` | *(optional, ~9 GB)* station air temperature 2016–2024 | 9 yearly datasets |
| `bronze_hdb_property_information` | HDB Property Information | `d_17f5382f26140b1fdae0ba2ef6239d2f` |
| `bronze_ura_mp2019_planning_area` / `_subzone` | URA Master Plan 2019 boundaries | `d_4765…`, `d_8594…` |
| `bronze_ura_mp2025_planning_area` | URA Master Plan 2025 planning areas | `d_2cc750190544007400b2cfd5d7f53209` |
| `bronze_sgcharts_dengue_clusters_2013_2020` | Past NEA cluster lists, 347 snapshots May 2013 – Nov 2020 | [outbreak.sgcharts.com/data](https://outbreak.sgcharts.com/data) |
| `bronze_scidata_dengue_clusters_2020` | Dengue clusters Feb–Jul 2020 (Huang et al., *Scientific Data*) | [figshare 13142777](https://doi.org/10.6084/m9.figshare.13142777) |
| `bronze_wolbachia_release_start_dates` | Wolbachia phase/site start dates compiled from NEA pages | [`data/seed/wolbachia_release_start_dates.csv`](data/seed/wolbachia_release_start_dates.csv) |

The full registry lives in [`src/dengue_ingest/sources.py`](src/dengue_ingest/sources.py); add a row there to ingest another dataset.

### Version history

data.gov.sg has no API for past versions; it only serves the current file. So the landing step snapshots each
dataset whenever its `lastUpdatedAt` changes (and the bytes differ), and never overwrites a previous
snapshot. Every bronze row carries `_version` / `_version_ts_sgt`, so the history is plain SQL:

```sql
SELECT _version_ts_sgt, count(*) AS clusters, sum(case_size) AS cases
FROM workspace.dengue.bronze_nea_dengue_clusters
GROUP BY 1 ORDER BY 1;
```

History starts the first time the job runs. Before that, the SGCharts and *Scientific Data* archives cover
2013–2020. Delta time travel (`VERSION AS OF`) also works on every bronze table, and `landing_log`
records every landing attempt.

### Run it

**In a Databricks workspace (Git folder):** clone this repo as a Git folder and run the notebooks in order:
`notebooks/00_setup` → `01_land_raw` → `02_bronze_autoloader`. Widgets default to catalog `workspace`, schema `dengue`.
Set `include_large = true` to also pull the station-level weather history.

**As a job (Asset Bundle):**

```bash
databricks bundle deploy -t dev --var="catalog=workspace" --var="schema=dengue"
databricks bundle run dengue_ingest
```

The job is scheduled daily at 11:30 SGT but deployed **paused**. Unpause it to start collecting daily
cluster versions.

**Locally (landing only, no Spark):**

```bash
python scripts/land_local.py --root ./_landing
```

Optional: store a data.gov.sg API key in secret scope `datagov` / key `api_key` (or env `DATAGOV_API_KEY`
locally) for higher rate limits. Without one, the client backs off and retries automatically.

### Notes on the data

* **SGCharts**: snapshots before Jul 2015 have `latlon_flagged_incorrect = true` (SGCharts flags their
  coordinates as wrong; counts and addresses are fine). `recent_cases_in_cluster = -1` before Dec 2013.
  `cluster_number` is not a stable ID across snapshots.
* **Dengue Cases by Region** and **MOH weekly** datasets stopped updating on data.gov.sg in 2024 (MOH bulletin coverage ends 2022).
* **Wolbachia seed**: dates come from NEA's *Progress of Project Wolbachia* timeline (Jun 2022) and NEA
  press releases; each row has its `source_url` and a date precision (`day` / `month` / `quarter`).
  `town_hint` is hand-assigned. Validate it spatially against `bronze_nea_wolbachia_release_sites`.
  Some press coverage mentions Pasir Ris from Mar 2026; no NEA page confirmed it, so it is not included.
