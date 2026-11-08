"""Downloading and reading hourly demand and weather data from each free source."""
import io
import json
import time

import pandas as pd
import requests

from forecast_globe.config import CACHE, EIA_KEY, REGIONS, START, WX


def get(name, url, params=None, live=False):
    """Download once into cache/. Live files (data still growing) expire after an hour.
    Timeouts, rate limits and cut-off JSON are retried, and a broken file is never cached."""
    path = CACHE / name
    if path.exists() and not (live and time.time() - path.stat().st_mtime > 3600):
        return path.read_bytes()
    last = None
    for wait in (0, 10, 30, 65, 130, 130):
        time.sleep(wait)
        try:
            res = requests.get(url, params=params, headers={"User-Agent": "energy-forecast-demo"}, timeout=240)
        except requests.RequestException as e:
            last = e
            continue
        if res.status_code in (429, 500, 502, 503, 504):
            last = requests.HTTPError(f"{res.status_code} from {url}")
            continue
        res.raise_for_status()
        if name.endswith(".json"):
            try:
                json.loads(res.content)
            except ValueError as e:  # cut off or an error page served as 200
                last = e
                continue
        CACHE.mkdir(exist_ok=True)
        path.write_bytes(res.content)
        return res.content
    raise last


def now_hour():
    return pd.Timestamp.now(tz="UTC").floor("h")


def months(now):
    return [(p, p == now.to_period("M")) for p in pd.period_range("2024-11", now.tz_localize(None), freq="M")]


_eia = None


def eia_table():
    """All US demand and operator forecast. Half-year files need no key; the API fills the newest hours."""
    global _eia
    if _eia is not None:
        return _eia
    now, codes, rows = now_hour(), [r["code"] for r in REGIONS if r["src"] == "eia"], []
    cols = {"Balancing Authority": "ba", "UTC Time at End of Hour": "end", "Demand (MW)": "D", "Demand Forecast (MW)": "DF"}
    for year in range(2024, now.year + 1):
        for half in ("Jan_Jun", "Jul_Dec"):
            first = pd.Timestamp(f"{year}-{'01' if half == 'Jan_Jun' else '07'}-01", tz="UTC")
            if first > now or first + pd.DateOffset(months=6) < START:
                continue
            live = first + pd.DateOffset(months=6) > now
            small = CACHE / f"eia_{year}_{half}.csv"
            if not small.exists() or (live and time.time() - small.stat().st_mtime > 3600):
                name = f"EIA930_BALANCE_{year}_{half}.csv"
                raw = get(name, f"https://www.eia.gov/electricity/gridmonitor/sixMonthFiles/{name}", live=live)
                df = pd.read_csv(io.BytesIO(raw), usecols=list(cols), thousands=",").rename(columns=cols)
                df[df.ba.isin(codes)].to_csv(small, index=False)
                (CACHE / name).unlink()  # 50 MB each, only these regions are needed
            rows.append(pd.read_csv(small))
    files = pd.concat(rows)
    files["t"] = pd.to_datetime(files.end, format="%m/%d/%Y %I:%M:%S %p", utc=True) - pd.Timedelta(hours=1)
    files = files.drop(columns="end").groupby(["ba", "t"]).last().reset_index()

    # newest 5000 rows is about 17 days across these regions, longer than the file lag
    params = {"api_key": EIA_KEY, "frequency": "hourly", "data[0]": "value", "facets[respondent][]": codes,
              "facets[type][]": ["D", "DF"], "sort[0][column]": "period", "sort[0][direction]": "desc", "length": 5000}
    pages = [get(f"eia_tail{k}.json", "https://api.eia.gov/v2/electricity/rto/region-data/data/", {**params, "offset": 5000 * k}, live=True) for k in range(2)]
    api = pd.DataFrame([row for raw in pages for row in json.loads(raw)["response"]["data"]])
    api["t"] = pd.to_datetime(api.period, utc=True) - pd.Timedelta(hours=1)
    api["value"] = pd.to_numeric(api.value, errors="coerce")
    api = api.pivot_table(index=["respondent", "t"], columns="type", values="value").reset_index().rename(columns={"respondent": "ba"})
    both = files.merge(api, on=["ba", "t"], suffixes=("", "_api")).dropna(subset=["D", "D_api"])
    if len(both):  # files and API must share a time convention, or lags would be silently shifted
        assert (both.D - both.D_api).abs().median() / both.D.median() < 0.02, "EIA file and API disagree"
    _eia = pd.concat([files, api]).groupby(["ba", "t"]).last().reset_index()
    return _eia


def load_eia(r):
    df = eia_table().query("ba == @r['code']").set_index("t")
    return df.D.dropna(), df.DF.dropna()


def load_ieso(r):
    parts = []
    for y in range(2024, now_hour().year + 1):
        raw = get(f"ieso_{y}.csv", f"https://reports-public.ieso.ca/public/Demand/PUB_Demand_{y}.csv", live=y == now_hour().year)
        parts.append(pd.read_csv(io.BytesIO(raw), skiprows=3))
    df = pd.concat(parts)
    # report is hour-ending in EST all year, no daylight saving
    t = pd.to_datetime(df.Date) + pd.to_timedelta(df.Hour - 1, unit="h") + pd.Timedelta(hours=5)
    return pd.Series(df["Ontario Demand"].values, index=t.dt.tz_localize("UTC")), None


def load_ons(r):
    parts = []
    for y in range(2024, now_hour().year + 1):
        url = f"https://ons-aws-prod-opendata.s3.amazonaws.com/dataset/curva-carga-ho/CURVA_CARGA_{y}.csv"
        parts.append(pd.read_csv(io.BytesIO(get(f"ons_{y}.csv", url, live=y == now_hour().year)), sep=";"))
    df = pd.concat(parts).query("id_subsistema == @r['code']")
    t = pd.to_datetime(df.din_instante).dt.tz_localize("Etc/GMT+3").dt.tz_convert("UTC")  # Brasilia time
    return pd.Series(df.val_cargaenergiahomwmed.values, index=t), None


def load_elexon(r):
    parts, now = [], now_hour()
    for a in pd.date_range(START, now, freq="28D"):
        b = a + pd.Timedelta(days=27)
        raw = get(f"elexon_{a:%Y%m%d}.json", "https://data.elexon.co.uk/bmrs/api/v1/demand/outturn",
                  {"settlementDateFrom": f"{a:%Y-%m-%d}", "settlementDateTo": f"{b:%Y-%m-%d}", "format": "json"}, live=b >= now - pd.Timedelta(days=2))
        parts.append(pd.DataFrame(json.loads(raw)["data"]))
    df = pd.concat(parts)
    return pd.Series(df.initialDemandOutturn.values, index=pd.to_datetime(df.startTime, utc=True)), None


def load_echarts(r):
    raw = get(f"echarts_{r['code']}.json", "https://api.energy-charts.info/public_power",
              {"country": r["code"], "start": f"{START:%Y-%m-%d}", "end": f"{now_hour() + pd.Timedelta(days=1):%Y-%m-%d}"}, live=True)
    j = json.loads(raw)
    vals = next(p["data"] for p in j["production_types"] if p["name"] == "Load")
    return pd.Series(vals, index=pd.to_datetime(j["unix_seconds"], unit="s", utc=True), dtype=float), None


def load_tepco(r):
    parts, now = [], now_hour()
    for p, live in months(now):
        url = f"https://www.tepco.co.jp/forecast/html/images/eria_jukyu_{p:%Y%m}_03.csv"
        parts.append(pd.read_csv(io.BytesIO(get(f"tepco_{p:%Y%m}.csv", url, live=live)), encoding="cp932", encoding_errors="replace", skiprows=1, usecols=[0, 1, 2], names=["d", "h", "mw"], header=0))
    df = pd.concat(parts)
    t = pd.to_datetime(df.d + " " + df.h, format="%Y/%m/%d %H:%M").dt.tz_localize("Asia/Tokyo").dt.tz_convert("UTC")
    return pd.Series(df.mw.values, index=t, dtype=float), None


def load_aemo(r):
    parts, now = [], now_hour()
    for p, live in months(now):
        url = f"https://aemo.com.au/aemo/data/nem/priceanddemand/PRICE_AND_DEMAND_{p:%Y%m}_{r['code']}.csv"
        parts.append(pd.read_csv(io.BytesIO(get(f"aemo_{r['code']}_{p:%Y%m}.csv", url, live=live))))
    df = pd.concat(parts)
    # stamps mark the end of each interval, in AEST all year; the file changed from 30 to 5 minutes
    t = pd.to_datetime(df.SETTLEMENTDATE).dt.tz_localize("Etc/GMT-10").dt.tz_convert("UTC") - pd.Timedelta(minutes=1)
    return pd.Series(df.TOTALDEMAND.values, index=t), None


def load_area(r):
    """Hourly demand (and the operator's own forecast, when one is published) for one area."""
    return globals()[f"load_{r['src']}"](r)


def load_sum(r):
    """A country made of several areas: add their hourly demand, and their operator forecasts when every part has one."""
    loads, ops = [], []
    for part in r["parts"]:
        raw, op = load_area(part)
        loads.append(hourly(raw))
        ops.append(op)
    total = pd.concat(loads, axis=1, join="inner").sum(axis=1)  # only hours every part reports
    op = pd.concat(ops, axis=1, join="inner").sum(axis=1) if all(o is not None for o in ops) else None
    return total, op


def hourly(s):
    s = s[~s.index.duplicated()].sort_index().resample("h").mean()
    s = s[s.index >= START]
    med = s.median()
    return s.where(s.between(0.3 * med, 2.5 * med)).dropna()  # reported glitches, not real demand


def weather(r, now):
    """Day-ahead forecasts as issued, for history too, so training and backtest see realistic weather error."""
    lat = ",".join(str(p[0]) for p in r["pts"])
    lon = ",".join(str(p[1]) for p in r["pts"])
    month = now.replace(day=1)
    prev = ",".join(v + "_previous_day1" for v in WX)
    frames = []
    for name, a, b, live in ((f"wx_{r['id']}_{month:%Y%m}.json", START, month - pd.Timedelta(days=1), False),
                             (f"wx_{r['id']}_recent.json", month, now, True)):
        raw = get(name, "https://previous-runs-api.open-meteo.com/v1/forecast",
                  dict(latitude=lat, longitude=lon, start_date=f"{a:%Y-%m-%d}", end_date=f"{b:%Y-%m-%d}", hourly=prev, timezone="UTC"), live=live)
        frames.append(wx_frame(raw, "_previous_day1"))
    raw = get(f"wx_{r['id']}_fc.json", "https://api.open-meteo.com/v1/forecast",
              dict(latitude=lat, longitude=lon, hourly=",".join(WX), past_days=2, forecast_days=4, timezone="UTC"), live=True)
    return pd.concat(frames).dropna().combine_first(wx_frame(raw, "")).sort_index()


def wx_frame(raw, suffix):
    places = json.loads(raw)
    places = places if isinstance(places, list) else [places]  # one location comes back as a single object
    frames = [pd.DataFrame(p["hourly"]).assign(t=lambda d: pd.to_datetime(d.time, utc=True)).set_index("t").drop(columns="time") for p in places]
    avg = sum(frames) / len(frames)
    return avg.rename(columns=lambda c: c.replace(suffix, "") if suffix else c)
