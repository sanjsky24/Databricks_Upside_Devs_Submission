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
| `bronze_nea_dengue_cluster_page` | NEA's dengue cluster web page, parsed at every run: alert, cases in the last 2 weeks, total, street breakdown | [nea.gov.sg/…/dengue-clusters](https://www.nea.gov.sg/dengue-zika/dengue/dengue-clusters) |
| `bronze_nea_dengue_cases_by_region` | NEA Dengue Cases – Central / NE / NW / SE / SW (`_part` = region) | `d_5f90…`, `d_e95e…`, `d_ce8c…`, `d_2c13…`, `d_e342…` |
| `bronze_nea_high_aedes_areas` | Areas with High Aedes Population | `d_5d060d8b7838a15e8906fb22c50dbf51` |
| `bronze_nea_wolbachia_release_sites` | Wolbachia Release Sites | `d_533d673fe769e58f71609c18080ea7a7` |
| `bronze_moh_weekly_infectious_disease` | MOH Weekly Infectious Disease Bulletin, 2012–2022 | `d_ca168b2cb763640d72c4600a68f9909e` |
| `bronze_moh_weekly_dengue_dhf` | Weekly dengue & DHF cases | `d_ac1eecf0886ff0bceefbc51556247015` |
| `bronze_weather_rainfall_monthly_*` | Monthly rainfall total / rain days / max daily, 1982– | `d_b16d…`, `d_1348…`, `d_53ff…` |
| `bronze_weather_temp_monthly_*` | Monthly temperature mean / mean daily max & min / extremes | `d_7552…`, `d_b0fb…`, `d_c92e…`, `d_72a4…`, `d_3b4b…` |
| `bronze_weather_humidity_monthly_mean` | Monthly mean relative humidity | `d_e3b1111a26b2aefef4f386c068d4ca5d` |
| `bronze_mss_daily_weather` | Daily rainfall, temperature and wind for every MSS station (~110), Jan 2015 – last month, no breaks | [weather.gov.sg/climate-historical-daily](https://www.weather.gov.sg/climate-historical-daily/) |
| `bronze_weather_station_rainfall_hist` | *(optional, ~9 GB)* 5-min station rainfall 2016–2024 (`_part` = year) | 9 yearly datasets |
| `bronze_weather_station_air_temp_hist` | *(optional, ~9 GB)* station air temperature 2016–2024 | 9 yearly datasets |
| `bronze_hdb_property_information` | HDB Property Information | `d_17f5382f26140b1fdae0ba2ef6239d2f` |
| `bronze_hdb_existing_building` | HDB building footprints (block, postal code); gives blocks their coordinates | `d_16b157c52ed637edd6ba1232e026258d` |
| `bronze_ura_mp2019_planning_area` / `_subzone` | URA Master Plan 2019 boundaries | `d_4765…`, `d_8594…` |
| `bronze_ura_mp2025_planning_area` | URA Master Plan 2025 planning areas | `d_2cc750190544007400b2cfd5d7f53209` |
| `bronze_sgcharts_dengue_clusters_2013_2020` | Past NEA cluster lists, 347 snapshots May 2013 – Nov 2020 | [outbreak.sgcharts.com/data](https://outbreak.sgcharts.com/data) |
| `bronze_scidata_dengue_clusters_2020` | Dengue clusters Feb–Jul 2020 (Huang et al., *Scientific Data*) | [figshare 13142777](https://doi.org/10.6084/m9.figshare.13142777) |
| `bronze_nea_dengue_clusters_wayback` | Past NEA cluster lists from 79 Internet Archive captures, Dec 2018 – Sep 2026 | [`data/seed/nea_dengue_clusters_wayback_2018_2026.ndjson`](data/seed/nea_dengue_clusters_wayback_2018_2026.ndjson) |
| `bronze_wolbachia_release_start_dates` | Wolbachia phase/site start dates compiled from NEA pages | [`data/seed/wolbachia_release_start_dates.csv`](data/seed/wolbachia_release_start_dates.csv) |
| `bronze_wolbachia_zone_start_dates` | Start date for each of the 31 zones in NEA's Wolbachia layer (used by the model) | [`data/seed/wolbachia_zone_start_dates.csv`](data/seed/wolbachia_zone_start_dates.csv) |
| `bronze_cda_weekly_dengue` | Weekly national dengue cases 2023-W01 – 2026-W40 from CDA bulletin PDFs, no missing weeks | [`data/seed/cda_weekly_dengue_2023_2026.csv`](data/seed/cda_weekly_dengue_2023_2026.csv) |
| `bronze_osm_road_centroids` | Centre point of every named road (OpenStreetMap), to place streets with no block number | [`data/seed/osm_road_centroids.csv`](data/seed/osm_road_centroids.csv) |

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
databricks bundle deploy -t prod
databricks bundle run dengue_ingest -t prod
```

The `prod` job runs every day at 11:30 and 18:30 SGT (the second run is a backup) and emails you if a
run fails. Every missed day is cluster history that can't be recovered, so keep it unpaused.
`-t dev` deploys a paused copy that writes to schema `dengue_dev`, for experiments.

**Locally (landing only, no Spark):**

```bash
python scripts/land_local.py --root ./_landing
```

Optional: store a data.gov.sg API key in secret scope `datagov` / key `api_key` (or env `DATAGOV_API_KEY`
locally) for higher rate limits. Without one, the client backs off and retries automatically.

### Notes on the data

* **SGCharts**: snapshots before Jul 2015 have `latlon_flagged_incorrect = true` (SGCharts flags their
  coordinates as wrong; counts and addresses are fine, so silver places those by street and block).
  `recent_cases_in_cluster = -1` before 25 Nov 2013; silver starts there.
  `cluster_number` is not a stable ID across snapshots.
* **Dengue Cases by Region** and **MOH weekly** datasets stopped updating on data.gov.sg in 2024 (MOH bulletin coverage ends 2022).
* **Wolbachia seed**: dates come from NEA's *Progress of Project Wolbachia* timeline (Jun 2022) and NEA
  press releases; each row has its `source_url` and a date precision (`day` / `month` / `quarter`).
  `town_hint` is hand-assigned. Validate it spatially against `bronze_nea_wolbachia_release_sites`.
  Some press coverage mentions Pasir Ris from Mar 2026; no NEA page confirmed it, so it is not included.
* **Wolbachia zone dates** (`wolbachia_zone_start_dates.csv`): one row per zone in NEA's release-sites layer, names
  matching it exactly (including the trailing space in `"Moulmein - Dorset "`). This is the file the model uses to
  work out which areas were protected on any given date. Circuit Road's date is inferred (`confidence = low`);
  Pasir Ris (Mar 2026) relies on press coverage.
* **Internet Archive cluster lists**: parsed from archived copies of NEA's dengue cluster page (source: NEA). Coverage
  is thin in 2021 (few captures) and 2023–24 (8–9 a year). Each row keeps its `capture_url`. 11 of 7,102 clusters have
  street counts that don't add up to the cluster total, as printed on NEA's page. To rebuild the seed, run
  `python scripts/build_wayback_seed.py <wb_clusters.json>`.
* **CDA weekly cases**: parsed from the bulletin PDFs; 2025 is missing about 9 weeks.
* **OpenStreetMap roads**: © OpenStreetMap contributors, ODbL 1.0. Rebuild with `python scripts/build_osm_roads_seed.py`
  (overpass-api.de; the kumi.systems mirror returned HTTP 500 on 17 Oct 2026).

## Step 2 – Silver: one mapped cluster history (Lakeflow Declarative Pipeline)

[`pipelines/silver_clusters.sql`](pipelines/silver_clusters.sql) runs as the last task of the daily job and turns the
bronze tables into one cluster history from Nov 2013 to today, every cluster placed on the map:

```
sgcharts 2013-11 → 2015-04 (streets) ───────┐
sgcharts 2015-07 → 2020-11 (lat/lon given) ─┤
Internet Archive 2021 → 2026-09 (streets) ──┼─► silver_cluster_points ─► silver_clusters (area, alert, active)
NEA page, every run from 17 Oct 2026 ───────┤        ▲
(data.gov.sg layer only before that) ───────┘        │
HDB blocks + OpenStreetMap roads ─► silver_street_locations
URA planning areas, Wolbachia zones + start dates ─► silver_planning_areas, silver_wolbachia_zones, silver_hdb_blocks
```

| Table | One row per | Notes |
|---|---|---|
| `silver_planning_areas` | URA 2019 planning area (55) | shapes repaired with `st_buffer(g, 0)` (5 were slightly invalid; areas unchanged) |
| `silver_wolbachia_zones` | NEA release zone (31) | with `start_date` and `confidence` |
| `silver_hdb_blocks` | residential HDB block (10,270; 94.9% of homes) | coordinates, homes, planning area, H3 cell, earliest Wolbachia start covering it |
| `silver_street_locations` | street named in the archive lists (9,226) | `method`: block (63%), road (35%), road_exp, none (161 streets) |
| `silver_cluster_points` | address / street / live polygon in a cluster list | planning area, SVY21 x/y, H3 res-9 cell, Wolbachia zone |
| `silver_clusters` | cluster × list date | `planning_area` = area with most of its cases; `alert` red (10+) / yellow; `is_active` |
| `silver_source_agreement` | date covered by 2+ archives | sgcharts, Internet Archive and *Scientific Data* agree case-for-case on all 19 shared dates |

Spatial work uses Databricks' built-in `st_*` and `h3_*` SQL functions. Quality rules (pipeline expectations) drop
rows with no cases or no planning area, and record (without dropping) streets that couldn't be placed, clusters
with under half their cases placed, and any disagreement between archives; see the pipeline's data-quality tab.

Checked against the prototype: 314 archived list dates, 17,750 active clusters, 99% of archive cases placed, 67% of
HDB homes in today's Wolbachia zones, and the same share of cases inside those zones by period (2015–16 67% … 2025 20%).

**Live clusters come from NEA's page, not the data.gov.sg layer.** The page has the same fields as the Internet
Archive captures the model learns from (including cases with onset in the last 2 weeks, which the data.gov.sg layer
lacks) and is a day or two more current. The data.gov.sg layer is still captured and fills only the days before the
first page capture. The parser (`src/dengue_ingest/nea_page.py`) fails the run loudly if the page layout changes.

## Step 3 – Gold: the area-by-fortnight table

[`pipelines/gold_area_fortnight.sql`](pipelines/gold_area_fortnight.sql) (same pipeline) builds `gold_area_fortnight`:
one row per planning area per cluster-list date, with what was known that day and the area's risk level about two
weeks later. It is what the model trains on and what the weekly forecast scores.

* **Risk level** (NEA terms): 2 High = an active cluster with 10+ cases, 1 Medium = any active cluster, 0 Low.
* **Features**: today's level, recent and total cases, number of clusters, past cases here (each list weighted by
  `exp(-days/180)`), average level over the last 3 lists, recent cases in neighbouring areas (`gold_area_neighbours`,
  boundaries within 50 m), national recent cases, HDB homes, share of homes in Wolbachia zones started ≥60 days
  earlier, season, and national weather known that day (`gold_weather_monthly`: rainfall, rain days, mean and
  mean-daily-max temperature, humidity at Changi, as anomalies vs the 1982–2014 average for the calendar month;
  the latest month that ended ≥15 days before the list date, since NEA publishes about a week after month end,
  plus the 3 months up to it).
* **Label `y`**: the level at the first list ≥14 days later, kept only if that list is 14–35 days away.
  A **new cluster** is `cls = 0` today and `y > 0`.
* Daily lists from our own captures are thinned to every 4th day back from the newest, matching the twice-weekly
  rhythm of the 2015–20 training data.

Matches the prototype: 15,345 labelled pairs. The 2024–26 test window has 69 new-cluster cases (prototype: 68); the
extra one is a cluster on 13 Jul 2026 split 2–2 between Sengkang and a same-named road in Western Water Catchment,
which the prototype resolved by chance and the pipeline now resolves to the area with more homes (Sengkang).

## Step 4 – The Monday screen: Databricks App on Lakebase

The app in [`app/`](app/) is what officers use. Everyone signs in with their Databricks account (Databricks Apps does
this; the app has no passwords of its own); on the team's shared account it asks "What should we call you?" and keeps
the name in that browser. It greets the officer in Singapore time with a summary built only from this week's data. A
left panel has ten tabs in the order an officer works (This week, Areas, Why?, Clusters today, New clusters, Alerts,
Teams, Run sheets, Ask, Track record) next to an OpenStreetMap map (dark tiles in dark mode; HDB blocks appear when
zoomed in). Teams shows time per team (travel, walking, inspection, breaks, finish time, shifts needed) from editable
assumptions, an ordered walking route, and printable / CSV run sheets. Ask is the Genie chat
([`app/genie_chat.py`](app/genie_chat.py)); run [`scripts/grant_app_genie.sh`](scripts/grant_app_genie.sh) once so the
app may use the Genie space and read its tables. Alerts: a "Draft alert" button (residents / town council /
grassroots; SMS, notice or email; English, 中文, Melayu, தமிழ்) drafts the message with Llama 3.3 on Databricks from that
area's facts only, flags any number not in the data, and logs drafts and sent alerts in Lakebase
(`dengueradar_app.alerts`). Track record: the 2020–26 test in plain language, the forecast made afterwards from NEA's
14 Oct list next to NEA today, the locked forecasts and weekly cases.

```
gold tables ──► 05_publish_app_data ──► Lakebase Postgres (schema dengueradar) ──► Databricks App (FastAPI + Leaflet)
```

- **Lakebase** instance `dengueradar` (CU_1) and the app `dengueradar` are bundle resources. The app's service
  principal signs in to Postgres with a short-lived OAuth token.
- [`notebooks/05_publish_app_data.py`](notebooks/05_publish_app_data.py) runs after the team planner in both jobs and
  replaces each table in one transaction.
- Start or redeploy the app with `databricks bundle run dengueradar_app -t prod`.
- Local preview without Lakebase: `DENGUERADAR_SAMPLE=data.json python app/app.py` (same JSON as `/api/data`).

## Step 5 – Analyst dashboard (AI/BI)

[`pipelines/gold_dashboard.sql`](pipelines/gold_dashboard.sql) (same pipeline) builds the tables behind the app and
the AI/BI dashboard `DengueRadar` ([`dashboards/dengueradar.lvdash.json`](dashboards/dengueradar.lvdash.json), generated by
[`scripts/build_dashboard.py`](scripts/build_dashboard.py) and deployed with the bundle):

| Table | What |
|---|---|
| `gold_forecast_latest` | Newest locked forecast per area, with map position and "Why?" reasons (the model's inputs for the area, not its weights) |
| `gold_track_record` | Every locked forecast next to NEA's first list 14–35 days later (`waiting` until that list exists) |
| `gold_area_levels` | Every area's NEA level on every list date |
| `gold_block_priority` | HDB blocks weighted for inspection: near a cluster ×3, near a high-Aedes area ×2, in a flagged area ×1, halved in Wolbachia zones |
| `gold_national_weekly` | Weekly national cases 2012 → latest bulletin |
| `gold_team_plan`, `_blocks`, `_summary` | One non-overlapping site per team (300 m), from [`notebooks/04_team_plan.py`](notebooks/04_team_plan.py) |
| `gold_model_test_*` | The 2020–26 test results, written by `03_lock_forecast` |

The team plan is compared with a simple plan (top-weight blocks spread ≥600 m apart), not with "top blocks,
overlaps allowed", which can send every team to the same street.
