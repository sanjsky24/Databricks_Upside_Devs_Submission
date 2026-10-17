-- Gold: the area-by-fortnight table the forecasting model learns from and predicts with.
--
-- One row = one planning area on one cluster-list date. It holds what was known that day
-- (the features) and, where the future is known, the area's risk level about two weeks
-- later (the label). A port of the prototype's d10.py panel.
--
-- Risk level (NEA alert terms): 2 = High (an active cluster with 10+ cases, "red"),
-- 1 = Medium (any active cluster, "yellow"), 0 = Low (none). A cluster belongs to the
-- planning area holding most of its cases.
--
-- Label: the level at the first list date at least 14 days later, kept only when that
-- list is 14-35 days away (lists are irregular: twice a week in 2015-20, a few a year in
-- some archive years). Rows without a usable later list have label NULL; the newest
-- rows are the ones the weekly forecast scores.
--
-- Lists since our own captures began (daily) are thinned to every 4th day counting back
-- from the newest list, so the history features see roughly the twice-weekly rhythm the
-- model was trained on (sgcharts, 2015-20).


CREATE OR REFRESH MATERIALIZED VIEW gold_area_neighbours
COMMENT 'Pairs of planning areas whose boundaries come within 50 m of each other (SVY21).'
AS
SELECT a.planning_area, b.planning_area AS neighbour
FROM silver_planning_areas a
JOIN silver_planning_areas b
  ON a.planning_area <> b.planning_area
 AND st_intersects(st_buffer(st_transform(st_geomfromgeojson(a.geometry_geojson), 3414), 50),
                   st_transform(st_geomfromgeojson(b.geometry_geojson), 3414));


CREATE OR REFRESH MATERIALIZED VIEW gold_area_fortnight (
  CONSTRAINT label_gap_14_35_days EXPECT (y IS NULL OR gap_days BETWEEN 14 AND 35),
  CONSTRAINT valid_level EXPECT (cls IN (0, 1, 2)),
  CONSTRAINT shares_between_0_1 EXPECT (wol_dyn BETWEEN 0 AND 1 AND wol_static BETWEEN 0 AND 1)
)
COMMENT 'One row per planning area per cluster-list date: features known that day and the NEA risk level (0 Low / 1 Medium / 2 High) about two weeks later (y). y is NULL where no list falls 14-35 days later. New cluster = cls 0 today and y > 0.'
AS
WITH cl AS (
  SELECT snapshot_date, source, planning_area, recent, coalesce(total, recent) AS total
  FROM silver_clusters
  WHERE is_active AND planning_area IS NOT NULL
),
last_archive AS (SELECT max(snapshot_date) AS d FROM cl WHERE source IN ('sgcharts', 'wayback')),
newest AS (SELECT max(snapshot_date) AS d FROM cl),
snaps AS (
  SELECT snapshot_date, max(source) AS list_source
  FROM cl
  WHERE snapshot_date <= (SELECT d FROM last_archive)
     OR datediff((SELECT d FROM newest), snapshot_date) % 4 = 0
  GROUP BY snapshot_date
),
agg AS (
  SELECT snapshot_date, planning_area,
         sum(recent) AS recent, sum(total) AS total, count(*) AS n, max(total) AS maxtot
  FROM cl GROUP BY snapshot_date, planning_area
),
grid AS (
  SELECT
    s.snapshot_date, s.list_source, a.planning_area,
    coalesce(g.recent, 0) AS recent,
    coalesce(g.total, 0) AS total,
    coalesce(g.n, 0) AS n_clusters,
    coalesce(g.maxtot, 0) AS max_cluster_total,
    CASE WHEN g.maxtot >= 10 THEN 2 WHEN g.n > 0 THEN 1 ELSE 0 END AS cls
  FROM snaps s
  CROSS JOIN silver_planning_areas a
  LEFT JOIN agg g ON g.snapshot_date = s.snapshot_date AND g.planning_area = a.planning_area
),
homes AS (
  SELECT planning_area,
         sum(total_dwelling_units) AS du,
         sum(CASE WHEN wolbachia_start_date IS NOT NULL THEN total_dwelling_units ELSE 0 END) AS du_wol
  FROM silver_hdb_blocks GROUP BY planning_area
),
protected AS (
  -- homes in a zone whose releases began at least 60 days before the list date
  SELECT s.snapshot_date, b.planning_area, sum(b.total_dwelling_units) AS du_protected
  FROM snaps s
  JOIN silver_hdb_blocks b ON b.wolbachia_start_date <= date_sub(s.snapshot_date, 60)
  GROUP BY s.snapshot_date, b.planning_area
),
hist AS (
  -- past recent cases in this area, each list weighted by exp(-days ago / 180)
  SELECT a.snapshot_date, a.planning_area,
         sum(b.recent * exp(-datediff(a.snapshot_date, b.snapshot_date) / 180.0)) AS h
  FROM grid a
  JOIN grid b ON b.planning_area = a.planning_area AND b.snapshot_date < a.snapshot_date
  GROUP BY a.snapshot_date, a.planning_area
),
nbr AS (
  SELECT g.snapshot_date, n.planning_area, sum(g.recent) AS r
  FROM gold_area_neighbours n
  JOIN grid g ON g.planning_area = n.neighbour
  GROUP BY g.snapshot_date, n.planning_area
),
nat AS (SELECT snapshot_date, sum(recent) AS r FROM grid GROUP BY snapshot_date),
nxt AS (
  SELECT s.snapshot_date, min(t.snapshot_date) AS next_date
  FROM snaps s JOIN snaps t ON t.snapshot_date >= date_add(s.snapshot_date, 14)
  GROUP BY s.snapshot_date
)
SELECT
  g.snapshot_date,
  g.planning_area,
  g.list_source,
  -- today
  g.cls,
  g.recent,
  g.total,
  g.n_clusters,
  g.max_cluster_total,
  -- features (names as in the prototype)
  log1p(g.recent) AS lrecent,
  log1p(g.total) AS ltotal,
  g.n_clusters AS n,
  log1p(coalesce(h.h, 0)) AS lhist,
  coalesce(avg(g.cls) OVER (PARTITION BY g.planning_area ORDER BY g.snapshot_date
                            ROWS BETWEEN 3 PRECEDING AND 1 PRECEDING), 0) AS cls_last3,
  log1p(coalesce(nb.r, 0)) AS nbr,
  log1p(nat.r) AS nat,
  log1p(coalesce(hm.du, 0)) AS du,
  coalesce(hm.du_wol / nullif(hm.du, 0), 0) AS wol_static,
  coalesce(p.du_protected / nullif(hm.du, 0), 0) AS wol_dyn,
  sin(2 * pi() * month(g.snapshot_date) / 12) AS ms,
  cos(2 * pi() * month(g.snapshot_date) / 12) AS mc,
  -- label: about two weeks later
  x.next_date,
  datediff(x.next_date, g.snapshot_date) AS gap_days,
  CASE WHEN datediff(x.next_date, g.snapshot_date) BETWEEN 14 AND 35 THEN y.cls END AS y,
  CASE WHEN datediff(x.next_date, g.snapshot_date) BETWEEN 14 AND 35 THEN y.recent END AS y_recent
FROM grid g
LEFT JOIN hist h ON h.snapshot_date = g.snapshot_date AND h.planning_area = g.planning_area
LEFT JOIN nbr nb ON nb.snapshot_date = g.snapshot_date AND nb.planning_area = g.planning_area
JOIN nat ON nat.snapshot_date = g.snapshot_date
LEFT JOIN homes hm ON hm.planning_area = g.planning_area
LEFT JOIN protected p ON p.snapshot_date = g.snapshot_date AND p.planning_area = g.planning_area
LEFT JOIN nxt x ON x.snapshot_date = g.snapshot_date
LEFT JOIN grid y ON y.planning_area = g.planning_area AND y.snapshot_date = x.next_date;
