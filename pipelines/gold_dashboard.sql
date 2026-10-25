-- Gold tables behind the DengueRadar dashboard (the "Monday screen").
--
--   gold_area_levels        every area's NEA risk level on every list date (no thinning)
--   gold_forecast_latest    the newest locked forecast, one row per area, with map position and
--                           plain-language "Why?" reasons
--   gold_track_record       every locked forecast next to what NEA's list showed 14-35 days later
--   gold_block_priority     HDB blocks weighted for the team planner (notebook 04 picks the sites)
--   gold_national_weekly    weekly national dengue cases 2012 -> latest bulletin, no gaps
--
-- The "Why?" reasons describe what the model was given for that area (clusters today, cases
-- next door, past clusters, Wolbachia cover). They are not the model's weights.


CREATE OR REFRESH MATERIALIZED VIEW gold_area_levels (
  CONSTRAINT valid_level EXPECT (level IN (0, 1, 2))
)
COMMENT 'NEA risk level of every planning area on every cluster list date: 2 High (an active cluster with 10+ cases), 1 Medium (any active cluster), 0 Low. Unlike gold_area_fortnight, live lists are not thinned, so any list can score a forecast.'
AS
WITH lists AS (
  SELECT snapshot_date, max(source) AS source FROM silver_clusters GROUP BY snapshot_date
),
agg AS (
  SELECT snapshot_date, planning_area, count(*) AS n, sum(coalesce(recent, 0)) AS recent,
         max(coalesce(total, recent)) AS largest
  FROM silver_clusters
  WHERE is_active AND planning_area IS NOT NULL
  GROUP BY snapshot_date, planning_area
)
SELECT
  l.snapshot_date,
  l.source,
  a.planning_area,
  coalesce(g.n, 0) AS n_clusters,
  coalesce(g.recent, 0) AS recent_cases,
  coalesce(g.largest, 0) AS largest_cluster,
  CASE WHEN g.largest >= 10 THEN 2 WHEN g.n > 0 THEN 1 ELSE 0 END AS level
FROM lists l
CROSS JOIN silver_planning_areas a
LEFT JOIN agg g ON g.snapshot_date = l.snapshot_date AND g.planning_area = a.planning_area;


CREATE OR REFRESH MATERIALIZED VIEW gold_forecast_latest
COMMENT 'The newest locked forecast: one row per planning area with the predicted level, P(High), the new-cluster rank, the map position (area centroid) and up to three plain-language reasons describing what the model was given for that area.'
AS
WITH latest AS (
  SELECT forecast_id FROM gold_sealed_forecasts ORDER BY basis_date DESC, locked_at_utc DESC LIMIT 1
),
f AS (
  SELECT * FROM gold_sealed_forecasts WHERE forecast_id = (SELECT forecast_id FROM latest)
),
today AS (
  SELECT lv.* FROM gold_area_levels lv WHERE lv.snapshot_date = (SELECT max(basis_date) FROM f)
),
next_door AS (
  SELECT n.planning_area, sum(t.recent_cases) AS recent_next_door, count_if(t.level > 0) AS areas_next_door
  FROM gold_area_neighbours n JOIN today t ON t.planning_area = n.neighbour
  GROUP BY n.planning_area
),
hist AS (
  -- history and Wolbachia cover change slowly: take the model table's newest row on or before the basis date
  SELECT planning_area, lhist_avg, wol_dyn,
         percent_rank() OVER (ORDER BY lhist_avg) AS hist_rank
  FROM gold_area_fortnight
  WHERE snapshot_date = (SELECT max(snapshot_date) FROM gold_area_fortnight
                         WHERE snapshot_date <= (SELECT max(basis_date) FROM f))
),
base AS (
  SELECT
    f.forecast_id, f.basis_date, f.locked_at_utc, f.score_from, f.score_until,
    f.planning_area, initcap(f.planning_area) AS area,
    pa.region, pa.lon, pa.lat,
    f.level_today,
    CASE f.level_today WHEN 2 THEN 'High' WHEN 1 THEN 'Medium' ELSE 'Low' END AS level_today_name,
    f.predicted_level, f.same_as_now_level, f.p_low, f.p_medium, f.p_high,
    f.new_cluster_score, f.new_cluster_rank,
    coalesce(t.n_clusters, 0) AS n_clusters, coalesce(t.recent_cases, 0) AS recent_cases,
    coalesce(t.largest_cluster, 0) AS largest_cluster,
    coalesce(nd.recent_next_door, 0) AS recent_next_door, coalesce(nd.areas_next_door, 0) AS areas_next_door,
    h.hist_rank, coalesce(h.wol_dyn, 0) AS wolbachia_share
  FROM f
  LEFT JOIN silver_planning_areas pa ON pa.planning_area = f.planning_area
  LEFT JOIN today t ON t.planning_area = f.planning_area
  LEFT JOIN next_door nd ON nd.planning_area = f.planning_area
  LEFT JOIN hist h ON h.planning_area = f.planning_area
)
SELECT
  b.*,
  CASE b.predicted_level WHEN 'High' THEN 2 WHEN 'Medium' THEN 1 ELSE 0 END AS predicted_level_num,
  CASE
    WHEN b.predicted_level = b.level_today_name THEN 'Same'
    WHEN (CASE b.predicted_level WHEN 'High' THEN 2 WHEN 'Medium' THEN 1 ELSE 0 END) > b.level_today THEN 'Rising'
    ELSE 'Falling'
  END AS direction,
  coalesce(b.new_cluster_rank <= 5, false) AS new_cluster_watch,
  array_join(slice(filter(array(
    CASE WHEN b.level_today = 2 THEN concat('Red-alert cluster here today (', b.largest_cluster, ' cases)')
         WHEN b.level_today = 1 THEN concat(b.n_clusters, CASE WHEN b.n_clusters = 1 THEN ' active cluster' ELSE ' active clusters' END, ' here today') END,
    CASE WHEN b.recent_cases > 0 THEN concat(b.recent_cases, ' cases in the last 2 weeks') END,
    CASE WHEN b.recent_next_door > 0 THEN concat(b.recent_next_door, ' recent cases in neighbouring areas')
         WHEN b.level_today = 0 THEN 'No clusters here or next door today' END,
    CASE WHEN b.hist_rank >= 0.8 THEN 'One of the areas with the most past clusters'
         WHEN b.hist_rank <= 0.3 THEN 'Few clusters here in the past' END,
    CASE WHEN b.wolbachia_share >= 0.5 THEN concat('Wolbachia covers ', CAST(round(100 * b.wolbachia_share) AS INT), '% of homes')
         WHEN b.wolbachia_share = 0 THEN 'No Wolbachia releases here yet' END
  ), r -> r IS NOT NULL), 1, 3), ' · ') AS why
FROM base b;


CREATE OR REFRESH MATERIALIZED VIEW gold_track_record
COMMENT 'Every locked forecast next to the outcome: the area''s level on the first NEA list 14-35 days after the basis date (score_from..score_until). status = scored once that list exists, waiting before. model_right / same_as_now_right compare each with the outcome.'
AS
WITH windows AS (
  SELECT DISTINCT forecast_id, score_from, score_until FROM gold_sealed_forecasts
),
outcome_list AS (
  SELECT w.forecast_id, min(l.snapshot_date) AS outcome_date
  FROM windows w JOIN (SELECT DISTINCT snapshot_date FROM gold_area_levels) l
    ON l.snapshot_date BETWEEN w.score_from AND w.score_until
  GROUP BY w.forecast_id
)
SELECT
  f.forecast_id, f.basis_date, f.locked_at_utc, f.score_from, f.score_until, f.git_commit,
  f.planning_area, initcap(f.planning_area) AS area,
  f.level_today, f.predicted_level, f.same_as_now_level, f.p_high, f.new_cluster_rank,
  o.outcome_date,
  CASE WHEN o.outcome_date IS NULL THEN 'waiting' ELSE 'scored' END AS status,
  CASE lv.level WHEN 2 THEN 'High' WHEN 1 THEN 'Medium' WHEN 0 THEN 'Low' END AS outcome_level,
  lv.n_clusters AS outcome_clusters,
  CASE WHEN o.outcome_date IS NOT NULL THEN f.predicted_level = CASE lv.level WHEN 2 THEN 'High' WHEN 1 THEN 'Medium' ELSE 'Low' END END AS model_right,
  CASE WHEN o.outcome_date IS NOT NULL THEN f.same_as_now_level = CASE lv.level WHEN 2 THEN 'High' WHEN 1 THEN 'Medium' ELSE 'Low' END END AS same_as_now_right,
  CASE WHEN o.outcome_date IS NOT NULL AND f.level_today = 0 THEN lv.level > 0 END AS new_cluster_appeared
FROM gold_sealed_forecasts f
LEFT JOIN outcome_list o ON o.forecast_id = f.forecast_id
LEFT JOIN gold_area_levels lv ON lv.snapshot_date = o.outcome_date AND lv.planning_area = f.planning_area;


CREATE OR REFRESH MATERIALIZED VIEW gold_block_priority (
  CONSTRAINT positive_weight EXPECT (weight > 0)
)
COMMENT 'HDB blocks worth inspecting this week, for the team planner (prototype d8.py rules plus the forecast): weight = homes x 3 if within 300 m of a cluster on the newest NEA list, x 2 if within 150 m of a high-Aedes area, x 1 if in an area the newest locked forecast calls High/Medium or ranks top 5 for a new cluster; halved inside a Wolbachia release zone.'
AS
WITH newest AS (SELECT max(snapshot_date) AS d FROM silver_cluster_points),
cluster_pts AS (
  SELECT x_svy21 AS x, y_svy21 AS y
  FROM silver_cluster_points
  WHERE snapshot_date = (SELECT d FROM newest) AND x_svy21 IS NOT NULL AND coalesce(recent, 1) > 0
),
aedes AS (
  SELECT st_transform(st_buffer(st_geomfromgeojson(geometry_geojson), 0), 3414) AS g
  FROM bronze_nea_high_aedes_areas_latest
),
flagged AS (
  SELECT planning_area FROM gold_forecast_latest WHERE predicted_level IN ('High', 'Medium') OR new_cluster_watch
),
b AS (
  SELECT
    k.postal_code, k.blk_no, k.street, k.planning_area, k.total_dwelling_units AS homes,
    k.lon, k.lat, k.x_svy21 AS x, k.y_svy21 AS y,
    k.wolbachia_start_date IS NOT NULL AS in_wolbachia,
    EXISTS (SELECT 1 FROM cluster_pts c
            WHERE (c.x - k.x_svy21) * (c.x - k.x_svy21) + (c.y - k.y_svy21) * (c.y - k.y_svy21) <= 300 * 300)
      AS near_cluster,
    EXISTS (SELECT 1 FROM aedes a WHERE st_distance(st_point(k.x_svy21, k.y_svy21, 3414), a.g) <= 150) AS near_high_aedes,
    k.planning_area IN (SELECT planning_area FROM flagged) AS in_flagged_area
  FROM silver_hdb_blocks k
)
SELECT
  b.*,
  (SELECT d FROM newest) AS cluster_list_date,
  b.homes * CASE WHEN b.near_cluster THEN 3 WHEN b.near_high_aedes THEN 2 WHEN b.in_flagged_area THEN 1 ELSE 0 END
         * CASE WHEN b.in_wolbachia THEN 0.5 ELSE 1 END AS weight,
  CASE WHEN b.near_cluster THEN 'Within 300 m of a dengue cluster'
       WHEN b.near_high_aedes THEN 'Within 150 m of a high-mosquito area'
       ELSE 'In an area the forecast flags' END AS reason
FROM b
WHERE b.near_cluster OR b.near_high_aedes OR b.in_flagged_area;


CREATE OR REFRESH MATERIALIZED VIEW gold_national_weekly (
  CONSTRAINT one_row_per_week EXPECT (week_end IS NOT NULL)
)
COMMENT 'Weekly national dengue cases by epidemiological week (Sunday-Saturday), 2012 to the latest bulletin: MOH bulletin 2012-2022, CDA bulletin seed 2023 on (gap-free). week_end is the Saturday that ends the week.'
AS
WITH w AS (
  SELECT CAST(substr(epi_week, 1, 4) AS INT) AS year, CAST(substr(epi_week, 7, 2) AS INT) AS week,
         CAST(no_of_cases AS INT) AS cases, 'MOH bulletin' AS source
  FROM bronze_moh_weekly_infectious_disease_latest
  WHERE disease = 'Dengue Fever'
  UNION ALL
  SELECT CAST(year AS INT), CAST(ew AS INT), CAST(dengue AS INT), 'CDA bulletin'
  FROM bronze_cda_weekly_dengue_latest
  WHERE year >= 2023
),
d AS (
  -- epi week 1 starts on the Sunday on or before 1 Jan if that week has 4+ days of the new year
  SELECT w.*, make_date(year, 1, 1) AS jan1,
         date_sub(make_date(year, 1, 1), dayofweek(make_date(year, 1, 1)) - 1) AS sun
  FROM w
)
SELECT
  year, week, cases, source,
  date_add(CASE WHEN datediff(jan1, sun) <= 3 THEN sun ELSE date_add(sun, 7) END, 7 * (week - 1) + 6) AS week_end
FROM d;
