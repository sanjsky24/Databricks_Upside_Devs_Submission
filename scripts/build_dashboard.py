"""Write the DengueRadar AI/BI dashboard definition (dashboards/dengueradar.lvdash.json).

    python scripts/build_dashboard.py

The .lvdash.json format repeats a lot of boilerplate (every table column carries ~20 display
settings), so the dashboard is described here and the JSON is generated. Widget formats follow
Databricks' published examples (github.com/databricks/bundle-examples): tables spec v1, counters
v2, charts v3, on a 6-column grid. Datasets read workspace.dengue; the bundle deploys the file.

Colours: NEA's alert colours for risk levels (High red, Medium amber, Low grey), always shown
next to the level name, never colour alone.
"""

import hashlib
import json
import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO, "dashboards", "dengueradar.lvdash.json")
T = "workspace.dengue"
LEVEL_COLORS = [("High", "#d03b3b"), ("Medium", "#fab219"), ("Low", "#a3a29a")]


def hid(*parts):
    """Stable 8-hex id, so regenerating doesn't churn the file."""
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:8]


# ---------------------------------------------------------------- datasets

DATASETS = {
    "forecast": ("Newest locked forecast", f"""
SELECT area, region, lon, lat, level_today_name AS today, predicted_level AS forecast, direction,
       round(p_high, 2) AS p_high, CAST(new_cluster_rank AS INT) AS new_cluster_rank,
       round(new_cluster_score, 2) AS new_cluster_score, new_cluster_watch, why,
       predicted_level_num, level_today, recent_cases, n_clusters,
       basis_date, CAST(locked_at_utc AS DATE) AS locked_on, score_from, score_until
FROM {T}.gold_forecast_latest
ORDER BY predicted_level_num DESC, p_high DESC, new_cluster_rank"""),
    "headline": ("This week in numbers", f"""
SELECT
  count_if(predicted_level = 'High') AS high_areas,
  count_if(predicted_level = 'Medium') AS medium_areas,
  count_if(level_today > 0) AS areas_with_clusters_today,
  max(basis_date) AS basis_date,
  (SELECT plan_homes FROM {T}.gold_team_plan_summary) AS plan_homes,
  (SELECT round(homes_ratio, 1) FROM {T}.gold_team_plan_summary) AS plan_ratio
FROM {T}.gold_forecast_latest"""),
    "basis": ("Forecast dates", f"""
SELECT max(basis_date) AS basis_date, CAST(max(locked_at_utc) AS DATE) AS locked_on, max(score_from) AS score_from
FROM {T}.gold_forecast_latest"""),
    "watch": ("New-cluster watch", f"""
SELECT CAST(new_cluster_rank AS INT) AS rank, area, round(new_cluster_score, 2) AS score, why
FROM {T}.gold_forecast_latest
WHERE new_cluster_watch
ORDER BY new_cluster_rank"""),
    "plan": ("Team plan", f"""
SELECT team, anchor, area, blocks, homes, blocks_near_cluster, homes_in_wolbachia, cluster_list_date
FROM {T}.gold_team_plan ORDER BY team"""),
    "plan_summary": ("Team plan vs simple plan", f"""
SELECT 'Team planner' AS plan, plan_homes AS priority_homes FROM {T}.gold_team_plan_summary
UNION ALL
SELECT 'Top blocks, spread out', simple_homes FROM {T}.gold_team_plan_summary"""),
    "track": ("Locked forecasts", f"""
SELECT basis_date, CAST(min(locked_at_utc) AS DATE) AS locked_on, min(score_from) AS score_from,
       min(score_until) AS score_until, max(status) AS status, max(outcome_date) AS outcome_list,
       count(*) AS areas,
       count_if(model_right) AS model_right, count_if(same_as_now_right) AS same_as_now_right,
       count_if(predicted_level = 'High' AND outcome_level = 'High') AS high_caught,
       count_if(outcome_level = 'High') AS high_outcomes,
       count_if(new_cluster_rank <= 5 AND new_cluster_appeared) AS new_clusters_in_top5,
       count_if(new_cluster_appeared) AS new_clusters,
       left(max(git_commit), 7) AS code
FROM {T}.gold_track_record
GROUP BY basis_date ORDER BY basis_date DESC"""),
    "test_headline": ("Test 2020-26: model vs simple rules", f"""
SELECT question, baseline_name AS compared_with, round(100 * model, 1) AS model_pct,
       round(100 * baseline, 1) AS baseline_pct,
       CASE WHEN diff_p05 IS NULL THEN '' ELSE concat(format_number(100 * diff_p05, 1), ' to +', format_number(100 * diff_p95, 1), ' pts') END AS difference_90pct,
       test, test_lists
FROM {T}.gold_model_test_headline"""),
    "test_years": ("Test by year", f"""
SELECT CAST(year AS STRING) AS year, lists, high_outcomes, round(high_f1, 2) AS model_high_f1,
       round(same_as_now_high_f1, 2) AS same_as_now_high_f1, new_clusters,
       round(100 * caught_at_5, 0) AS model_caught_pct, round(100 * past_clusters_caught_at_5, 0) AS past_clusters_caught_pct
FROM {T}.gold_model_test_by_year ORDER BY year"""),
    "weekly": ("Weekly national dengue cases", f"""
SELECT week_end, cases FROM {T}.gold_national_weekly WHERE week_end >= '2019-01-01' ORDER BY week_end"""),
}


def dataset(key):
    name, sql = DATASETS[key]
    return {"name": hid("ds", key), "displayName": name,
            "queryLines": [line + "\n" for line in sql.strip().split("\n")]}


# ---------------------------------------------------------------- widgets

def query(key, fields, disaggregated=True):
    return [{"name": "main_query", "query": {
        "datasetName": hid("ds", key), "disaggregated": disaggregated,
        "fields": [{"name": f, "expression": f"`{f}`"} for f in fields]}}]


def frame(title, description=None):
    f = {"showTitle": True, "title": title}
    if description:
        f.update(showDescription=True, description=description)
    return f


def text(name, lines, pos):
    return {"widget": {"name": hid("w", name), "multilineTextboxSpec": {"lines": lines}}, "position": pos}


def counter(name, key, field, title, description, pos):
    return {"widget": {"name": hid("w", name), "queries": query(key, [field], disaggregated=True),
                       "spec": {"version": 2, "widgetType": "counter",
                                "encodings": {"value": {"fieldName": field, "displayName": title}},
                                "frame": frame(title, description)}},
            "position": pos}


def column(field, title, kind, order, conditions=None, width=None):
    c = {
        "fieldName": field, "title": title, "displayName": title, "visible": True, "order": 100000 + order,
        "type": {"str": "string", "int": "integer", "float": "float", "date": "date", "bool": "boolean"}[kind],
        "displayAs": {"str": "string", "int": "number", "float": "number", "date": "datetime", "bool": "boolean"}[kind],
        "alignContent": "left" if kind in ("str", "date", "bool") else "right",
        "booleanValues": ["no", "yes"], "allowSearch": kind == "str", "allowHTML": False,
        "highlightLinks": False, "useMonospaceFont": False, "preserveWhitespace": False,
        "linkOpenInNewTab": True, "imageUrlTemplate": "{{ @ }}", "imageTitleTemplate": "{{ @ }}",
        "imageWidth": "", "imageHeight": "", "linkUrlTemplate": "{{ @ }}", "linkTextTemplate": "{{ @ }}",
        "linkTitleTemplate": "{{ @ }}",
    }
    if kind == "int":
        c["numberFormat"] = "0"
    if kind == "float":
        c["numberFormat"] = "0.00"
    if kind == "date":
        c["dateTimeFormat"] = "D MMM YYYY"
    if conditions:
        c.update(colorMode="text", conditions=[{"condition": {"operator": "=", "value": v}, "color": col}
                                               for v, col in conditions])
    if width:
        c["defaultColumnWidth"] = width
    return c


def table(name, key, cols, title, description, pos):
    return {"widget": {"name": hid("w", name), "queries": query(key, [c[0] for c in cols]),
                       "spec": {"version": 1, "widgetType": "table",
                                "encodings": {"columns": [column(*c[:3], i, *c[3:]) for i, c in enumerate(cols)]},
                                "invisibleColumns": [], "allowHTMLByDefault": False, "itemsPerPage": 25,
                                "paginationSize": "default", "condensed": True, "withRowNumber": False,
                                "frame": frame(title, description)}},
            "position": pos}


def level_scale():
    return {"type": "categorical", "mappings": [{"value": v, "color": c} for v, c in LEVEL_COLORS]}


# ---------------------------------------------------------------- pages

LEVEL_COND = LEVEL_COLORS[:2]  # colour the words High / Medium in tables; Low stays plain ink

monday = [
    text("title", [
        "# DengueRadar · this week",
        "Two-week dengue risk for Singapore's 55 planning areas, from the newest **locked** forecast "
        "(it can't be edited once written). Levels use NEA's alert rule: **High** = a cluster with 10+ cases, "
        "**Medium** = any active cluster, **Low** = none.",
    ], {"x": 0, "y": 0, "width": 6, "height": 2}),
    counter("c_high", "headline", "high_areas", "Areas forecast High", "in two weeks", {"x": 0, "y": 2, "width": 1, "height": 3}),
    counter("c_med", "headline", "medium_areas", "Areas forecast Medium", "in two weeks", {"x": 1, "y": 2, "width": 1, "height": 3}),
    counter("c_today", "headline", "areas_with_clusters_today", "Areas with clusters today", "on the forecast's NEA list", {"x": 2, "y": 2, "width": 1, "height": 3}),
    table("t_basis", "basis", [("basis_date", "NEA list used", "date"), ("locked_on", "Locked", "date"),
                               ("score_from", "Scored from", "date")],
          "Forecast", None, {"x": 3, "y": 2, "width": 1, "height": 3}),
    counter("c_homes", "headline", "plan_homes", "Priority homes, 6 teams", "covered by this week's team plan", {"x": 4, "y": 2, "width": 1, "height": 3}),
    counter("c_ratio", "headline", "plan_ratio", "× the simple plan", "homes reached vs top blocks spread out", {"x": 5, "y": 2, "width": 1, "height": 3}),
    {"widget": {"name": hid("w", "map"), "queries": query("forecast", ["lon", "lat", "forecast", "area", "p_high"]),
                "spec": {"version": 3, "widgetType": "scatter",
                         "encodings": {
                             "x": {"fieldName": "lon", "scale": {"type": "quantitative"}, "displayName": "Longitude",
                                   "axis": {"title": "west ← → east"}},
                             "y": {"fieldName": "lat", "scale": {"type": "quantitative"}, "displayName": "Latitude",
                                   "axis": {"title": "south ← → north"}},
                             "color": {"fieldName": "forecast", "scale": level_scale(), "displayName": "Forecast"},
                             "extra": [{"fieldName": "area", "displayName": "Area"},
                                       {"fieldName": "p_high", "displayName": "P(High)"}]},
                         "frame": frame("Risk map (one dot per planning area)", "Hover a dot for the area and P(High)")}},
     "position": {"x": 0, "y": 5, "width": 3, "height": 8}},
    table("t_watch", "watch", [("rank", "#", "int"), ("area", "Area", "str"), ("score", "Chance", "float"),
                               ("why", "What the model sees", "str", None, 420)],
          "New-cluster watch", "Areas clear today, ranked by the chance a new cluster starts within two weeks",
          {"x": 3, "y": 5, "width": 3, "height": 8}),
    table("t_risk", "forecast", [
        ("area", "Area", "str"), ("today", "Today", "str", LEVEL_COND), ("forecast", "In 2 weeks", "str", LEVEL_COND),
        ("direction", "Change", "str"), ("p_high", "P(High)", "float"), ("new_cluster_rank", "New-cluster rank", "int"),
        ("why", "Why? (what the model sees)", "str", None, 520)],
        "Risk by area", "Every planning area, highest risk first. Reasons describe the model's inputs for the area, not its weights.",
        {"x": 0, "y": 13, "width": 6, "height": 10}),
    text("plan_title", [
        "## Team plan",
        "One inspection site per team, no overlaps: an anchor HDB block plus every priority block within 300 m. "
        "Priority = near a cluster on the newest NEA list (×3), near a high-mosquito area (×2), or in an area the forecast flags (×1); "
        "halved inside Wolbachia zones.",
    ], {"x": 0, "y": 23, "width": 6, "height": 2}),
    table("t_plan", "plan", [("team", "Team", "int"), ("anchor", "Start at", "str"), ("area", "Area", "str"),
                             ("blocks", "Blocks", "int"), ("homes", "Homes", "int"),
                             ("blocks_near_cluster", "Blocks near a cluster", "int"),
                             ("homes_in_wolbachia", "Homes in Wolbachia zones", "int")],
          "This week's sites", None, {"x": 0, "y": 25, "width": 4, "height": 7}),
    {"widget": {"name": hid("w", "plan_vs"), "queries": query("plan_summary", ["plan", "priority_homes"]),
                "spec": {"version": 3, "widgetType": "bar",
                         "encodings": {"x": {"fieldName": "plan", "scale": {"type": "categorical"}, "displayName": "Plan"},
                                       "y": {"fieldName": "priority_homes", "scale": {"type": "quantitative"},
                                             "displayName": "Priority homes reached"},
                                       "label": {"show": True}},
                         "frame": frame("Priority homes reached by 6 teams")}},
     "position": {"x": 4, "y": 25, "width": 2, "height": 7}},
]

proof = [
    text("proof_title", [
        "# Track record & tests",
        "Every forecast is locked in an append-only table before the outcome is known, then scored against "
        "NEA's first list 14–35 days later. The test below predicts every year from 2020 using only the years before it.",
    ], {"x": 0, "y": 0, "width": 6, "height": 2}),
    table("t_track", "track", [
        ("basis_date", "NEA list", "date"), ("locked_on", "Locked", "date"), ("score_from", "Scored from", "date"),
        ("status", "Status", "str"), ("outcome_list", "Outcome list", "date"), ("model_right", "Areas right: model", "int"),
        ("same_as_now_right", "…: same as now", "int"), ("high_caught", "High caught", "int"),
        ("high_outcomes", "High outcomes", "int"), ("new_clusters_in_top5", "New clusters in top 5", "int"),
        ("new_clusters", "New clusters", "int"), ("code", "Code", "str")],
        "Locked forecasts", "status = waiting until NEA publishes a list in the scoring window",
        {"x": 0, "y": 2, "width": 6, "height": 5}),
    table("t_test", "test_headline", [
        ("question", "Question", "str"), ("compared_with", "Compared with", "str"), ("model_pct", "Model %", "float"),
        ("baseline_pct", "Rule %", "float"), ("difference_90pct", "Difference (90% range)", "str"),
        ("test_lists", "Test lists", "int")],
        "Test 2020–26: model vs simple rules",
        "High-risk row is F1 ×100 (balance of catching High areas and avoiding false alarms). Range = model minus rule, resampling list dates.",
        {"x": 0, "y": 7, "width": 6, "height": 4}),
    table("t_years", "test_years", [
        ("year", "Year", "str"), ("lists", "Lists", "int"), ("high_outcomes", "High outcomes", "int"),
        ("model_high_f1", "High F1: model", "float"), ("same_as_now_high_f1", "…: same as now", "float"),
        ("new_clusters", "New clusters", "int"), ("model_caught_pct", "Caught in top 5: model %", "float"),
        ("past_clusters_caught_pct", "…: past clusters %", "float")],
        "Test by year", "2021 and 2023 have no NEA list with a known outcome two weeks later",
        {"x": 0, "y": 11, "width": 6, "height": 6}),
    {"widget": {"name": hid("w", "weekly"), "queries": query("weekly", ["week_end", "cases"]),
                "spec": {"version": 3, "widgetType": "line",
                         "encodings": {"x": {"fieldName": "week_end", "scale": {"type": "temporal"}, "displayName": "Week ending"},
                                       "y": {"fieldName": "cases", "scale": {"type": "quantitative"}, "displayName": "Cases"}},
                         "frame": frame("Weekly dengue cases, Singapore", "MOH and CDA weekly bulletins, no missing weeks")}},
     "position": {"x": 0, "y": 17, "width": 6, "height": 6}},
]

dashboard = {
    "datasets": [dataset(k) for k in DATASETS],
    "pages": [
        {"name": hid("page", "monday"), "displayName": "This week", "layout": monday, "pageType": "PAGE_TYPE_CANVAS"},
        {"name": hid("page", "proof"), "displayName": "Track record & tests", "layout": proof, "pageType": "PAGE_TYPE_CANVAS"},
    ],
    "uiSettings": {"theme": {"widgetHeaderAlignment": "ALIGNMENT_UNSPECIFIED"}},
}

os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, "w") as f:
    json.dump(dashboard, f, indent=2, ensure_ascii=False)
    f.write("\n")
print(f"{sum(len(p['layout']) for p in dashboard['pages'])} widgets, {len(dashboard['datasets'])} datasets -> {OUT}")
