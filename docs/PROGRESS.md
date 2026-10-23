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

### 24 Oct: searched for better data and tried more models. Nothing beat the current ones.

**More recent cluster history** is what would help most: the archive has only 63 lists for 2021–26, the years
after Wolbachia changed where dengue happens. There is no more to be had:

| Source | Result |
|---|---|
| Internet Archive (all 112 captures of the page) | Seed already has 79 usable ones; 17 more are a different page ("clusters under surveillance") and 1 repeats a date |
| data.gov.sg version history | Lists ~1 version a day, but the API serves only the current file (the `version` parameter is ignored) |
| GitHub (other projects) | No 2021–26 history; other projects reuse the same sgcharts 2013–20 archive |

**More models** (development backtest only; the test years were not used):

| | High-risk F1 | New clusters caught by top 5 |
|---|---|---|
| Current models | 0.838 | **0.530** |
| + how long the area has been active | 0.840 | – |
| Newer years weighted more (half-life 3 / 1.5 years) | 0.839 / 0.838 | 0.526 / 0.524 |
| LightGBM | 0.837 | – |
| LightGBM ranking objective (trained for top 5) | – | 0.503 |
| Model + "past clusters" blend | – | 0.522 |
| Baseline ("same as now" / "past clusters here") | 0.816 | 0.484 |

**Transfer learning from southern Taiwan: tried, does not help.** Taiwan CDC publishes every dengue case since
1998 with onset date, report date and location. The download used is the Internet Archive copy of
`od.cdc.gov.tw/eic/Dengue_Daily.csv` from 29 Jul 2025, because the CDC server doesn't answer from here. From
98,538 local cases in Tainan, Kaohsiung and Pingtung, the same table as Singapore's was built:
- clusters by NEA's rule (150 m, 14 days), counting a case only once reported;
- twice-weekly lists across 108 districts, 2002 – Jul 2025;
- 258,660 labelled rows, 2,720 High outcomes, 1,815 new clusters.

Each Singapore backtest year only used Taiwan data from before that year.

| Development backtest | High-risk F1 | New clusters caught |
|---|---|---|
| Current Singapore models | 0.838 | **0.530** |
| Taiwan model applied to Singapore as-is | 0.806 | 0.366 |
| Pooled Singapore + Taiwan (Taiwan weight 0.05 / 0.2 / 1) | 0.848 / 0.849 / **0.851** | 0.530 / 0.530 / 0.520 |
| Singapore model pulled toward Taiwan's weights (weak → strong) | 0.848 / 0.844 / 0.848 | 0.530 / 0.530 / 0.435 |
| Taiwan prediction as an extra input | 0.837 | 0.530 |
| LightGBM pretrained on Taiwan, fine-tuned on Singapore | 0.850 | 0.505 |
| Singapore only, same 8 features as the pooled model (no Taiwan) | 0.849 | – |

Most of the risk-level gain comes from using fewer features (dropping homes, Wolbachia and season), not from
Taiwan. Taiwan added only +0.002 on top. The best method (pooled, weight 1) was tested once on Singapore 2024–26,
next to its no-Taiwan twin:

| Test 2024–26 | Called High | Right | Precision | Recall | F1 |
|---|---|---|---|---|---|
| Current model (in production) | 68 | 44 | 64.7% | 61.1% | **0.629** |
| Pooled with Taiwan | 59 | 40 | 67.8% | 55.6% | 0.611 |
| Singapore only, 8 features | 66 | 43 | 65.2% | 59.7% | 0.623 |
| Same as now | 71 | 45 | 63.4% | 62.5% | 0.629 |

Neither beats the current model, so **production is unchanged**. Taiwan's dengue behaves differently: it is
seeded by imported cases, has no Wolbachia, and shows weaker persistence ("same as now" F1 0.76 vs 0.82). The test
years have now been used for this second comparison too, so treat later test comparisons with more caution.
Scripts: `taiwan_panel.py`, `transfer.py`, `transfer_test.py` (kept outside the repo, not adopted).

**Small-data techniques: every one tested in code, none adopted.** First, a check for overfitting, which is what
these techniques fix. The models score about the same on rows they trained on as on unseen years: risk level
0.849 vs 0.838, new clusters 0.526 vs 0.530. They have 1,200–3,450 training rows per input. So they are not
overfitting, and there is no missing data (0 missing values in the inputs). Development backtest only:

| Technique | Risk level, High F1 (current 0.838) | New clusters, caught by top 5 (current 0.530) |
|---|---|---|
| Outlier removal: IQR 1.5× / 3× | 0.709 / 0.812 | 0.528 / 0.530 |
| Outlier removal: Mahalanobis, top 2.5% | 0.841 | 0.528 |
| Stronger L2 (C = 0.1 / 0.01) | 0.840 / 0.838 | 0.528 / 0.524 |
| L1 / Lasso | 0.838–0.841 | 0.524 |
| PCA (5 components) | 0.820 | – |
| LDA | 0.823 | 0.518 |
| SVM (RBF) | 0.828 | 0.385 |
| Random forest, depth 4 | 0.839 | 0.524 |
| XGBoost, depth 3, `reg_alpha` 1, `reg_lambda` 5 | 0.842 | 0.507 |
| Bagging (50 bootstrapped logistic models) | 0.840 | 0.530 |
| SMOTE (imbalanced-learn, k = 5) | 0.837 | 0.526 |
| Noise injection (2 noisy copies, 10% σ) | 0.839 | 0.530 |
| Per-area embedding (one-hot area + L2) | 0.836 | 0.503 |
| Gaussian copula synthetic rows (2× data) | 0.812 | 0.379 |
| CTGAN synthetic rows (2× data, 50 epochs) | 0.829 | 0.520 |
| Transfer learning from Taiwan (above) | 0.837–0.851 | 0.366–0.530 |

The best gain is +0.004, which is noise. Outlier removal by IQR throws away the epidemics, which are what's being
predicted. Synthetic rows dilute real patterns: copula rows push new clusters below "past clusters here" (0.484). CTGAN does less damage but still loses, and 51.8% of its synthetic rows break basic rules of the data
(for example, "no cluster today" alongside a cluster count above zero, or more recent cases than total cases).

Validation schemes, scored on the same rows: random 10-fold ×3 gives 0.848, leave-one-list-out 0.847, our
forward backtest 0.838. Random folds are slightly optimistic, as expected when a model may train on later years.
**All three overestimate the real 2024–26 result (0.629)** because none of them contains post-Wolbachia years.
No cross-validation scheme can fix that.

Every variant is within noise of the current model or worse. **The limit is the data, not the model.** With
area-level cluster lists that are sparse after 2020, this is about as good as it gets. What could change that:

1. **Ask NEA for its historical cluster lists for 2021–26.** NEA has every daily list. This is the single
   biggest lever.
2. **Our own daily captures,** running since 15 Oct 2026. Every day adds the history that's missing. In 6–12
   months there will be enough Wolbachia-era data to retrain on, but not by Demo Day.
3. **Local weather** (station rainfall, 2016–24 on data.gov.sg, later years via the real-time API) is the only
   weather that could help rank areas. Expected gain is small, given how weak the national signal is.

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
  55 areas, code commit `00a8567`, MLflow run `6b21a32982da4f6c8510e2042274cfc8` (experiment `dengueradar`).
- It was made by the **previous** model and stays as it is (the table is append-only). Score it as locked. It is
  the only forecast that can be spot-checked by Demo Day.
- **Checked it can't be edited:** the table has `delta.appendOnly = true`, and a test DELETE was refused
  (`DELTA_CANNOT_MODIFY_APPEND_ONLY`).
- Based on NEA's list as at 16 Oct. Scored against the first NEA list 14–35 days later, so from about 30 Oct. On
  Demo Day it can only be spot-checked.
- **High:** Ang Mo Kio, Jurong West, Bishan. **Medium:** Serangoon. **Most likely new clusters:** Sengkang, Hougang,
  Bukit Batok, Yishun, Woodlands.

## The second locked forecast (24 Oct, fixed model)

- **Locked Sat 24 Oct 2026, 00:07 SGT** by a manual run of `dengue-lock-forecast` from the team account (job run
  `517401153971366`; Claude did not start it). Forecast id `5cb34046-67e4-4631-b783-e7bc9a315538`, 55 areas, code
  commit `e2564d7`, MLflow run `46b1cb992e6247ec8ba551d732f89509`, table version 2.
- **MLflow matches this report exactly:** it chose "clusters & history" (High ≥ 0.4067, Medium ≥ 0.3102) and
  "+ neighbours", on 1,045 test rows. Test High precision 0.647 and F1 0.629, new clusters 0.417, and identical
  bootstrap intervals. One exception: its "Chance" metric (0.153) is the old single random draw, because the
  run predates the expected-chance fix. Quote 12%.
- Based on NEA's list as at 22 Oct. **Scored from 5 Nov** (first list 14–35 days later, until 26 Nov), so after
  Demo Day.
- **High:** Ang Mo Kio (P = 0.78), Jurong West (0.62). **Medium:** Bishan (High today, 2 recent cases). **Low:**
  Outram (Medium today). **Most likely new clusters:** Serangoon, Sengkang, Hougang, Bukit Batok, Jurong East.

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
4. **Who runs jobs, and when?** The 23 Oct 23:40 ingest and the 24 Oct 00:04 lock were both started by hand from
   the shared account while code was being deployed. Agree that one person runs locks, after a commit and a
   deploy, and posts the forecast id. A weekly schedule for the lock job would remove the guesswork.
