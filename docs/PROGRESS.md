# DengueRadar: progress report

**As of Fri 23 Oct 2026, 23:45 SGT · sprint day 12 of 14 · Demo Day Tue 27 Oct**

## In one paragraph

The data side is built and running on Databricks. Every day, a job downloads NEA's dengue clusters and 31 other
sources, cleans them into one cluster history from 2015 to today, and turns that into the table the model learns
from. **The 19 Oct report overstated the model.** Its "69% of High-risk areas caught vs 62%" came from calling far
more areas High: precision fell to 56%, against 62% for simply carrying today's level forward. Counted properly, it
was no better than "same as now". Today the High-risk model was fixed (precision **65%**, same hits, fewer false
alarms) and the new-cluster score improved to **43%** (from 39%). Rainfall and temperature were added and tested
fairly. **They do not improve either model**, so the models don't use them. Both models beat their simple baselines on
the test years, but by margins that 17 test lists cannot prove. Say so if asked.

## Where we are against the plan

| Sprint days (slide 3) | Planned | Status |
|---|---|---|
| 1–3 (12–14 Oct) | Ingest + clean | ✅ Done, 3 days late (finished 15–17 Oct) |
| 4–5 (15–16 Oct) | Baselines; lock 12 Oct | ❌ 12 Oct lock missed (nothing was built yet) |
| 6–8 (17–19 Oct) | Models; lock 19 Oct | ✅ Models trained and tested; ✅ forecast locked 19 Oct, 18:44 |
| 9–11 (20–22 Oct) | Planner, dashboard, Genie | Not started (no code in the repo as of 23 Oct) |
| 12–14 (23–26 Oct) | Results + rehearsal | 23 Oct: models fixed and re-tested, weather added (below) |

## What changed on 23 Oct (model fix + weather)

### What was wrong

1. **The High-risk claim counted hits but not false alarms.** The model was trained with `class_weight="balanced"`
   and then picked the most likely level. That makes "High" far more likely than it really is. On 2024–26 it called
   **84** area-fortnights High to catch 47; "same as now" called **68** and caught 42. Recall went up (69% vs 62%)
   only because precision went down (56% vs 62%). High F1 was 0.618 for both, a tie.
2. **The new-cluster feature set was chosen on too little data.** 2022–23 has just 11 labelled lists and 42 new
   clusters (2023 has none: the archive is too thin). One cluster more or less changes the pick.
3. **Weather was never used.** It was downloaded daily but not in the model table.

### What was done

- **Rebuilt the whole pipeline locally to check every number before changing anything.** It reproduces production
  exactly: 15,345 labelled pairs, 10,270 HDB blocks, 161 unplaced streets, 69 new-cluster test cases, and the 19 Oct
  test scores to the decimal (69.1% / 56.0% High, 39.1% vs 37.7% new clusters). All labelled rows come from the
  archives (sgcharts + Internet Archive, last list 4 Sep 2026), so local and Databricks results are the same data.
- **Weather in the gold table** (`pipelines/gold_area_fortnight.sql`). New `gold_weather_monthly`: Changi rainfall,
  rain days, mean and mean-daily-max temperature, and humidity, 1982 → Sep 2026, as anomalies against the 1982–2014
  average for the same calendar month. `gold_area_fortnight` gets 9 weather columns, using only what was published
  on each list date: the last month that ended ≥15 days earlier (NEA published Sep 2026 on 8 Oct) and the 3 months
  up to it. The SQL text was run in DuckDB (with stand-ins for 4 Databricks function names) and matches an
  independent Python version on all 315 list dates.
- **High-risk fix** (`src/dengue_model/forecast.py`): no class weighting. The model calls an area High when
  P(High) ≥ a threshold tuned for High F1, and Medium by a second threshold. Both thresholds are set on development
  years only (High ≥ 0.407, Medium ≥ 0.310).
- **Sounder model choice:** feature sets and thresholds are now picked on a backtest of the development years.
  It predicts 2018, 2019, 2020 and 2022, each from only the years before. That gives 483 new clusters and
  1,061 High outcomes to choose on, instead of 42 new clusters. Then the models retrain on 2015–23 and are tested **once** on
  2024–26.
- **Weather tested fairly:** "+ weather" is a candidate set for both models, re-checked by every lock run and
  logged to MLflow.
- **More honest reporting:** every model and baseline is reported with High precision, recall and F1, plus
  precision@5 for new clusters. A 90% bootstrap interval (resampling test lists) shows how sure the gain over
  each baseline is. All of it goes to MLflow.

### Weather: tested, not used

| Development backtest (2018–22) | Without weather | With weather |
|---|---|---|
| High risk: F1 (precision / recall) | **0.838** (0.836 / 0.840) | 0.834 (0.832 / 0.836) |
| New clusters caught by top 5 | **53.0%** | 51.3% |

Also tried and rejected, all on development years only: weather from 2–3 months earlier (dengue lags weather),
weather × past-clusters interactions, and gradient boosting. None beat the models without weather. The reason:
the weather data is **one national monthly reading**, the same for all 55 areas on a date. It can shift overall
risk, but it can't say *which* area is at risk, and that is what both scores measure. Its link to next-fortnight
national change is weak too (correlation ≤ 0.15; hotter months 2–3 months earlier slightly precede growth). To
help, weather would need to be **local**: the station-level rainfall/temperature datasets (~18 GB, 2016–2024,
already in the source registry with `include_large`), matched to each area's nearest station.

Other ideas that also failed to beat "history + neighbours" on development years: cases within 1–2 km of an
area's homes, cases spilling over from a neighbour's cluster, history measured in days instead of lists, and
cluster size and growth.

## Model results (2024–26, years the model never saw while learning)

17 NEA lists, 68 High outcomes, 69 new clusters.

**High-risk areas** (is the area High at the next list 14–35 days later?)

| | Called High | Right | Precision | Recall | F1 |
|---|---|---|---|---|---|
| **New model (23 Oct)** | 65 | 42 | **64.6%** | 61.8% | **0.632** |
| Same as now (baseline) | 68 | 42 | 61.8% | 61.8% | 0.618 |
| Previous model (19 Oct) | 84 | 47 | 56.0% | 69.1% | 0.618 |
| Gradient boosting | 63 | 41 | 65.1% | 60.3% | 0.626 |

- Precision is up 9 points on the previous model (56% → 65%), at the cost of 5 hits. Against "same as now" it makes
  the same 42 correct High calls with 3 fewer false alarms.
- Macro-F1 across Low/Medium/High: 0.643 (previous 0.634, same as now 0.617). Top-10 case capture: 76.4%.
- **90% interval for F1 minus "same as now": −0.03 to +0.07.** The gain is not proven. "Same as now" is very hard
  to beat because clusters last weeks.

**New clusters** (areas clear today; is there a cluster at the next list?)

| | Caught by top 5 | Of 69 | Precision of picks | ROC-AUC |
|---|---|---|---|---|
| **New model: history + neighbours** | **43.5%** | 30 | **35.3%** | 0.822 |
| Previous model (19 Oct) | 39.1% | 27 | 31.8% | 0.828 |
| Past clusters here (baseline) | 37.7% | 26 | 30.6% | 0.825 |
| + weather | 39.1% | 27 | 31.8% | 0.822 |
| Chance | 7.2% | 5 | 5.9% | ≈0.5 |

- 6× better than chance, and 4 more new clusters caught than ranking by past clusters.
- **90% interval for the gain over "past clusters here": −1 to +11 points.** Likely better, not proven.
- **Read this caveat before quoting 43%.** The "+ neighbours" set scored 43.5% in the 19 Oct test table (every
  set was shown there), and the new choice method was designed after that table had been seen. The method itself
  uses development years only, and it prefers "+ neighbours" clearly there (53.0% vs 50.9% for the old pick). But
  it is not a blind result. Safest wording: **"about 40–44%, 6× chance, ahead of 'past clusters here' but not
  significantly."**

## The first locked forecast (19 Oct, previous model)

- **Locked Mon 19 Oct 2026, 18:44 SGT** in `gold_sealed_forecasts`: forecast id `02bf3500-83ce-4931-9d89-301c13008ed4`,
  55 areas, code commit `00a8567`, MLflow run `6b21a32982da4f6c8510e2042274cfc8` (experiment `dengueradar`).
- It was made by the **previous** model and stays as it is (the table is append-only). Score it as locked. The
  next lock uses the fixed model, and its MLflow run records the thresholds and the chosen feature sets.
- **Checked it can't be edited:** the table has `delta.appendOnly = true`, and a test DELETE was refused
  (`DELTA_CANNOT_MODIFY_APPEND_ONLY`).
- Based on NEA's list as at 16 Oct. Scored against the first NEA list 14–35 days later, so from about 30 Oct. On
  Demo Day it can only be spot-checked.
- **High:** Ang Mo Kio, Jurong West, Bishan. **Medium:** Serangoon. **Most likely new clusters:** Sengkang, Hougang,
  Bukit Batok, Yishun, Woodlands.
- For comparison, the fixed model on today's page (as at 22 Oct, local run, **not locked**): High Ang Mo Kio and
  Jurong West. Bishan drops to Medium (2 recent cases) and Outram to Low. Top new-cluster picks: Serangoon,
  Sengkang, Hougang, Bukit Batok, Yishun.

## To do before the next lock (in this order)

1. `databricks bundle deploy -t prod`, so the workspace gets the new SQL, model code and notebook.
2. Run the `dengue-silver` pipeline (or wait for the daily job) so `gold_weather_monthly` and the weather columns
   exist. **The lock job fails if they're missing.**
3. Run `dengue-lock-forecast`. Check in MLflow that it chose "clusters & history" (High ≥ ~0.41) and
   "+ neighbours", and that its test numbers match the tables above. They should, because the labelled data is the
   same. If they don't, quote the MLflow numbers, not these.

## Problems found

1. **19 Oct (fixed then):** a Figshare outage stopped the morning run. Frozen archives are now downloaded once and
   skipped afterwards.
2. **NEA's cluster page was stuck at "as at 16 Oct"** on 19 Oct. It has updated since: today's page reads "as at
   22 Oct".
3. **Silver ignores data.gov.sg lists after the first page capture,** so on days the page is stale the newest list
   lags (decision 1 below).
4. **23 Oct:** the High-risk headline was wrong (above). Fixed in code, and the docs say so.

## Not built yet

- Scoring locked forecasts against what actually happened (`track_record`); first possible on ~30 Oct, so Demo
  Day shows a spot-check
- "Why?" reasons per area
- Team planner, AI/BI dashboard, Genie space, SQL alert, AI weekly summary
- A weekly schedule for the lock job
- Local (station-level) weather, the only kind that could help rank areas (see above)

## Decisions needed from the team

1. **What do we use on days NEA's page is stale?** Options: (a) use data.gov.sg and estimate "recent cases" from how
   much each cluster grew over the last 14 days of daily captures, or (b) forecast only from page dates. Recommend (a).
2. **Slides and `docs/SOLUTION.md` need new numbers.** Both say "catches 69% vs 62%". Replace with "65% of High calls
   are right (same as now: 62%), with the same hits and fewer false alarms". Change "39%" to "about 40–44%, 6× chance,
   ahead of 'past clusters here' (38%) but not significantly". Slide 2's "Forecasts locked 12 & 19 Oct" should say
   one forecast, locked 19 Oct, spot-checked on Demo Day.
3. **How do we pitch it?** Suggest leading with the honest test and the locked, uneditable forecast. The models
   match or slightly beat strong simple rules. Then the team planner, which turns a ranked list into inspection
   routes. Mention weather as tested and not yet useful at national level, with local weather as next.
