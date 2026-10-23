# DengueRadar: progress report

**As of Sat 24 Oct 2026, 00:15 SGT · sprint day 13 of 14 · Demo Day Tue 27 Oct**

## In one paragraph

The data side is built and running on Databricks. Every day, a job downloads NEA's dengue clusters and 31 other
sources, cleans them into one cluster history from 2015 to today, and turns that into the table the model learns
from. **The 19 Oct report overstated the model.** Its "69% of High-risk areas caught vs 62%" came from calling far
more areas High: 87 calls to get 49 right, while simply carrying today's level forward gets 45 right from 71 calls.
On F1 it never beat "same as now". The High-risk model is now fixed: same accuracy as "same as now", with precision
back up from 56% to **65%** and a third fewer false alarms than before. The new-cluster score improved to **42%** of
new clusters in its top 5 picks (old model 38%, "past clusters here" 38%, chance 12%). Rainfall and temperature are
now in the model table and were tested fairly. **They do not improve either model**, so the models don't use them.
None of the gains over the simple rules can be proven with 19 test lists. Say so if asked.

## Where we are against the plan

| Sprint days (slide 3) | Planned | Status |
|---|---|---|
| 1–3 (12–14 Oct) | Ingest + clean | ✅ Done, 3 days late (finished 15–17 Oct) |
| 4–5 (15–16 Oct) | Baselines; lock 12 Oct | ❌ 12 Oct lock missed (nothing was built yet) |
| 6–8 (17–19 Oct) | Models; lock 19 Oct | ✅ Models trained and tested; ✅ forecast locked 19 Oct, 18:44 |
| 9–11 (20–22 Oct) | Planner, dashboard, Genie | Not started (no code in the repo as of 24 Oct) |
| 12–14 (23–26 Oct) | Results + rehearsal | 23–24 Oct: models fixed and re-tested, weather added, deployed to prod (below) |

## What changed on 23–24 Oct (model fix + weather)

Commits `e2564d7` (models, weather) and `1b8bf6f` (seed restore, below), plus the chance-baseline fix. All are
deployed to prod. The `dengue-silver` pipeline has rebuilt `gold_area_fortnight` with the weather columns.

### What was wrong

1. **The High-risk claim counted hits but not false alarms.** The model was trained with `class_weight="balanced"`
   and then picked the most likely level. That makes "High" far more likely than it really is, so the model
   over-called it: precision 56% vs 63% for "same as now". Recall looked better only because of that.
2. **The new-cluster feature set was chosen on too little data.** 2022–23 has just 11 labelled lists and 42 new
   clusters (2023 has none: the archive is too thin). One cluster more or less changes the pick.
3. **"5× better than chance" was a lucky number.** "Chance" was one random draw of picks. The exact expected value
   is 12%, not 7%. The code now reports the expected value.
4. **Weather was never used.** It was downloaded daily but not in the model table.

### What was done

- **Rebuilt the whole pipeline locally to check every number before changing anything.** It reproduced
  production exactly. Before the change it matched 15,345 labelled pairs and the 19 Oct test scores to the decimal.
  On 24 Oct, after the seed restore, it matches prod's gold table again: 17,600 rows, 15,455 labelled, and every
  weather column to 6 decimals.
- **Weather in the gold table** (`pipelines/gold_area_fortnight.sql`). New `gold_weather_monthly`: Changi rainfall,
  rain days, mean and mean-daily-max temperature, and humidity, 1982 → Sep 2026, as anomalies against the 1982–2014
  average for the same calendar month. `gold_area_fortnight` gets 9 weather columns, using only what was published
  on each list date: the last month that ended ≥15 days earlier (NEA published Sep 2026 on 8 Oct) and the 3 months
  up to it.
- **High-risk fix** (`src/dengue_model/forecast.py`): no class weighting. The model calls an area High when
  P(High) ≥ a threshold tuned for High F1, and Medium by a second threshold. Both thresholds are set on development
  years only (High ≥ 0.407, Medium ≥ 0.310).
- **Sounder model choice:** feature sets and thresholds are now picked on a backtest of the development years.
  It predicts 2018, 2019, 2020 and 2022, each from only the years before. That gives 483 new clusters and 1,061
  High outcomes to choose on, instead of 42 new clusters. Then the models retrain on 2015–23 and are tested **once**
  on 2024–26.
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

On 2024–26 the weather variant also does worst of all new-cluster sets (26 of 72 caught). Also tried and rejected,
all on development years only: weather from 2–3 months earlier (dengue lags weather), weather × past-clusters
interactions, and gradient boosting. None beat the models without weather. The reason: the weather data is **one
national monthly reading**, the same for all 55 areas on a date. It can shift overall risk, but it can't say
*which* area is at risk, and that is what both scores measure. Its link to next-fortnight national change is weak
too (correlation ≤ 0.15; hotter months 2–3 months earlier slightly precede growth). To help, weather would need to
be **local**: the station-level rainfall/temperature datasets (~18 GB, 2016–2024, already in the source registry
with `include_large`), matched to each area's nearest station.

Other ideas that also failed to beat "history + neighbours" on development years: cases within 1–2 km of an
area's homes, cases spilling over from a neighbour's cluster, history measured in days instead of lists, and
cluster size and growth.

## Model results (2024–26, years the model never saw while learning)

19 NEA lists, 72 High outcomes, 72 new clusters (814 clear area-fortnights). Production's current data, which
includes the 5 restored archive captures.

**High-risk areas** (is the area High at the next list 14–35 days later?)

| | Called High | Right | Precision | Recall | F1 |
|---|---|---|---|---|---|
| **New model** | 68 | 44 | **64.7%** | 61.1% | **0.629** |
| Same as now (baseline) | 71 | 45 | 63.4% | 62.5% | 0.629 |
| Previous model (19 Oct) | 87 | 49 | 56.3% | 68.1% | 0.616 |
| Gradient boosting | 68 | 44 | 64.7% | 61.1% | 0.629 |

- **The fix:** precision up from 56% to 65%, and false alarms down from 38 to 24 for 5 fewer hits.
- **Against "same as now" it is a tie** (F1 0.629 each; 1 fewer hit, 2 fewer false alarms). 90% interval for the
  difference: −0.05 to +0.06. Clusters last weeks, so "same as now" is very hard to beat. The model's value is in
  its probabilities and its reasons, not in beating that rule. Don't claim it does.
- Macro-F1 across Low/Medium/High: 0.652 (previous 0.645, same as now 0.632). Top-10 case capture: 79.0%.

**New clusters** (areas clear today; is there a cluster at the next list?)

| | Caught by top 5 | Of 72 | Precision of picks | ROC-AUC |
|---|---|---|---|---|
| **New model: history + neighbours** | **41.7%** | 30 | **31.6%** | 0.833 |
| Past clusters here (baseline) | 37.5% | 27 | 28.4% | 0.830 |
| Previous model (19 Oct set) | 37.5% | 27 | 28.4% | 0.842 |
| + weather | 36.1% | 26 | 27.4% | 0.829 |
| Chance (expected) | 12.0% | 8.6 | 8.8% | 0.5 |

- About 3.5× chance, and 3 more new clusters caught than ranking by past clusters.
- **90% interval for the gain over "past clusters here": −3 to +9 points.** Likely better, not proven.
- Caveat: the "+ neighbours" set had already scored well in the 19 Oct test table (every set was shown there), and
  the new choice method was designed after that table had been seen. The method uses development years only, and
  it prefers "+ neighbours" clearly there (53.0% vs 50.9% for the old pick). But it is not a perfectly blind
  result. Safest wording: **"about 40%, 3–4× chance, ahead of ranking by past clusters (38%) but not
  significantly."**

## The first locked forecast (19 Oct, previous model)

- **Locked Mon 19 Oct 2026, 18:44 SGT** in `gold_sealed_forecasts`: forecast id `02bf3500-83ce-4931-9d89-301c13008ed4`,
  55 areas, code commit `00a8567`, MLflow run `6b21a32982da4f6c8510e2042274cfc8` (experiment `dengueradar`). It is
  still the only locked forecast (checked 24 Oct).
- It was made by the **previous** model and stays as it is (the table is append-only). Score it as locked. The
  next lock uses the fixed model, and its MLflow run records the thresholds and the chosen feature sets.
- **Checked it can't be edited:** the table has `delta.appendOnly = true`, and a test DELETE was refused
  (`DELTA_CANNOT_MODIFY_APPEND_ONLY`).
- Based on NEA's list as at 16 Oct. Scored against the first NEA list 14–35 days later, so from about 30 Oct. On
  Demo Day it can only be spot-checked.
- **High:** Ang Mo Kio, Jurong West, Bishan. **Medium:** Serangoon. **Most likely new clusters:** Sengkang, Hougang,
  Bukit Batok, Yishun, Woodlands.
- For comparison, the fixed model on NEA's page as at 22 Oct (local run, **not locked**): High Ang Mo Kio and
  Jurong West. Bishan drops to Medium (2 recent cases), Outram to Low. Top new-cluster picks: Serangoon, Sengkang,
  Hougang, Bukit Batok, Jurong East.

## Next step: lock a forecast with the fixed model

Everything is deployed and the gold table has the weather columns. Run `dengue-lock-forecast` (prod). Check in
MLflow that it chose "clusters & history" (High ≥ ~0.41) and "+ neighbours", and that its test numbers match the
tables above. They should, because it reads the same table. If they don't, quote the MLflow numbers. Its basis
will be the newest NEA list (22 Oct today). A lock can't be undone, so run it when the team agrees.

## Problems found

1. **19 Oct (fixed then):** a Figshare outage stopped the morning run. Frozen archives are now downloaded once and
   skipped afterwards.
2. **NEA's cluster page was stuck at "as at 16 Oct"** on 19 Oct. It has updated since: the newest list is 22 Oct.
3. **Silver ignores data.gov.sg lists after the first page capture,** so on days the page is stale the newest list
   lags (decision 1 below).
4. **23 Oct:** the High-risk headline was wrong (above). Fixed in code, and the docs say so.
5. **23 Oct, 23:40–23:58: an uncommitted seed change was nearly lost.** Someone on the team account added 5
   Internet Archive captures to the cluster seed in the workspace and ran the ingest, without committing to
   GitHub. The captures were 10 Jun 2024, 13 Sep 2024, 30 Apr 2025, 5 May 2025 and 6 Apr 2026, 156 clusters in
   all. Deploying `e2564d7` at 23:58 overwrote the workspace seed with the older file, and would have dropped the
   captures at the 11:30 run. **Restored** from the landed copy (sha256 `d2271935…`) in `1b8bf6f` and redeployed.
   The workspace, landing and repo now hold the same file. That deploy also deleted **2 workspace files that
   weren't in the repo**. Whoever edited the workspace directly: check nothing of yours is missing. **Rule from now
   on: commit to GitHub, then deploy. Never edit the bundle folder in the workspace by hand.** A deploy always
   overwrites it with the deployer's laptop.

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
2. **Slides and `docs/SOLUTION.md` need new numbers.** Both say "catches 69% vs 62%" and "39%, 5× chance". Replace
   with:
   - High risk: "as accurate as 'same as now', with 65% of High calls right".
   - New clusters: "about 40%, 3–4× chance, ahead of ranking by past clusters (38%), not significantly".
   - Slide 2's "Forecasts locked 12 & 19 Oct" should say one forecast, locked 19 Oct, spot-checked on Demo Day.
3. **How do we pitch it?** Suggest leading with the honest test and the locked, uneditable forecast. The models
   match or slightly beat strong simple rules. Then the team planner, which turns a ranked list into inspection
   routes. Mention weather as tested and not useful at national level, with local weather as next.
4. **When do we lock the next forecast?** See "Next step" above.
