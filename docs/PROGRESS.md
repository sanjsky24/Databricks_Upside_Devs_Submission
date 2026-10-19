# DengueRadar: progress report

**As of Mon 19 Oct 2026, 18:50 SGT · sprint day 8 of 14 · Demo Day Tue 27 Oct**

## In one paragraph

The data side is built and running on Databricks. Every day, a job downloads NEA's dengue clusters and 31 other
sources, cleans them into one cluster history from 2015 to today, and turns that into the table the model learns
from. Every number we checked matches the prototype. **Tonight the models were trained and honestly re-tested,
and the first forecast was locked at 18:44.** The High-risk result holds up (69% vs 62%). The new-cluster result comes
in lower than the prototype claimed: **39%, not 44%**.

## Where we are against the plan

| Sprint days (slide 3) | Planned | Status |
|---|---|---|
| 1–3 (12–14 Oct) | Ingest + clean | ✅ Done, 3 days late (finished 15–17 Oct) |
| 4–5 (15–16 Oct) | Baselines; lock 12 Oct | ❌ 12 Oct lock missed (nothing was built yet) |
| 6–8 (17–19 Oct) | Models; lock 19 Oct | ✅ Models trained and tested; ✅ forecast locked 19 Oct, 18:44 |
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

**4. Models and locked forecasts: job `dengue-lock-forecast`** (run by hand)
- Two logistic regression models: **area risk level** (Low / Medium / High in two weeks) and **new-cluster score**
  (for areas with no cluster today). Gradient boosting is trained alongside for comparison.
- Honest test: learn on 2015–21, choose the new-cluster clue set on 2022–23, then test **once** on 2024–26.
- Logs every score and both models to the MLflow experiment `dengueradar`, and registers the models in Unity Catalog.
- Appends all 55 areas' forecasts to `gold_sealed_forecasts`, an **append-only** table: rows can be added, never
  edited or deleted. "Same as now" is stored next to each forecast so the two can be scored side by side.
- Refuses to lock the same NEA list twice, and records the git commit of the code that made it.

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

## Model results (2024–26, years the model never saw while learning)

| Question | Our model | Best simple rule | Chance | Prototype claimed |
|---|---|---|---|---|
| High-risk areas caught | **69%** (precision 56%) | 62% "same as now" | – | 69% ✅ |
| New clusters caught by top 5 picks | **39%** | 38% "past clusters here" | 7% | 44% ❌ |

- **High risk:** the model beats "same as now", the toughest baseline. This is the strongest claim we have.
- **New clusters:** 5× better than chance, but only 1 point ahead of simply ranking areas by past clusters. The
  prototype's 44% came from picking the best clue set after seeing the test years. **Quote 39%.**
- Gradient boosting lost again (65% High recall, 29% new clusters), so logistic regression stays.

## The first locked forecast

- **Locked Mon 19 Oct 2026, 18:44 SGT** in `gold_sealed_forecasts`: forecast id `02bf3500-83ce-4931-9d89-301c13008ed4`,
  55 areas, code commit `00a8567`, MLflow run `6b21a32982da4f6c8510e2042274cfc8` (experiment `dengueradar`).
- **Checked it can't be edited:** the table has `delta.appendOnly = true`, and a test DELETE was refused
  (`DELTA_CANNOT_MODIFY_APPEND_ONLY`).
- Models registered in Unity Catalog: `workspace.dengue.dengueradar_area_risk_level` and
  `workspace.dengue.dengueradar_new_cluster_score`.
- **Based on NEA's list as at 16 Oct**, the newest with "cases in the last 2 weeks". Scored against the first NEA list
  14–35 days later, so from about 30 Oct. On Demo Day it can only be spot-checked.
- **High:** Ang Mo Kio, Jurong West, Bishan. **Medium:** Serangoon. Outram has a cluster today but the model expects
  it to clear.
- **Most likely new clusters** (clear today): Sengkang, Hougang, Bukit Batok, Yishun, Woodlands.

## Problems found today

1. **This morning's run failed.** Figshare, which hosts a 2020 archive file we already have, was down (HTTP 500), and
   one failed download stopped the rest of the job. No data was lost. **Fixed and deployed:** frozen archives are now
   downloaded once and skipped after. The 18:30 run succeeded.
2. **NEA's cluster page hasn't updated since "as at 16 Oct".** We expected it to change daily. data.gov.sg does
   update daily, but it has no "cases in the last 2 weeks" count, which is the model's strongest clue.
3. **Silver currently ignores data.gov.sg lists after the first page capture,** so its newest list is 16 Oct, not
   19 Oct. This needs a rule for days when only data.gov.sg is fresh (decision 1 below).

## Not built yet

- Scoring locked forecasts against what actually happened (`track_record`); first possible on ~30 Oct, so Demo
  Day shows a spot-check
- "Why?" reasons per area
- Team planner, AI/BI dashboard, Genie space, SQL alert, AI weekly summary
- A weekly schedule for the lock job
- Weather features (only monthly national weather is loaded so far)

## Decisions needed from the team

1. **What do we use on days NEA's page is stale?** Options: (a) use data.gov.sg and estimate "recent cases" from how
   much each cluster grew over the last 14 days of daily captures, or (b) forecast only from page dates. Recommend (a).
2. **Slides need two fixes before anyone presents them.** Slide 2 says "Forecasts locked 12 & 19 Oct, scored live";
   it should say one forecast, locked 19 Oct, spot-checked on Demo Day. Slides 2 and 3 say 44% and "6× better than
   chance"; the honest figures are 39% and about 5×.
3. **How do we pitch the new-cluster score,** given it barely beats "past clusters here"? Suggest: lead with the
   High-risk result and the team planner, and present the new-cluster score as "5× chance, and it explains itself".

## Next step

Build the team planner and the dashboard (sprint days 9–11, Tue 20 – Thu 22 Oct).
