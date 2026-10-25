"""Create or update the DengueRadar Genie space: ask the dashboard's questions in plain English.

    python scripts/build_genie_space.py --check     # run every example and benchmark query, change nothing
    python scripts/build_genie_space.py             # check, then create the space (or update it, found by title)
    python scripts/build_genie_space.py --ask       # also ask Genie each benchmark question and compare answers

Genie reads the same gold tables as the DengueRadar dashboard, so its answers move with every new
NEA list and every locked forecast. Uses your Databricks CLI login (`databricks auth login`).
The space is created through the Genie REST API rather than the bundle, so deploying it never
touches the jobs, pipeline or dashboard.
"""

import argparse
import json
import subprocess
import sys
import time

TITLE = "DengueRadar Genie"
DESCRIPTION = (
    "Ask about dengue in Singapore's 55 planning areas in plain English: active clusters and alerts, "
    "the newest locked two-week forecast, where new clusters may appear, this week's inspection team "
    "plan, how past forecasts scored, national weekly cases and weather."
)

# ---------------------------------------------------------------------------------------------
# Tables, with descriptions and column notes Genie reads alongside the Unity Catalog comments.
# ---------------------------------------------------------------------------------------------
LEVEL = "Risk level by NEA's alert rule: 2 = High (an active cluster with 10+ cases), 1 = Medium (any active cluster), 0 = Low (none)."
AREA = "Planning area name in UPPER CASE, e.g. 'ANG MO KIO'. Filter with upper(...) = upper('<name>')."

TABLES = {
    "gold_forecast_latest": (
        "THE CURRENT FORECAST. The newest locked two-week forecast, one row per planning area (55 rows). "
        "Use for: high-risk areas, the forecast, areas at risk, rising or falling risk, new-cluster watch, "
        "why an area is flagged. basis_date is the NEA list it was made from; it predicts each area's level "
        "on the first NEA list between score_from and score_until.",
        {
            "planning_area": (AREA, ["town", "estate", "neighbourhood", "area"]),
            "area": ("Planning area name in title case for display, e.g. 'Ang Mo Kio'.", []),
            "region": ("URA region, e.g. 'NORTH-EAST REGION'.", []),
            "level_today": (LEVEL + " Level on the basis_date list.", ["current level"]),
            "level_today_name": ("level_today as text: High, Medium or Low.", ["current risk", "today"]),
            "predicted_level": ("Forecast level for the scoring window: High, Medium or Low.", ["forecast", "predicted risk", "risk level"]),
            "predicted_level_num": ("predicted_level as 2/1/0; sort by it to list the riskiest areas first.", []),
            "same_as_now_level": ("Simple baseline: assumes today's level continues.", []),
            "p_high": ("Model probability (0-1) that the area is High on the outcome list. Show as a percentage.", ["chance of high risk", "probability"]),
            "p_medium": ("Model probability (0-1) of Medium.", []),
            "p_low": ("Model probability (0-1) of Low.", []),
            "new_cluster_rank": ("Rank 1 = most likely area to get a NEW cluster, among areas with no cluster today. NULL for areas that already have a cluster.", ["new cluster rank"]),
            "new_cluster_score": ("Model probability (0-1) of a new cluster appearing in this currently clear area.", []),
            "new_cluster_watch": ("True for the top 5 areas for a new cluster.", ["watch list"]),
            "direction": ("Rising, Falling or Same: forecast level compared with today's level.", ["trend"]),
            "why": ("Up to three plain-language reasons describing what the model was given for the area (not the model's weights).", ["reason", "explanation"]),
            "n_clusters": ("Active clusters in the area on the basis_date list.", []),
            "recent_cases": ("Cases with onset in the last 2 weeks in the area's clusters on the basis_date list.", []),
            "largest_cluster": ("Total cases in the area's largest active cluster on the basis_date list.", []),
            "recent_next_door": ("Recent cases in neighbouring planning areas on the basis_date list.", []),
            "wolbachia_share": ("Share (0-1) of the area's HDB homes inside Wolbachia release zones. Show as a percentage.", ["wolbachia coverage"]),
            "hist_rank": ("Percentile (0-1) of the area's long-run cluster history; 1 = most past clusters.", []),
            "basis_date": ("Date of the NEA cluster list the forecast was made from.", ["data as of"]),
            "locked_at_utc": ("When the forecast was locked (UTC). Locked forecasts can never be edited.", []),
            "score_from": ("First day of the scoring window (basis_date + 14 days).", []),
            "score_until": ("Last day of the scoring window (basis_date + 35 days).", []),
        },
    ),
    "silver_clusters": (
        "Every dengue cluster on every NEA cluster list, 2013 to today. Use for: which clusters are active now, "
        "cluster localities, cases per cluster, red/yellow alerts. One row per cluster per list date, so the same "
        "outbreak appears on many lists: for 'now' filter snapshot_date = the newest snapshot_date.",
        {
            "cluster_id": ("Unique per cluster per list (it contains the list date); do not count distinct cluster_id across lists to count outbreaks.", []),
            "snapshot_date": ("Date of the NEA cluster list. Lists are irregular in the archive (2013-2020 and 2021-26 captures), daily from October 2026.", ["list date"]),
            "source": ("sgcharts (2013-2020 archive), wayback (2021-2026 Internet Archive captures), live / nea_page (daily captures from October 2026).", []),
            "locality": ("Streets and blocks NEA lists for the cluster.", ["location", "address", "streets"]),
            "recent": ("Cases with onset in the last 2 weeks.", ["recent cases", "new cases"]),
            "total": ("Total cases since the cluster started.", ["cluster size", "cases"]),
            "alert": ("red = cluster with 10 or more cases (High), yellow = fewer than 10 cases.", ["alert level"]),
            "planning_area": (AREA, ["town", "estate", "area"]),
            "is_active": ("True for clusters on the list (all rows are active).", []),
            "cases_in_wolbachia_zone_today": ("Located cases inside a Wolbachia release zone.", []),
        },
    ),
    "gold_area_levels": (
        "Every planning area's NEA risk level on every cluster list date (55 rows per list). Use for: current "
        "levels of all areas, history of an area's level, how often an area had clusters.",
        {
            "snapshot_date": ("Date of the NEA cluster list.", ["list date"]),
            "planning_area": (AREA, ["town", "estate", "area"]),
            "level": (LEVEL, ["risk level", "alert level"]),
            "n_clusters": ("Active clusters in the area on that list.", []),
            "recent_cases": ("Cases with onset in the last 2 weeks across the area's clusters.", []),
            "largest_cluster": ("Total cases of the area's largest cluster on that list.", []),
        },
    ),
    "gold_track_record": (
        "Every locked forecast (55 areas each) next to what NEA's list showed 14-35 days later. Use for: how past "
        "forecasts did, live accuracy, whether a forecast came true. status = waiting until the outcome list exists.",
        {
            "forecast_id": ("One id per locked forecast.", []),
            "status": ("scored or waiting.", []),
            "predicted_level": ("What the forecast said.", []),
            "outcome_level": ("What actually happened: level on the first NEA list in the scoring window.", ["actual", "outcome"]),
            "model_right": ("predicted_level = outcome_level (NULL while waiting).", []),
            "same_as_now_right": ("The 'same as now' baseline was right (NULL while waiting).", []),
            "new_cluster_appeared": ("For areas clear on the basis date: a cluster appeared by the outcome list.", []),
            "new_cluster_rank": ("The forecast's new-cluster rank for the area (top 5 = watch list).", []),
            "planning_area": (AREA, []),
        },
    ),
    "gold_model_test_headline": (
        "Headline test of the model against simple rules: every year from 2020 predicted using only earlier years. "
        "Use for: how accurate is the model, is it better than simple rules. Always report the model, the baseline "
        "and the 90% interval of the difference together.",
        {
            "question": ("What is measured: 'High-risk areas (F1)' or 'New clusters caught by top 5'.", []),
            "model": ("Model score (0-1). Show as a percentage.", []),
            "baseline_name": ("The simple rule compared with.", []),
            "baseline": ("Baseline score (0-1).", []),
            "diff_p05": ("Lower end of the 90% bootstrap interval for model minus baseline. If it is below 0 the gain is not proven.", []),
            "diff_p95": ("Upper end of that interval.", []),
            "test": ("Years tested.", []),
            "test_lists": ("Number of NEA lists in the test.", []),
        },
    ),
    "gold_model_test_risk": (
        "Test results for the High/Medium/Low risk forecast, every model and baseline (same test as gold_model_test_headline).",
        {
            "high_precision": ("Of areas called High, share that were High.", []),
            "high_recall": ("Of areas that were High, share called High.", ["high-risk areas caught"]),
            "high_f1": ("Balance of precision and recall for High.", []),
            "macro_f1": ("F1 averaged over High, Medium, Low.", []),
        },
    ),
    "gold_model_test_new_clusters": (
        "Test results for the new-cluster watch list (areas with no cluster today), every score and baseline.",
        {
            "caught_at_5": ("Share of new clusters that appeared in the 5 top-ranked areas.", ["new clusters flagged ahead"]),
            "precision_at_5": ("Share of top-5 picks that got a new cluster.", []),
            "n_new": ("New clusters in the test.", []),
        },
    ),
    "gold_model_test_by_year": (
        "The same test, broken down by year.",
        {
            "high_f1": ("Model High F1 that year.", []),
            "same_as_now_high_f1": ("'Same as now' baseline High F1 that year.", []),
            "caught_at_5": ("Model: share of new clusters in its top 5.", []),
            "past_clusters_caught_at_5": ("Baseline 'past clusters here': share of new clusters in its top 5.", []),
        },
    ),
    "gold_team_plan": (
        "This week's inspection plan: one site per team (6 teams), an anchor HDB block plus every priority block "
        "within 300 m, no overlaps. Use for: where teams should go, how many homes they reach.",
        {
            "team": ("Team number 1-6.", []),
            "anchor": ("The HDB block the site is centred on.", ["site"]),
            "area": ("Planning area of the site, title case.", []),
            "blocks": ("Priority HDB blocks in the site.", []),
            "homes": ("HDB homes in those blocks.", []),
            "blocks_near_cluster": ("Blocks within 300 m of a cluster on the newest list.", []),
            "homes_in_wolbachia": ("Homes inside Wolbachia release zones.", []),
            "cluster_list_date": ("NEA list the plan used.", []),
            "forecast_basis_date": ("Locked forecast the plan used.", []),
        },
    ),
    "gold_block_priority": (
        "HDB blocks worth inspecting this week, with a priority weight. Use for: which blocks or streets to inspect in an area.",
        {
            "blk_no": ("Block number.", ["block"]),
            "street": ("Street name.", []),
            "planning_area": (AREA, ["town", "area"]),
            "homes": ("HDB homes in the block.", []),
            "weight": ("Priority: homes x3 near a cluster, x2 near a high-Aedes area, x1 in a flagged area; halved in Wolbachia zones. Higher = inspect first.", ["priority"]),
            "reason": ("Why the block is on the list.", []),
        },
    ),
    "gold_national_weekly": (
        "Weekly dengue cases for all of Singapore by epidemiological week, 2012 to the latest bulletin, no gaps. "
        "Use for: national trend, cases last week, this year vs last year.",
        {
            "week_end": ("The Saturday that ends the epidemiological week.", ["week"]),
            "year": ("Epidemiological year.", []),
            "week": ("Epidemiological week number.", []),
            "cases": ("Dengue cases reported that week, nationally.", []),
        },
    ),
    "gold_weather_monthly": (
        "Monthly national weather (Changi station): rainfall, rain days, temperature, humidity, each with its "
        "anomaly from the 1982-2014 average for that calendar month.",
        {
            "month": ("First day of the month.", []),
            "rain_mm": ("Total rainfall (mm).", ["rainfall"]),
            "rain_anom": ("Rainfall minus the 1982-2014 average for that month (mm); positive = wetter than usual.", []),
            "temp_mean": ("Mean temperature (deg C).", ["temperature"]),
            "temp_anom": ("Mean temperature minus the 1982-2014 average for that month (deg C).", []),
            "rh_anom": ("Relative humidity minus the 1982-2014 average (percentage points).", []),
        },
    ),
    "bronze_nea_high_aedes_areas_latest": (
        "NEA's current high Aedes mosquito population areas. description holds the area code and streets; there is "
        "no planning_area column.",
        {
            "description": ("Area code and streets, e.g. 'CO341 - Aroozoo Ave / Jln Lepas'.", ["streets", "location"]),
            "fmel_upd_d": ("Last updated, yyyyMMddHHmmss text.", []),
        },
    ),
    "silver_planning_areas": (
        "Singapore's 55 URA planning areas with region and centroid.",
        {
            "planning_area": (AREA, ["town", "estate", "area"]),
            "region": ("URA region.", []),
            "geometry_geojson": ("Boundary polygon. Never select it.", []),
        },
    ),
}

JOINS = [  # (left, right, condition, relationship)
    ("gold_area_levels", "silver_planning_areas", "planning_area", "MANY_TO_ONE"),
    ("silver_clusters", "silver_planning_areas", "planning_area", "MANY_TO_ONE"),
    ("gold_forecast_latest", "gold_area_levels", "planning_area", "ONE_TO_MANY"),
]

INSTRUCTIONS = """\
You answer questions about dengue in Singapore for DengueRadar, using the tables provided.

Risk levels follow NEA's alert rule: High = a planning area has an active cluster with 10 or more cases (red alert); Medium = it has any active cluster (yellow alert); Low = no active cluster. Numeric levels are 2 = High, 1 = Medium, 0 = Low.

Which table to use:
* Forecast, high-risk areas, areas at risk, next two weeks, rising risk, why an area is flagged: gold_forecast_latest. Always show basis_date (the NEA list it was made from) and the window score_from to score_until.
* New clusters / where dengue may spread next: gold_forecast_latest WHERE new_cluster_watch, ordered by new_cluster_rank. It only ranks areas with no cluster today.
* Clusters, cases and alerts right now: silver_clusters WHERE snapshot_date = (SELECT max(snapshot_date) FROM silver_clusters). Area levels right now: gold_area_levels on its newest snapshot_date. The newest list can be newer than the forecast's basis_date; say which date you used.
* History of an area: gold_area_levels. To say how often an area had clusters, use the share of lists with level > 0, because lists are irregularly spaced. Never count distinct cluster_id across lists: the same outbreak appears on many lists.
* National cases and trend: gold_national_weekly (weekly, all of Singapore). There are no case counts by planning area other than cluster cases in silver_clusters.
* Inspection teams, sites, homes reached: gold_team_plan. Blocks to inspect: gold_block_priority ordered by weight DESC.
* Accuracy, how good the model is: gold_model_test_headline (always report model, baseline and the 90% interval together; if diff_p05 < 0 say the gain is not statistically proven). Details: gold_model_test_risk, gold_model_test_new_clusters, gold_model_test_by_year. How locked forecasts did live: gold_track_record (only status = 'scored' rows have an outcome).
* Weather: gold_weather_monthly (national, monthly). Mosquito hotspots: bronze_nea_high_aedes_areas_latest.

Conventions:
* planning_area is UPPER CASE. Match area names case-insensitively: upper(planning_area) = upper('<name>'). Users say town, estate or neighbourhood for planning area. Show the title-case area column, or initcap(planning_area), in answers.
* Show probabilities (p_high, new_cluster_score, wolbachia_share, model scores) as percentages rounded to whole numbers. If a probability rounds to 0%, write "under 1%", never "0%".
* Name test scores correctly: high_f1 is an F1 score for High-risk areas; never call it accuracy. caught_at_5 is the share of new clusters that were in the top 5 areas.
* When asked how accurate or how good the forecast is, judge each row of gold_model_test_headline on its own: if diff_p05 < 0, say the model scores about the same as that baseline (the gain is not proven); if diff_p05 >= 0, say it beats that baseline by diff_p05 to diff_p95 points. Never claim the model is better overall when one row is not proven.
* Sort risk lists by predicted_level_num DESC, then p_high DESC.
* Never select geometry_geojson columns.
* A forecast is a probability, not a certainty. Never describe an area as safe or say it will have no clusters: Low means the model does not expect a cluster on NEA's list, and new clusters can still appear. For a Low area, mention its new_cluster_rank.
* For medical questions (symptoms, treatment), say to see a doctor and check NEA's and MOH's websites; the data here is about clusters and risk, not individual health.
"""

# (question, sql) pairs. {s} is replaced by catalog.schema.
EXAMPLES = [
    ("Which areas are forecast to be High risk in the next two weeks?", """
SELECT area, region, level_today_name AS level_today, predicted_level,
       round(100 * p_high) AS chance_of_high_pct, why, basis_date, score_from, score_until
FROM {s}.gold_forecast_latest
WHERE predicted_level = 'High'
ORDER BY p_high DESC"""),
    ("Show the risk forecast for every area", """
SELECT area, region, level_today_name AS level_today, predicted_level, direction,
       round(100 * p_high) AS chance_of_high_pct, CAST(new_cluster_rank AS INT) AS new_cluster_rank, why,
       basis_date, score_from, score_until
FROM {s}.gold_forecast_latest
ORDER BY predicted_level_num DESC, p_high DESC, new_cluster_rank"""),
    ("Where could new dengue clusters appear?", """
SELECT CAST(new_cluster_rank AS INT) AS rank, area, region, round(100 * new_cluster_score) AS chance_pct, why,
       basis_date, score_from, score_until
FROM {s}.gold_forecast_latest
WHERE new_cluster_watch
ORDER BY new_cluster_rank"""),
    ("Is Tampines at risk?", """
SELECT area, level_today_name AS level_today, predicted_level,
       CASE WHEN p_high < 0.005 THEN 'under 1%' ELSE concat(CAST(round(100 * p_high) AS INT), '%') END AS chance_of_high,
       CAST(new_cluster_rank AS INT) AS new_cluster_rank, round(100 * wolbachia_share) AS wolbachia_homes_pct,
       why, basis_date, score_from, score_until
FROM {s}.gold_forecast_latest
WHERE upper(planning_area) = upper('Tampines')"""),
    ("Which dengue clusters are active right now?", """
SELECT initcap(planning_area) AS area, locality, alert, recent AS cases_last_2_weeks, total AS cases_since_start,
       snapshot_date AS nea_list_date
FROM {s}.silver_clusters
WHERE snapshot_date = (SELECT max(snapshot_date) FROM {s}.silver_clusters)
ORDER BY total DESC"""),
    ("Which areas have a red alert right now?", """
SELECT initcap(planning_area) AS area, n_clusters, largest_cluster, recent_cases, snapshot_date AS nea_list_date
FROM {s}.gold_area_levels
WHERE snapshot_date = (SELECT max(snapshot_date) FROM {s}.gold_area_levels) AND level = 2
ORDER BY largest_cluster DESC"""),
    ("How many dengue cases were reported last week?", """
SELECT week_end, year, week, cases
FROM {s}.gold_national_weekly
ORDER BY week_end DESC
LIMIT 1"""),
    ("How do dengue cases this year compare with last year?", """
WITH latest AS (SELECT year, week FROM {s}.gold_national_weekly ORDER BY week_end DESC LIMIT 1)
SELECT w.year, sum(w.cases) AS cases_to_date, max(w.week) AS up_to_week
FROM {s}.gold_national_weekly w, latest l
WHERE w.year IN (l.year, l.year - 1) AND w.week <= l.week
GROUP BY w.year
ORDER BY w.year"""),
    ("How accurate is the forecast?", """
SELECT question, baseline_name, round(100 * model, 1) AS model_pct, round(100 * baseline, 1) AS baseline_pct,
       round(100 * diff_p05, 1) AS diff_low_pts, round(100 * diff_p95, 1) AS diff_high_pts, test, test_lists
FROM {s}.gold_model_test_headline"""),
    ("How did our locked forecasts do?", """
SELECT basis_date, min(score_from) AS score_from, min(score_until) AS score_until, max(status) AS status,
       max(outcome_date) AS outcome_list,
       count_if(model_right) AS areas_model_right, count_if(same_as_now_right) AS areas_same_as_now_right,
       count_if(predicted_level = 'High' AND outcome_level = 'High') AS high_caught,
       count_if(outcome_level = 'High') AS high_outcomes,
       count_if(new_cluster_rank <= 5 AND new_cluster_appeared) AS new_clusters_in_top5,
       count_if(new_cluster_appeared) AS new_clusters
FROM {s}.gold_track_record
GROUP BY basis_date
ORDER BY basis_date DESC"""),
    ("Where are the inspection teams going this week?", """
SELECT team, anchor, area, blocks, homes, blocks_near_cluster, homes_in_wolbachia, cluster_list_date, forecast_basis_date
FROM {s}.gold_team_plan
ORDER BY team"""),
    ("Which HDB blocks in Jurong West should be inspected first?", """
SELECT blk_no, street, homes, weight, reason, in_wolbachia, cluster_list_date
FROM {s}.gold_block_priority
WHERE upper(planning_area) = upper('Jurong West')
ORDER BY weight DESC
LIMIT 20"""),
    ("Which areas had dengue clusters most often this year?", """
SELECT initcap(planning_area) AS area, count(*) AS nea_lists, count_if(level > 0) AS lists_with_cluster,
       round(100 * avg(CASE WHEN level > 0 THEN 1 ELSE 0 END)) AS pct_of_lists_with_cluster,
       count_if(level = 2) AS lists_with_red_alert
FROM {s}.gold_area_levels
WHERE year(snapshot_date) = (SELECT year(max(snapshot_date)) FROM {s}.gold_area_levels)
GROUP BY planning_area
HAVING count_if(level > 0) > 0
ORDER BY pct_of_lists_with_cluster DESC, lists_with_red_alert DESC"""),
    ("Has it been wetter or hotter than usual recently?", """
SELECT month, rain_mm, round(rain_anom, 1) AS rain_vs_usual_mm, temp_mean, round(temp_anom, 2) AS temp_vs_usual_c,
       round(rh_anom, 1) AS humidity_vs_usual_pts
FROM {s}.gold_weather_monthly
ORDER BY month DESC
LIMIT 6"""),
    ("Where are the high Aedes mosquito areas?", """
SELECT description AS area_and_streets, to_date(substr(fmel_upd_d, 1, 8), 'yyyyMMdd') AS updated
FROM {s}.bronze_nea_high_aedes_areas_latest
ORDER BY description"""),
]

SAMPLE_QUESTIONS = [
    "Which areas are forecast to be High risk in the next two weeks?",
    "Where could new dengue clusters appear?",
    "Which dengue clusters are active right now?",
    "How many dengue cases were reported last week?",
    "How accurate is the forecast?",
    "Where are the inspection teams going this week?",
]

# Benchmarks: reworded questions with a known answer. key = a column whose values Genie's answer
# must contain exactly (as a set) for --ask to count it right.
BENCHMARKS = [
    ("Which towns will be red alert in the coming fortnight?", "area", """
SELECT area, round(100 * p_high) AS chance_of_high_pct FROM {s}.gold_forecast_latest WHERE predicted_level = 'High'"""),
    ("List the top 5 places a fresh cluster might pop up", "area", """
SELECT area FROM {s}.gold_forecast_latest WHERE new_cluster_watch ORDER BY new_cluster_rank"""),
    ("What is the forecast for Bishan?", "predicted_level", """
SELECT area, predicted_level FROM {s}.gold_forecast_latest WHERE planning_area = 'BISHAN'"""),
    ("Which areas currently have dengue clusters?", "area", """
SELECT DISTINCT initcap(planning_area) AS area FROM {s}.silver_clusters
WHERE snapshot_date = (SELECT max(snapshot_date) FROM {s}.silver_clusters)"""),
    ("What is the biggest active cluster and where is it?", "total", """
SELECT locality, initcap(planning_area) AS area, total FROM {s}.silver_clusters
WHERE snapshot_date = (SELECT max(snapshot_date) FROM {s}.silver_clusters) ORDER BY total DESC LIMIT 1"""),
    ("How many dengue cases did Singapore have in 2025?", "cases", """
SELECT sum(cases) AS cases FROM {s}.gold_national_weekly WHERE year = 2025"""),
    ("Which team reaches the most homes?", "team", """
SELECT team, homes FROM {s}.gold_team_plan ORDER BY homes DESC LIMIT 1"""),
    ("Is the model better than assuming nothing changes?", "baseline", """
SELECT question, model, baseline FROM {s}.gold_model_test_headline WHERE baseline_name = 'Same as now'"""),
    ("Which areas are expected to fall in risk?", "area", """
SELECT area FROM {s}.gold_forecast_latest WHERE direction = 'Falling'"""),
    ("How many areas are forecast Medium?", "n", """
SELECT count(*) AS n FROM {s}.gold_forecast_latest WHERE predicted_level = 'Medium'"""),
]


# ---------------------------------------------------------------------------------------------
def cli(*args, body=None):
    cmd = ["databricks", *args] + (["--json", json.dumps(body)] if body is not None else [])
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"{' '.join(args[:3])}: {r.stderr.strip() or r.stdout.strip()}")
    return json.loads(r.stdout) if r.stdout.strip() else {}


def sql(warehouse, statement):
    """Run a statement; return (columns, rows)."""
    r = cli("api", "post", "/api/2.0/sql/statements",
            body={"warehouse_id": warehouse, "statement": statement, "wait_timeout": "50s"})
    while r["status"]["state"] in ("PENDING", "RUNNING"):
        time.sleep(2)
        r = cli("api", "get", f"/api/2.0/sql/statements/{r['statement_id']}")
    if r["status"]["state"] != "SUCCEEDED":
        raise RuntimeError(r["status"].get("error", {}).get("message", r["status"]["state"]))
    cols = [c["name"] for c in r["manifest"]["schema"]["columns"]]
    return cols, r.get("result", {}).get("data_array", []) or []


def lines(text):
    """Genie stores text as a list of lines, each keeping its newline."""
    out = text.strip("\n").splitlines(keepends=True)
    return out or [""]


def ident(section, i):
    return f"{section:02x}{i:030x}"


def build(schema):
    tables = []
    for name in sorted(TABLES):
        desc, cols = TABLES[name]
        tables.append({
            "identifier": f"{schema}.{name}",
            "description": [desc],
            "column_configs": [
                {"column_name": c, "description": [d], **({"synonyms": syn} if syn else {})}
                for c, (d, syn) in sorted(cols.items())
            ],
        })
    joins = [{
        "id": ident(4, i),
        "left": {"identifier": f"{schema}.{l}"},
        "right": {"identifier": f"{schema}.{r}"},
        "sql": [f"`{l}`.`{col}` = `{r}`.`{col}`", f"--rt=FROM_RELATIONSHIP_TYPE_{rel}--"],
    } for i, (l, r, col, rel) in enumerate(JOINS)]
    return {
        "version": 2,
        "config": {"sample_questions": [{"id": ident(1, i), "question": [q]} for i, q in enumerate(SAMPLE_QUESTIONS)]},
        "data_sources": {"tables": tables},
        "instructions": {
            "text_instructions": [{"id": ident(2, 0), "content": lines(INSTRUCTIONS)}],
            "example_question_sqls": [{"id": ident(3, i), "question": [q], "sql": lines(s.format(s=schema))}
                                      for i, (q, s) in enumerate(EXAMPLES)],
            "join_specs": joins,
        },
        "benchmarks": {"questions": [
            {"id": ident(5, i), "question": [q], "answer": [{"format": "SQL", "content": lines(s.format(s=schema))}]}
            for i, (q, _, s) in enumerate(BENCHMARKS)]},
    }


def check(warehouse, schema):
    """Every table exists and every example / benchmark query runs and returns rows (or says why not)."""
    bad = 0
    for name, (_, cols) in TABLES.items():
        have, _ = sql(warehouse, f"SELECT * FROM {schema}.{name} LIMIT 0")
        missing = sorted(set(cols) - set(have))
        if missing:
            bad += 1
            print(f"  MISSING COLUMNS {name}: {missing}")
    for q, s in [(q, s) for q, s in EXAMPLES] + [(q, s) for q, _, s in BENCHMARKS]:
        try:
            _, rows = sql(warehouse, s.format(s=schema))
            print(f"  ok   {len(rows):4d} rows  {q}")
        except RuntimeError as e:
            bad += 1
            print(f"  FAIL            {q}\n       {e}")
    return bad


def find_space(title):
    page, token = [], None
    while True:
        r = cli("api", "get", "/api/2.0/genie/spaces" + (f"?page_token={token}" if token else ""))
        page += r.get("spaces", [])
        token = r.get("next_page_token")
        if not token:
            return [s for s in page if s.get("title") == title]


def ask(space_id, warehouse, schema):
    """Ask Genie each benchmark question; compare the key column with the expected answer."""
    right = 0
    for q, key, s in BENCHMARKS:
        cols, rows = sql(warehouse, s.format(s=schema))
        want = {str(r[cols.index(key)]) for r in rows}
        m = cli("api", "post", f"/api/2.0/genie/spaces/{space_id}/start-conversation", body={"content": q})
        conv, msg = m["conversation_id"], m["message_id"]
        for _ in range(90):
            time.sleep(3)
            m = cli("api", "get", f"/api/2.0/genie/spaces/{space_id}/conversations/{conv}/messages/{msg}")
            if m["status"] in ("COMPLETED", "FAILED", "CANCELLED", "QUERY_RESULT_EXPIRED"):
                break
        got_sets, text, gsql = [], "", ""
        for a in m.get("attachments") or []:
            if a.get("text"):
                text = a["text"].get("content", "")
            if a.get("query"):
                gsql = a["query"].get("query", "")
                r = cli("api", "get", f"/api/2.0/genie/spaces/{space_id}/conversations/{conv}/messages/{msg}"
                                      f"/attachments/{a['attachment_id']}/query-result")
                sr = r.get("statement_response", {})
                gcols = [c["name"] for c in sr.get("manifest", {}).get("schema", {}).get("columns", [])]
                grows = sr.get("result", {}).get("data_array", []) or []
                got_sets = [{str(r[j]) for r in grows} for j in range(len(gcols))]
        # Genie may round, or show a 0-1 score as a percentage: compare numbers to 1 decimal, as-is or x100
        def norm(v, scale=1):
            try:
                return round(float(v) * scale, 1)
            except (TypeError, ValueError):
                return v
        ok = any({norm(v) for v in g} in ({norm(v) for v in want}, {norm(v, 100) for v in want}) for g in got_sets)
        right += ok
        print(f"  {'RIGHT' if ok else 'WRONG'}  {q}\n         expected {key} = {sorted(want)}")
        if not ok:
            print(f"         status {m['status']}; genie sql: {' '.join(gsql.split())[:400]}\n         text: {text[:300]}")
    print(f"  {right}/{len(BENCHMARKS)} benchmark questions answered right")
    return right


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--warehouse", default="18e372f3f7575204")
    ap.add_argument("--schema", default="workspace.dengue", help="catalog.schema holding the gold tables")
    ap.add_argument("--check", action="store_true", help="only run the queries; do not create or update")
    ap.add_argument("--ask", action="store_true", help="after publishing, ask Genie the benchmark questions")
    a = ap.parse_args()

    print("Checking tables and queries:")
    if check(a.warehouse, a.schema):
        sys.exit("Fix the failures above before publishing.")
    if a.check:
        return

    body = {"warehouse_id": a.warehouse, "title": TITLE, "description": DESCRIPTION,
            "serialized_space": json.dumps(build(a.schema))}
    found = find_space(TITLE)
    if len(found) > 1:
        sys.exit(f"{len(found)} spaces are titled {TITLE!r}; delete the extras first: {[s['space_id'] for s in found]}")
    if found:
        space_id = found[0]["space_id"]
        cli("api", "patch", f"/api/2.0/genie/spaces/{space_id}", body=body)
        print(f"Updated Genie space {space_id}")
    else:
        space_id = cli("api", "post", "/api/2.0/genie/spaces", body=body)["space_id"]
        print(f"Created Genie space {space_id}")
    host = cli("auth", "describe", "-o", "json").get("details", {}).get("host", "").rstrip("/")
    print(f"Open it: {host}/genie/rooms/{space_id}")
    if a.ask:
        print("Asking Genie the benchmark questions:")
        ask(space_id, a.warehouse, a.schema)


if __name__ == "__main__":
    main()
