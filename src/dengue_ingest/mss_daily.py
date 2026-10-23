"""Daily weather per station from the Meteorological Service Singapore (MSS).

https://www.weather.gov.sg/climate-historical-daily/ publishes one CSV per station per
month (rainfall, temperature, wind), for ~110 stations, from the 1980s to last month.
Unlike the data.gov.sg station history (2016-2024 only), it has no break up to today,
so the model can use local rain and temperature in every year it learns from and in
the live forecast.

The station list and coordinates come from the same page (it embeds them for its map).
Headers changed over the years (case, a byte-order mark, "°C" in Latin-1), so every
file is mapped onto one fixed set of columns.
"""

import csv
import io
import re

PAGE_URL = "https://www.weather.gov.sg/climate-historical-daily/"
FILE_URL = "https://www.weather.gov.sg/files/dailydata/DAILYDATA_{station}_{yyyymm}.csv"
FIRST_MONTH = (2015, 1)  # cluster history starts Jul 2015; six months earlier leaves room for lagged weather

COLUMNS = ["station_id", "station_name", "latitude", "longitude", "date",
           "rain_mm", "rain_max_30min_mm", "rain_max_60min_mm", "rain_max_120min_mm",
           "temp_mean_c", "temp_max_c", "temp_min_c", "wind_mean_kmh", "wind_max_kmh"]
# Header keywords (lower case, letters and digits only) for each value column, in file order
_HEADER = [("dailyrainfalltotal", "rain_mm"), ("highest30min", "rain_max_30min_mm"),
           ("highest60min", "rain_max_60min_mm"), ("highest120min", "rain_max_120min_mm"),
           ("meantemperature", "temp_mean_c"), ("maximumtemperature", "temp_max_c"),
           ("minimumtemperature", "temp_min_c"), ("meanwindspeed", "wind_mean_kmh"),
           ("maxwindspeed", "wind_max_kmh")]


def stations(page: str):
    """[{station_id, station_name, latitude, longitude}] from the page's map data."""
    out = []
    for body in re.findall(r"stations\[\d+\]=\{(.*?)\};", page, re.S):
        f = dict(re.findall(r'(\w+):"([^"]*)"', body))
        out.append({"station_id": f["station_code"], "station_name": f["station_name"].strip(),
                    "latitude": float(f["latitude"]), "longitude": float(f["longitude"])})
    if not out:
        raise RuntimeError("MSS daily page: no station list found; the page layout may have changed")
    return out


def _text(raw: bytes) -> str:
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def _num(x):
    x = x.strip()
    try:
        return float(x)
    except ValueError:  # "-", "—" or blank = not recorded
        return None


def rows(raw: bytes, station):
    """One station-month file -> rows in COLUMNS order."""
    r = list(csv.reader(io.StringIO(_text(raw))))
    head = [re.sub(r"[^0-9a-z]", "", h.lower()) for h in r[0]]
    idx = {}
    for key, col in _HEADER:
        hit = [i for i, h in enumerate(head) if h.startswith(key)]
        if hit:
            idx[col] = hit[0]
    if "rain_mm" not in idx or head[1:4] != ["year", "month", "day"]:
        raise RuntimeError(f"MSS daily file for {station['station_id']}: unexpected header {r[0]}")
    out = []
    for line in r[1:]:
        if len(line) < 4 or not line[1].strip():
            continue
        date = f"{int(line[1]):04d}-{int(line[2]):02d}-{int(line[3]):02d}"
        vals = {c: _num(line[i]) if i < len(line) else None for c, i in idx.items()}
        out.append([station["station_id"], station["station_name"], station["latitude"], station["longitude"],
                    date] + [vals.get(c) for c in COLUMNS[5:]])
    return out


def to_csv(all_rows) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(COLUMNS)
    w.writerows(["" if v is None else v for v in row] for row in all_rows)
    return buf.getvalue().encode("utf-8")
