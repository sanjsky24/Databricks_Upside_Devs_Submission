-- Silver layer: one clean, mapped dengue cluster history, 2015 -> today.
--
-- Lakeflow Declarative Pipeline (SQL). Reads the bronze tables in the pipeline's own
-- catalog/schema and publishes:
--
--   silver_planning_areas    55 URA Master Plan 2019 planning areas
--   silver_wolbachia_zones   NEA's 31 release zones, each with its start date
--   silver_hdb_blocks        residential HDB blocks with coordinates, homes and Wolbachia start
--   silver_street_locations  every street named in the archived cluster lists, placed on the map
--   silver_cluster_points    one row per address/street (or live polygon) in every cluster list
--   silver_clusters          one row per cluster per list date, assigned to a planning area
--   silver_source_agreement  case totals from each archive on dates they both cover
--
-- History = sgcharts (Jul 2015 - Nov 2020) -> Internet Archive captures of NEA's page
-- (after Nov 2020) -> NEA's live data.gov.sg layer (captured daily since 15 Oct 2026).
-- Source of all cluster lists: NEA.
--
-- Geometry note: a few URA and NEA polygons are slightly invalid (self-touching rings);
-- st_buffer(g, 0) repairs them without changing their area. Coordinates are WGS84
-- (SRID 4326); x/y columns are SVY21 metres (SRID 3414) for distance work.


-- Street-name abbreviations used by HDB ("BEDOK RESERVOIR RD") so they match the
-- spelled-out names in NEA's lists and OpenStreetMap ("Bedok Reservoir Road").
CREATE TEMPORARY VIEW street_abbrev AS
SELECT map(
  'rd', 'road', 'st', 'street', 'ave', 'avenue', 'dr', 'drive', 'cres', 'crescent',
  'cl', 'close', 'ctrl', 'central', 'ctr', 'centre', 'nth', 'north', 'sth', 'south',
  'upp', 'upper', 'lor', 'lorong', 'jln', 'jalan', 'bt', 'bukit', 'tg', 'tanjong',
  'ter', 'terrace', 'pk', 'park', 'pl', 'place', 'ln', 'lane', 'gdns', 'gardens',
  'hts', 'heights', 'cwealth', 'commonwealth', 'kg', 'kampong', 'mkt', 'market',
  'sq', 'square', 'ind', 'industrial', 'pde', 'parade', 'vw', 'view', 'wk', 'walk',
  'e', 'east', 'w', 'west', 'grn', 'green', 'gr', 'grove', 'cir', 'circle', 'bldg', 'building'
) AS m;


-- ------------------------------------------------------------------ reference maps

CREATE OR REFRESH MATERIALIZED VIEW silver_planning_areas (
  CONSTRAINT valid_shape EXPECT (st_isvalid(st_geomfromgeojson(geometry_geojson)))
)
COMMENT 'URA Master Plan 2019 planning areas (55). geometry_geojson is repaired to be valid; area_km2 in SVY21.'
AS
WITH g AS (
  SELECT pln_area_n, region_n, st_buffer(st_geomfromgeojson(geometry_geojson), 0) AS geom
  FROM bronze_ura_mp2019_planning_area_latest
)
SELECT
  pln_area_n AS planning_area,
  region_n AS region,
  st_asgeojson(geom) AS geometry_geojson,
  st_x(st_centroid(geom)) AS lon,
  st_y(st_centroid(geom)) AS lat,
  st_area(st_transform(geom, 3414)) / 1e6 AS area_km2
FROM g;


CREATE OR REFRESH MATERIALIZED VIEW silver_wolbachia_zones (
  CONSTRAINT has_start_date EXPECT (start_date IS NOT NULL)
)
COMMENT 'NEA Wolbachia release zones (live layer) joined to their compiled start dates. start_date is when releases began; confidence high/medium/low.'
AS
SELECT
  w.wolbachia AS zone,
  z.start_date,
  z.confidence,
  z.note,
  z.source AS source_url,
  w.releaseday AS release_days,
  st_asgeojson(st_buffer(st_geomfromgeojson(w.geometry_geojson), 0)) AS geometry_geojson,
  st_area(st_transform(st_buffer(st_geomfromgeojson(w.geometry_geojson), 0), 3414)) / 1e6 AS area_km2
FROM bronze_nea_wolbachia_release_sites_latest w
LEFT JOIN bronze_wolbachia_zone_start_dates_latest z ON z.zone = w.wolbachia;


-- ------------------------------------------------------------------ HDB blocks

-- HDB Property Information (homes per block) has no coordinates; building footprints
-- do. Match on block number within the planning areas of the block's town, and break
-- ties with the street code most often seen for that street among clean matches.
CREATE OR REFRESH MATERIALIZED VIEW silver_hdb_blocks (
  CONSTRAINT located EXPECT (planning_area IS NOT NULL) ON VIOLATION DROP ROW,
  CONSTRAINT has_homes EXPECT (total_dwelling_units > 0)
)
COMMENT 'Residential HDB blocks with coordinates (building centroid), homes, planning area and the earliest Wolbachia start date of any zone covering the block.'
AS
WITH town_pa AS (
  SELECT * FROM VALUES
    ('AMK', 'ANG MO KIO'), ('BB', 'BUKIT BATOK'), ('BD', 'BEDOK'), ('BH', 'BISHAN'),
    ('BM', 'BUKIT MERAH'), ('BP', 'BUKIT PANJANG'), ('BT', 'BUKIT TIMAH'),
    ('CCK', 'CHOA CHU KANG'), ('CL', 'CLEMENTI'), ('CT', 'DOWNTOWN CORE'), ('CT', 'OUTRAM'),
    ('CT', 'ROCHOR'), ('CT', 'SINGAPORE RIVER'), ('CT', 'MUSEUM'), ('CT', 'RIVER VALLEY'),
    ('CT', 'NEWTON'), ('GL', 'GEYLANG'), ('HG', 'HOUGANG'), ('JE', 'JURONG EAST'),
    ('JW', 'JURONG WEST'), ('KWN', 'KALLANG'), ('KWN', 'NOVENA'), ('KWN', 'ROCHOR'),
    ('KWN', 'TOA PAYOH'), ('MP', 'MARINE PARADE'), ('PG', 'PUNGGOL'), ('PRC', 'PASIR RIS'),
    ('QT', 'QUEENSTOWN'), ('SB', 'SEMBAWANG'), ('SGN', 'SERANGOON'), ('SK', 'SENGKANG'),
    ('TAP', 'TAMPINES'), ('TG', 'TENGAH'), ('TP', 'TOA PAYOH'), ('WL', 'WOODLANDS'),
    ('YS', 'YISHUN')
  AS t(town, planning_area)
),
bld AS (
  SELECT
    upper(trim(b.blk_no)) AS blk,
    b.st_cod,
    b.postal_cod,
    st_centroid(st_geomfromgeojson(b.geometry_geojson)) AS c
  FROM bronze_hdb_existing_building_latest b
),
bld_pa AS (
  SELECT bld.*, pa.planning_area
  FROM bld JOIN silver_planning_areas pa
    ON st_contains(st_geomfromgeojson(pa.geometry_geojson), bld.c)
),
prop AS (
  SELECT
    upper(trim(blk_no)) AS blk, street, bldg_contract_town, total_dwelling_units,
    year_completed, max_floor_lvl,
    concat(upper(trim(blk_no)), '|', street) AS prop_key
  FROM bronze_hdb_property_information_latest
  WHERE residential = 'Y'
),
cand AS (
  SELECT p.*, b.st_cod, b.postal_cod, b.c, b.planning_area,
         count(*) OVER (PARTITION BY p.prop_key) AS n_cand
  FROM prop p
  JOIN town_pa t ON t.town = p.bldg_contract_town
  JOIN bld_pa b ON b.blk = p.blk AND b.planning_area = t.planning_area
),
street_code AS (
  SELECT street, max_by(st_cod, n) AS st_cod
  FROM (SELECT street, st_cod, count(*) AS n FROM cand WHERE n_cand = 1 GROUP BY street, st_cod)
  GROUP BY street
),
tie_broken AS (
  SELECT c.*, count(*) OVER (PARTITION BY c.prop_key) AS n_left
  FROM cand c JOIN street_code s ON s.street = c.street AND s.st_cod = c.st_cod
  WHERE c.n_cand > 1
),
matched AS (
  SELECT *, 'unique' AS match_method FROM cand WHERE n_cand = 1
  UNION ALL
  SELECT * EXCEPT (n_left), 'street_code' FROM tie_broken WHERE n_left = 1
),
wol AS (
  SELECT m.prop_key, min(z.start_date) AS wolbachia_start_date, min_by(z.zone, z.start_date) AS wolbachia_zone
  FROM matched m JOIN silver_wolbachia_zones z
    ON st_contains(st_geomfromgeojson(z.geometry_geojson), m.c)
  GROUP BY m.prop_key
)
SELECT
  m.postal_cod AS postal_code,
  m.blk AS blk_no,
  m.street,
  array_join(transform(filter(split(trim(regexp_replace(lower(replace(m.street, '''', '')), '[^a-z0-9 ]', '')), ' '), t -> t != ''),
                       t -> coalesce(try_element_at(a.m, t), t)), ' ') AS street_key,
  m.bldg_contract_town AS town,
  m.planning_area,
  m.total_dwelling_units,
  m.year_completed,
  m.max_floor_lvl,
  st_x(m.c) AS lon,
  st_y(m.c) AS lat,
  st_x(st_transform(m.c, 3414)) AS x_svy21,
  st_y(st_transform(m.c, 3414)) AS y_svy21,
  h3_longlatash3(st_x(m.c), st_y(m.c), 9) AS h3_r9,
  w.wolbachia_zone,
  w.wolbachia_start_date,
  m.match_method
FROM matched m
CROSS JOIN street_abbrev a
LEFT JOIN wol w ON w.prop_key = m.prop_key;


-- ------------------------------------------------------------------ geocoding

-- Every street string in the archived lists, placed on the map:
--   block    - "Road Name (Blk 123)" matched to that HDB block's centroid
--   road     - the road's centre point from OpenStreetMap
--   road_exp - same, after expanding abbreviations
--   none     - not found (kept, with null coordinates)
CREATE OR REFRESH MATERIALIZED VIEW silver_street_locations (
  CONSTRAINT located EXPECT (method != 'none')
)
COMMENT 'Each street named in the Internet Archive cluster lists with its map position and how it was found (block / road / road_exp / none). Road centres (c) OpenStreetMap contributors, ODbL.'
AS
WITH streets AS (
  SELECT DISTINCT s.street
  FROM bronze_nea_dengue_clusters_wayback_latest
  LATERAL VIEW explode(streets) t AS s
),
parsed AS (
  SELECT
    street,
    nullif(upper(regexp_extract(street, r'^(.*?)\s*\((?:Blk|Block)\s*([0-9A-Za-z]+)\)', 2)), '') AS blk,
    trim(regexp_replace(lower(replace(regexp_extract(street, r'^(.*?)\s*\((?:Blk|Block)\s*([0-9A-Za-z]+)\)', 1), '''', '')), '[^a-z0-9 ]', '')) AS blk_street_norm,
    trim(regexp_replace(lower(replace(regexp_replace(street, r'\s*\(.*?\)', ''), '''', '')), '[^a-z0-9 ]', '')) AS road_norm
  FROM streets
),
keyed AS (
  SELECT
    p.street, p.blk, p.road_norm,
    array_join(transform(filter(split(p.blk_street_norm, ' '), t -> t != ''), t -> coalesce(try_element_at(a.m, t), t)), ' ') AS blk_street_key,
    array_join(transform(filter(split(p.road_norm, ' '), t -> t != ''), t -> coalesce(try_element_at(a.m, t), t)), ' ') AS road_exp
  FROM parsed p CROSS JOIN street_abbrev a
),
blocks AS (
  SELECT blk_no, street_key, min(lon) AS lon, min(lat) AS lat
  FROM silver_hdb_blocks GROUP BY blk_no, street_key
),
roads AS (
  SELECT trim(regexp_replace(lower(replace(road_name, '''', '')), '[^a-z0-9 ]', '')) AS road_norm,
         median(lon) AS lon, median(lat) AS lat
  FROM bronze_osm_road_centroids_latest
  GROUP BY 1
)
SELECT
  k.street,
  CASE WHEN b.lon IS NOT NULL THEN 'block' WHEN r1.lon IS NOT NULL THEN 'road'
       WHEN r2.lon IS NOT NULL THEN 'road_exp' ELSE 'none' END AS method,
  coalesce(b.lon, r1.lon, r2.lon) AS lon,
  coalesce(b.lat, r1.lat, r2.lat) AS lat
FROM keyed k
LEFT JOIN blocks b ON b.blk_no = k.blk AND b.street_key = k.blk_street_key
LEFT JOIN roads r1 ON r1.road_norm = k.road_norm
LEFT JOIN roads r2 ON r2.road_norm = k.road_exp;


-- ------------------------------------------------------------------ cluster history

CREATE OR REFRESH MATERIALIZED VIEW silver_cluster_points (
  CONSTRAINT positive_cases EXPECT (cases > 0) ON VIOLATION DROP ROW,
  CONSTRAINT inside_singapore EXPECT (lon IS NULL OR (lon BETWEEN 103.6 AND 104.1 AND lat BETWEEN 1.15 AND 1.48)),
  CONSTRAINT located EXPECT (planning_area IS NOT NULL)
)
COMMENT 'Every location in every NEA cluster list, 2015 -> today: sgcharts addresses (lat/lon given), Internet Archive streets (geocoded), live NEA polygons (centroid). One list date per day; sgcharts until its last date, archive captures after it, live captures after the last archive capture.'
AS
WITH sg AS (
  SELECT
    snapshot_date, 'sgcharts' AS source,
    concat('sgcharts|', CAST(snapshot_date AS STRING), '|', CAST(cluster_number AS STRING)) AS cluster_id,
    CAST(NULL AS STRING) AS locality,
    street_address AS location_text,
    number_of_cases AS cases,
    recent_cases_in_cluster AS recent,
    total_cases_in_cluster AS total,
    CAST(NULL AS STRING) AS alert,
    longitude AS lon, latitude AS lat, 'latlon' AS locate_method
  FROM bronze_sgcharts_dengue_clusters_2013_2020_latest
  WHERE NOT latlon_flagged_incorrect
),
sg_last AS (SELECT max(snapshot_date) AS d FROM sg),
wb_caps AS (
  -- several captures can show the same "as at" date: keep the latest capture of each
  SELECT * FROM bronze_nea_dengue_clusters_wayback_latest
  QUALIFY snap = max(snap) OVER (PARTITION BY asat_d)
),
wb AS (
  SELECT
    to_date(w.asat_d) AS snapshot_date, 'wayback' AS source,
    concat('wayback|', w.asat_d, '|', w.locality) AS cluster_id,
    w.locality,
    w.s.street AS location_text,
    CAST(w.s.cases AS INT) AS cases,
    CAST(w.recent AS INT) AS recent,
    CAST(w.total AS INT) AS total,
    w.alert,
    l.lon, l.lat, l.method AS locate_method
  FROM (SELECT wc.*, s FROM wb_caps wc LATERAL VIEW explode(wc.streets) t AS s) w
  LEFT JOIN silver_street_locations l ON l.street = w.s.street
  WHERE to_date(w.asat_d) > (SELECT d FROM sg_last)
),
wb_last AS (SELECT max(asat_d) AS d FROM bronze_nea_dengue_clusters_wayback_latest),
live_versions AS (
  -- NEA can publish more than once a day: keep the last version of each day
  SELECT * FROM bronze_nea_dengue_clusters
  QUALIFY _version = max(_version) OVER (PARTITION BY to_date(_version_ts_sgt))
),
live AS (
  SELECT
    to_date(_version_ts_sgt) AS snapshot_date, 'live' AS source,
    concat('live|', CAST(to_date(_version_ts_sgt) AS STRING), '|', locality) AS cluster_id,
    locality,
    locality AS location_text,
    CAST(case_size AS INT) AS cases,
    CAST(NULL AS INT) AS recent,
    CAST(case_size AS INT) AS total,
    CASE WHEN case_size >= 10 THEN 'red' ELSE 'yellow' END AS alert,
    st_x(st_centroid(st_geomfromgeojson(geometry_geojson))) AS lon,
    st_y(st_centroid(st_geomfromgeojson(geometry_geojson))) AS lat,
    'polygon_centroid' AS locate_method
  FROM live_versions
  WHERE to_date(_version_ts_sgt) > (SELECT to_date(d) FROM wb_last)
),
pts AS (
  SELECT * FROM sg UNION ALL SELECT * FROM wb UNION ALL SELECT * FROM live
),
places AS (
  -- look up planning area and Wolbachia zone once per distinct position
  SELECT lon, lat, st_point(lon, lat, 4326) AS p
  FROM (SELECT DISTINCT lon, lat FROM pts WHERE lon IS NOT NULL)
),
place_pa AS (
  SELECT pl.lon, pl.lat, min(pa.planning_area) AS planning_area
  FROM places pl JOIN silver_planning_areas pa
    ON st_contains(st_geomfromgeojson(pa.geometry_geojson), pl.p)
  GROUP BY pl.lon, pl.lat
),
place_wol AS (
  SELECT pl.lon, pl.lat, min_by(z.zone, z.start_date) AS wolbachia_zone, min(z.start_date) AS wolbachia_start_date
  FROM places pl JOIN silver_wolbachia_zones z
    ON st_contains(st_geomfromgeojson(z.geometry_geojson), pl.p)
  GROUP BY pl.lon, pl.lat
)
SELECT
  p.*,
  CASE WHEN p.lon IS NOT NULL THEN st_x(st_transform(st_point(p.lon, p.lat, 4326), 3414)) END AS x_svy21,
  CASE WHEN p.lon IS NOT NULL THEN st_y(st_transform(st_point(p.lon, p.lat, 4326), 3414)) END AS y_svy21,
  CASE WHEN p.lon IS NOT NULL THEN h3_longlatash3(p.lon, p.lat, 9) END AS h3_r9,
  a.planning_area,
  w.wolbachia_zone,          -- a zone in NEA's current layer covers this spot ...
  w.wolbachia_start_date     -- ... and releases there began on this date (may be after snapshot_date)
FROM pts p
LEFT JOIN place_pa a ON a.lon = p.lon AND a.lat = p.lat
LEFT JOIN place_wol w ON w.lon = p.lon AND w.lat = p.lat;


CREATE OR REFRESH MATERIALIZED VIEW silver_clusters (
  CONSTRAINT has_planning_area EXPECT (planning_area IS NOT NULL),
  CONSTRAINT mostly_located EXPECT (cases_located >= 0.5 * cases_listed)
)
COMMENT 'One row per cluster per list date. planning_area = the area holding most of its located cases. is_active = cases in the last 2 weeks (recent > 0; live and pre-2013 lists have no recent count and count as active). alert: red = 10+ cases (NEA), yellow = fewer.'
AS
WITH by_pa AS (
  SELECT cluster_id, planning_area, sum(cases) AS pa_cases
  FROM silver_cluster_points WHERE planning_area IS NOT NULL
  GROUP BY cluster_id, planning_area
),
majority AS (
  SELECT cluster_id, max_by(planning_area, pa_cases) AS planning_area
  FROM by_pa GROUP BY cluster_id
)
SELECT
  c.cluster_id,
  max(c.snapshot_date) AS snapshot_date,   -- these are the same on every row of a cluster
  max(c.source) AS source,
  max(c.locality) AS locality,
  max(c.recent) AS recent,
  max(c.total) AS total,
  sum(c.cases) AS cases_listed,
  sum(CASE WHEN c.planning_area IS NOT NULL THEN c.cases ELSE 0 END) AS cases_located,
  sum(CASE WHEN c.wolbachia_zone IS NOT NULL THEN c.cases ELSE 0 END) AS cases_in_wolbachia_zone_today,
  sum(CASE WHEN c.wolbachia_start_date <= date_sub(c.snapshot_date, 60) THEN c.cases ELSE 0 END) AS cases_protected_60d,
  max(m.planning_area) AS planning_area,
  sum(c.cases * c.lon) / sum(CASE WHEN c.lon IS NOT NULL THEN c.cases END) AS lon,
  sum(c.cases * c.lat) / sum(CASE WHEN c.lon IS NOT NULL THEN c.cases END) AS lat,
  coalesce(max(c.alert), CASE WHEN max(c.total) >= 10 THEN 'red' ELSE 'yellow' END) AS alert,
  coalesce(max(c.recent) > 0, true) AS is_active
FROM silver_cluster_points c
LEFT JOIN majority m ON m.cluster_id = c.cluster_id
GROUP BY c.cluster_id;


-- ------------------------------------------------------------------ archive cross-check

CREATE OR REFRESH MATERIALIZED VIEW silver_source_agreement (
  CONSTRAINT archives_agree EXPECT (min_cases = max_cases)
)
COMMENT 'For every date covered by 2+ archives (sgcharts, Internet Archive, Scientific Data 2020), total cases listed by each. They should match exactly.'
AS
WITH totals AS (
  SELECT snapshot_date, 'sgcharts' AS source, sum(number_of_cases) AS cases
  FROM bronze_sgcharts_dengue_clusters_2013_2020_latest WHERE NOT latlon_flagged_incorrect
  GROUP BY 1
  UNION ALL
  SELECT to_date(asat_d), 'wayback', sum(street_sum)
  FROM (SELECT * FROM bronze_nea_dengue_clusters_wayback_latest QUALIFY snap = max(snap) OVER (PARTITION BY asat_d))
  GROUP BY 1
  UNION ALL
  SELECT to_date(CAST(date AS STRING), 'yyMMdd'), 'scidata_2020', sum(case_number)
  FROM bronze_scidata_dengue_clusters_2020_latest
  GROUP BY 1
)
SELECT
  snapshot_date,
  map_from_entries(collect_list(struct(source, cases))) AS cases_by_source,
  min(cases) AS min_cases,
  max(cases) AS max_cases
FROM totals
GROUP BY snapshot_date
HAVING count(*) >= 2;
