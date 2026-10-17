"""Registry of every raw source we ingest.

Each distinct ``name`` becomes one bronze Delta table ``bronze_<name>``. Entries
that share a name (the five regional dengue-case files, the yearly station
weather files) land side by side and are told apart by dataset_id / ``tags``.

kinds:
  datagov   - a data.gov.sg dataset (CSV or GeoJSON), downloaded via the
              v1 poll-download API and re-landed whenever its lastUpdatedAt changes
  http_zip  - a zip archive of CSVs (SGCharts Outbreak historical cluster snapshots)
  http_file - a single file at a stable URL (figshare)
  nea_page  - NEA's dengue cluster web page, parsed into one record per cluster (nea_page.py)
  seed      - a file checked into this repo under data/seed/ (CSV, or NDJSON with fmt="ndjson")
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Source:
    name: str
    kind: str
    group: str
    description: str
    dataset_id: str | None = None
    url: str | None = None
    fmt: str = "csv"  # csv | geojson | ndjson
    # Options passed to the bronze CSV reader (e.g. headerless files)
    csv_header: bool = True
    csv_schema: str | None = None
    # (glob, filename prefix) pairs: archive members to land, flattened into one folder
    member_globs: tuple = ()
    large: bool = False
    tags: tuple = field(default_factory=tuple)


def _dg(name, dataset_id, fmt, group, description, **kw):
    return Source(name=name, kind="datagov", dataset_id=dataset_id, fmt=fmt,
                  group=group, description=description, **kw)


SOURCES: list[Source] = [
    # ---------------------------------------------------------------- NEA dengue
    _dg("nea_dengue_clusters", "d_dbfabf16158d1b0e1c420627c0819168", "geojson", "dengue",
        "NEA active dengue clusters (polygons, case counts). Updated daily; every change is kept as a version."),
    Source(
        name="nea_dengue_cluster_page", kind="nea_page", group="dengue", fmt="ndjson",
        url="https://www.nea.gov.sg/dengue-zika/dengue/dengue-clusters",
        description="NEA's dengue cluster page, captured at every run and kept whenever the list changes. Same fields "
                    "as the Internet Archive captures: alert, recent (onset in last 2 weeks), total, street breakdown. "
                    "asat_d is the 'accurate as at' date printed on the page.",
    ),
    _dg("nea_dengue_cases_by_region", "d_5f90123ce50e3d323bfd0ff3c9a84601", "geojson", "dengue",
        "NEA dengue cases - Central region", tags=("central",)),
    _dg("nea_dengue_cases_by_region", "d_e95e09fb3e54a3249e4bdf32e463effa", "geojson", "dengue",
        "NEA dengue cases - North East region", tags=("north_east",)),
    _dg("nea_dengue_cases_by_region", "d_ce8cf1e3f4f5ff1a68393a7cb101eb9c", "geojson", "dengue",
        "NEA dengue cases - North West region", tags=("north_west",)),
    _dg("nea_dengue_cases_by_region", "d_2c13093a9d36377478755716f861ef14", "geojson", "dengue",
        "NEA dengue cases - South East region", tags=("south_east",)),
    _dg("nea_dengue_cases_by_region", "d_e34261c5ccace716132b55a5b02ebb1f", "geojson", "dengue",
        "NEA dengue cases - South West region", tags=("south_west",)),
    _dg("nea_high_aedes_areas", "d_5d060d8b7838a15e8906fb22c50dbf51", "geojson", "vector",
        "NEA areas with high Aedes mosquito population"),
    _dg("nea_wolbachia_release_sites", "d_533d673fe769e58f71609c18080ea7a7", "geojson", "vector",
        "NEA Project Wolbachia release sites (polygons)"),

    # --------------------------------------------------------- MOH weekly cases
    _dg("moh_weekly_infectious_disease", "d_ca168b2cb763640d72c4600a68f9909e", "csv", "cases",
        "MOH Weekly Infectious Disease Bulletin cases by disease, 2012-2022"),
    _dg("moh_weekly_dengue_dhf", "d_ac1eecf0886ff0bceefbc51556247015", "csv", "cases",
        "Weekly number of dengue and dengue haemorrhagic fever cases"),

    # ---------------------------------------------------- Weather (monthly, Changi)
    _dg("weather_rainfall_monthly_total", "d_b16d06b83473fdfcc92ed9d37b66ba58", "csv", "weather",
        "Rainfall - monthly total (mm)"),
    _dg("weather_rainfall_monthly_rain_days", "d_134857f63c76d227b6fa045f31ce59c1", "csv", "weather",
        "Rainfall - monthly number of rain days"),
    _dg("weather_rainfall_monthly_max_daily", "d_53ffdaa97e48ce934cf92799d381f3b1", "csv", "weather",
        "Rainfall - monthly highest daily total (mm)"),
    _dg("weather_temp_monthly_mean", "d_755290a24afe70c8f9e8bcbf9f251573", "csv", "weather",
        "Surface air temperature - monthly mean"),
    _dg("weather_temp_monthly_mean_daily_max", "d_b0fb69de67054d6d741bea78274ecc2a", "csv", "weather",
        "Surface air temperature - monthly mean daily maximum"),
    _dg("weather_temp_monthly_mean_daily_min", "d_c92edf841336f03ab51c6693ac8d33ad", "csv", "weather",
        "Surface air temperature - monthly mean daily minimum"),
    _dg("weather_temp_monthly_abs_max", "d_72a4d7402d4014f1999a009864f64a11", "csv", "weather",
        "Surface air temperature - monthly absolute extreme maximum"),
    _dg("weather_temp_monthly_abs_min", "d_3b4b0418948847eaca93546f7574e365", "csv", "weather",
        "Surface air temperature - monthly absolute extreme minimum"),
    _dg("weather_humidity_monthly_mean", "d_e3b1111a26b2aefef4f386c068d4ca5d", "csv", "weather",
        "Relative humidity - monthly mean"),

    # ------------------------------------------------------------- HDB / URA
    _dg("hdb_property_information", "d_17f5382f26140b1fdae0ba2ef6239d2f", "csv", "geo",
        "HDB property information (block, street, year completed, units by type)"),
    _dg("hdb_existing_building", "d_16b157c52ed637edd6ba1232e026258d", "geojson", "geo",
        "HDB existing building footprints (polygons with block number, postal code). Gives HDB blocks "
        "their coordinates; HDB Property Information has none."),
    _dg("ura_mp2019_planning_area", "d_4765db0e87b9c86336792efe8a1f7a66", "geojson", "geo",
        "URA Master Plan 2019 planning area boundary (no sea)"),
    _dg("ura_mp2019_subzone", "d_8594ae9ff96d0c708bc2af633048edfb", "geojson", "geo",
        "URA Master Plan 2019 subzone boundary (no sea)"),
    _dg("ura_mp2025_planning_area", "d_2cc750190544007400b2cfd5d7f53209", "geojson", "geo",
        "URA Master Plan 2025 planning area boundary (no sea)"),

    # ---------------------------------------- Past NEA cluster lists (archives)
    Source(
        name="sgcharts_dengue_clusters_2013_2020", kind="http_zip", group="dengue_history",
        url="https://outbreak.sgcharts.com/sgcharts.zip",
        member_globs=(
            ("sgcharts/csv/*-clusters.csv", ""),
            # May 2013 - Apr 2015 snapshots SGCharts flags as having wrong lat/long.
            # Counts and addresses are still usable; bronze marks them latlon_flagged_incorrect.
            ("sgcharts/incorrect_latitude_longitude/*-clusters.csv", "flagged_latlon__"),
        ),
        csv_header=False,
        # Column order from https://outbreak.sgcharts.com/data
        csv_schema=("number_of_cases INT, street_address STRING, latitude DOUBLE, longitude DOUBLE, "
                    "cluster_number INT, recent_cases_in_cluster INT, total_cases_in_cluster INT, "
                    "date_yymmdd STRING, month_number INT"),
        description="SGCharts Outbreak: twice-weekly snapshots of NEA's dengue cluster page, May 2013 - Nov 2020 "
                    "(lat/long before Jul 2015 flagged incorrect by SGCharts). "
                    "recent_cases_in_cluster is -1 before Dec 2013.",
    ),
    Source(
        name="scidata_dengue_clusters_2020", kind="http_file", group="dengue_history",
        url="https://ndownloader.figshare.com/files/25263446",
        description="Geographical dengue clusters during the 2020 COVID-19 lockdown (Feb-Jul 2020), "
                    "figshare 10.6084/m9.figshare.13142777; Huang et al., Scientific Data 9:547.",
    ),

    Source(
        name="nea_dengue_clusters_wayback", kind="seed", group="dengue_history", fmt="ndjson",
        url="data/seed/nea_dengue_clusters_wayback_2018_2026.ndjson",
        description="NEA dengue cluster lists from 74 Internet Archive captures of nea.gov.sg, Dec 2018 - Sep 2026 "
                    "(one row per cluster; streets = street-level case counts; capture_url per row). "
                    "Parsed from the archived pages; source NEA. Built by scripts/build_wayback_seed.py.",
    ),

    Source(
        name="osm_road_centroids", kind="seed", group="geo",
        url="data/seed/osm_road_centroids.csv",
        description="Centre point of every named road in Singapore (median of its OpenStreetMap segments), used to "
                    "place archive streets with no HDB block number. (c) OpenStreetMap contributors, ODbL. "
                    "Built by scripts/build_osm_roads_seed.py.",
    ),

    # ---------------------------------------------------------- Manual seeds
    Source(
        name="wolbachia_release_start_dates", kind="seed", group="vector",
        url="data/seed/wolbachia_release_start_dates.csv",
        description="Project Wolbachia phase/site start dates compiled by hand from NEA web pages (source_url per row).",
    ),
    Source(
        name="wolbachia_zone_start_dates", kind="seed", group="vector",
        url="data/seed/wolbachia_zone_start_dates.csv",
        description="Release start date for each of the 31 zones in NEA's Wolbachia Release Sites layer; zone names "
                    "match that layer exactly (note the trailing space in 'Moulmein - Dorset '). "
                    "confidence = high/medium/low; Circuit Road is inferred (low).",
    ),
    Source(
        name="cda_weekly_dengue", kind="seed", group="cases",
        url="data/seed/cda_weekly_dengue_2023_2026.csv",
        description="Weekly national dengue and DHF cases by epi-week, 2023-2026, parsed from CDA Weekly Infectious "
                    "Diseases Bulletin PDFs (cda.gov.sg). Some 2025 weeks are missing.",
    ),
]

# Station-level historical readings by year. These are large (one CSV per year per
# variable), so they only land when include_large=True.
_HIST_RAIN = {
    2016: "d_79a6f0fb898996d415b207bb26ed0fa6", 2017: "d_1990a5a1aeaf3dd243cf4dae294a61c4",
    2018: "d_024fb501ce7092b71bb713eaf54fa7eb", 2019: "d_61995f092320e7155b7528050880b502",
    2020: "d_9e7de44094f876f6804b8b5bcee45c81", 2021: "d_3b41598f74f1f11fc3430348fea51af5",
    2022: "d_42d64cc6c176ace1c52fbb40b9ede302", 2023: "d_f864cc30d58b467db83659ad17c737bf",
    2024: "d_a0b69d3e02576a1fd0ab673e71f83507",
}
_HIST_TEMP = {
    2016: "d_2b359912668f5f55fde28254e9c9092d", 2017: "d_320fc57e7871bd0cd7a713f0f6f2f89e",
    2018: "d_ef585339f3f57862d6cf13edd5a628c1", 2019: "d_6a384c5f4d719f33619563b27091b8fb",
    2020: "d_10d453bc02d1a41daf6373b96b1929e4", 2021: "d_557fbb859f12a8af29ef4e1375288165",
    2022: "d_6fd47a46bd59c5cb70d9561b404d0a64", 2023: "d_370bbfc65de96ad93eaefa182135d1c0",
    2024: "d_910c81fce47f38574015b3882ac59254",
}
# All years of a variable share one bronze table; the year goes in the _part column.
SOURCES += [
    _dg("weather_station_rainfall_hist", ds, "csv", "weather_station",
        f"Historical station-level rainfall readings ({y})", large=True, tags=(str(y),))
    for y, ds in sorted(_HIST_RAIN.items())
]
SOURCES += [
    _dg("weather_station_air_temp_hist", ds, "csv", "weather_station",
        f"Historical station-level air temperature readings ({y})", large=True, tags=(str(y),))
    for y, ds in sorted(_HIST_TEMP.items())
]


def select(groups=None, names=None, include_large=False):
    out = []
    for s in SOURCES:
        if s.large and not include_large:
            continue
        if groups and s.group not in groups:
            continue
        if names and s.name not in names:
            continue
        out.append(s)
    return out


def table_names(sources):
    """Unique bronze tables (regional/yearly sources share one table)."""
    seen = {}
    for s in sources:
        seen.setdefault(s.name, s)
    return list(seen.values())


def part_labels(name):
    """dataset_id -> tag (region or year) for sources that share a table."""
    return {s.dataset_id: s.tags[0] for s in SOURCES if s.name == name and s.dataset_id and s.tags}
