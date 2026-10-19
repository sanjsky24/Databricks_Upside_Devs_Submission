# DengueRadar: progress report

**As of Mon 19 Oct 2026, 18:30 SGT · sprint day 8 of 14 · Demo Day Tue 27 Oct**

## In one paragraph

The data side is built and running on Databricks. Every day, a job downloads NEA's dengue clusters and 31 other
sources, cleans them into one cluster history from 2015 to today, and turns that into the table the model learns
from. Every number we checked matches the prototype. **The model itself is not built yet, and the forecast we
planned to lock today did not happen.** That is the critical path for the next two days.

## Where we are against the plan

| Sprint days (slide 3) | Planned | Status |
|---|---|---|
| 1–3 (12–14 Oct) | Ingest + clean | ✅ Done, 3 days late (finished 15–17 Oct) |
| 4–5 (15–16 Oct) | Baselines; lock 12 Oct | ❌ 12 Oct lock missed (nothing was built yet) |
| 6–8 (17–19 Oct) | Models; lock 19 Oct | ⚠️ Model table done; models not trained; **19 Oct lock missed** |
| 9–11 (20–22 Oct) | Planner, dashboard, Genie | Not started |
| 12–14 (23–26 Oct) | Results + rehearsal | Not started |

## What is built and live

All of it lives in the Databricks workspace (team account) under catalog `workspace`, schema `dengue`.

**1. Daily download job: `dengue-ingest-bronze`**
- Runs at 11:30 and 18:30 SGT. Emails the team account if it fails.
- Downloads 32 sources (NEA clusters, NEA's cluster web page, Wolbachia sites, high-mosquito areas, weather, HDB,
  URA maps, cluster archives) and keeps every version. data.gov.sg keeps no history, so this job is building it.
- Live cluster history starts **15 Oct 2026**, with one data.gov.sg version every day since.

**2. Silver: one cluster history, 2015 → today** (`silver_*` tables, a Lakeflow Declarative Pipeline)
- Joins three sources end to end: sgcharts (Jul 2015 – Nov 2020), Internet Archive captures of NEA's page
  (2021 – Sep 2026), and our own daily captures (Oct 2026 →).
- Places every cluster on the map using HDB blocks and OpenStreetMap roads, and gives it a planning area and
  Wolbachia zone.
- Quality rules record anything that couldn't be placed (161 street names, 2%).

**3. Gold: the model table** (`gold_area_fortnight`)
- One row per planning area per list date: what was known that day, plus the area's risk level two weeks later.

**Checked against the prototype:**

| Number | Databricks | Prototype |
|---|---|---|
| Past cluster lists | 314 | 314 |
| Active clusters | 17,750 | 17,750 |
| Archive cases placed on the map | 99% | 99% |
| HDB homes in Wolbachia areas today | 67% | 67% |
| Area-fortnight pairs with a known outcome | 15,345 | 15,345 |
| New-cluster cases in the 2024–26 test | 69 | 68 (one tie the prototype got wrong) |
| Archives agree on overlapping dates | 19 of 19 | 19 of 19 |

Links: [job](https://dbc-3196997f-5ea3.cloud.databricks.com/jobs/966260569254903?w=7474648356621044) ·
[pipeline graph and data quality](https://dbc-3196997f-5ea3.cloud.databricks.com/pipelines/ed3176a1-c832-47b1-8516-645f95c7800d?w=7474648356621044)

## Problems found today

1. **This morning's run failed.** Figshare, which hosts a 2020 archive file we already have, was down (HTTP 500),
   and one failed download stops the rest of the job. No data was lost: today's NEA version was saved and loads on
   the next good run. **Fixed in code, not yet deployed:** frozen archives are now downloaded once and skipped after.
2. **NEA's cluster page hasn't updated since "as at 16 Oct".** We expected it to change daily. data.gov.sg does
   update daily, but it has no "cases in the last 2 weeks" count, which is the model's strongest clue.
3. **Silver currently ignores data.gov.sg lists after the first page capture,** so its newest list is 16 Oct, not
   19 Oct. This needs a rule for days when only data.gov.sg is fresh (see decisions below).

## Not built yet

- Baselines and the logistic model in MLflow, with the honest re-test (choose features on 2022–23, test once on
  2024–26)
- Locked forecasts (`gold.sealed_forecasts`) and scoring against what actually happened
- Team planner, AI/BI dashboard, Genie space, SQL alert, AI weekly summary
- Weather features (only monthly national weather is loaded so far)

## Decisions needed from the team

1. **When do we lock the first forecast?** Tomorrow (Tue 20 Oct) is the earliest realistic date. By Demo Day it can
   be checked after 7 of its 14 days. Present it as a spot-check; the 2024–26 backtest is the real evidence.
2. **What do we use on days NEA's page is stale?** Options: (a) use data.gov.sg and estimate "recent cases" from how
   much each cluster grew over the last 14 days of daily captures, or (b) forecast only from page dates. Recommend (a).
3. **Slide 2 says "Forecasts locked 12 & 19 Oct, scored live".** That is no longer true and needs changing before
   anyone presents it.

## Next step

Deploy the Figshare fix, then train the baselines and logistic model so a forecast can be locked on Tue 20 Oct.
