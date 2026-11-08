"""Fetch real grid load + weather, train one model per region, write data.js for index.html."""
import io, json, math, os, time
from pathlib import Path

import holidays
import lightgbm as lgb
import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).parent
CACHE = ROOT / "cache"
START = pd.Timestamp("2024-11-01", tz="UTC")
HORIZON = 48
TEST_DAYS = 90
EIA_KEY = os.environ.get("EIA_API_KEY") or "DEMO_KEY"  # DEMO_KEY allows about 10 calls an hour
WX = ["temperature_2m", "relative_humidity_2m", "wind_speed_10m", "shortwave_radiation", "cloud_cover"]

# points = the region's main load centres; weather is averaged across them
REGIONS = [
    dict(id="CISO", name="California", op="CAISO", cc="US", tz="America/Los_Angeles", src="eia", code="CISO", pts=[(34.05, -118.25), (37.77, -122.42), (32.72, -117.16)]),
    dict(id="ERCO", name="Texas", op="ERCOT", cc="US", tz="America/Chicago", src="eia", code="ERCO", pts=[(32.78, -96.8), (29.76, -95.37), (30.27, -97.74)]),
    dict(id="PJM", name="Mid-Atlantic US", op="PJM", cc="US", tz="America/New_York", src="eia", code="PJM", pts=[(39.95, -75.17), (38.9, -77.04), (41.88, -87.63)]),
    dict(id="NYIS", name="New York", op="NYISO", cc="US", tz="America/New_York", src="eia", code="NYIS", pts=[(40.71, -74.01), (42.89, -78.88), (42.65, -73.75)]),
    dict(id="ISNE", name="New England", op="ISO-NE", cc="US", tz="America/New_York", src="eia", code="ISNE", pts=[(42.36, -71.06), (41.76, -72.67), (41.82, -71.41)]),
    dict(id="MISO", name="US Midwest", op="MISO", cc="US", tz="America/Chicago", src="eia", code="MISO", pts=[(44.98, -93.27), (42.33, -83.05), (38.63, -90.2)]),
    dict(id="ONT", name="Ontario", op="IESO", cc="CA", sub="ON", tz="America/Toronto", src="ieso", pts=[(43.65, -79.38), (45.42, -75.7), (43.26, -79.87)]),
    dict(id="BRSE", name="Brazil Southeast", op="ONS", cc="BR", tz="America/Sao_Paulo", src="ons", code="SE", pts=[(-23.55, -46.63), (-22.91, -43.17), (-19.92, -43.94)]),
    dict(id="BRNE", name="Brazil Northeast", op="ONS", cc="BR", tz="America/Fortaleza", src="ons", code="NE", pts=[(-12.97, -38.5), (-8.05, -34.88), (-3.73, -38.52)]),
    dict(id="GB", name="Great Britain", op="NESO", cc="GB", tz="Europe/London", src="elexon", pts=[(51.51, -0.13), (53.48, -2.24), (55.86, -4.25)]),
    dict(id="FR", name="France", op="RTE", cc="FR", tz="Europe/Paris", src="echarts", code="fr", pts=[(48.86, 2.35), (45.76, 4.84), (43.3, 5.37)]),
    dict(id="DE", name="Germany", op="4 TSOs", cc="DE", tz="Europe/Berlin", src="echarts", code="de", pts=[(52.52, 13.4), (50.11, 8.68), (48.14, 11.58)]),
    dict(id="ES", name="Spain", op="REE", cc="ES", tz="Europe/Madrid", src="echarts", code="es", pts=[(40.42, -3.7), (41.39, 2.17), (37.39, -5.98)]),
    dict(id="IT", name="Italy", op="Terna", cc="IT", tz="Europe/Rome", src="echarts", code="it", pts=[(45.46, 9.19), (41.9, 12.5), (40.85, 14.27)]),
    dict(id="PL", name="Poland", op="PSE", cc="PL", tz="Europe/Warsaw", src="echarts", code="pl", pts=[(52.23, 21.01), (50.26, 19.02), (51.76, 19.46)]),
    dict(id="SE", name="Sweden", op="Svenska kraftnat", cc="SE", tz="Europe/Stockholm", src="echarts", code="se", pts=[(59.33, 18.07), (57.71, 11.97), (55.6, 13.0)]),
    dict(id="TKY", name="Tokyo", op="TEPCO", cc="JP", tz="Asia/Tokyo", src="tepco", pts=[(35.68, 139.69), (35.44, 139.64), (35.86, 139.65)]),
    dict(id="NSW", name="New South Wales", op="AEMO", cc="AU", sub="NSW", tz="Australia/Sydney", src="aemo", code="NSW1", pts=[(-33.87, 151.21), (-32.93, 151.78), (-34.42, 150.89)]),
    dict(id="VIC", name="Victoria", op="AEMO", cc="AU", sub="VIC", tz="Australia/Melbourne", src="aemo", code="VIC1", pts=[(-37.81, 144.96), (-38.15, 144.36), (-37.56, 143.85)]),
]

SOURCE = dict(
    eia="US EIA Form 930", ieso="IESO public reports", ons="ONS Dados Abertos",
    elexon="Elexon BMRS", echarts="Fraunhofer ISE Energy-Charts", tepco="TEPCO Power Grid", aemo="AEMO",
)


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


def load_sum(r):
    """A country made of several areas: add their hourly demand, and their operator forecasts when every part has one."""
    loads, ops = [], []
    for part in r["parts"]:
        raw, op = globals()[f"load_{part['src']}"](part)
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


def features(load, wx, r, lag, index):
    """Every load input is at least `lag` hours old, so the model only sees what is known at forecast time."""
    full = pd.date_range(min(load.index[0], index[0]), index[-1], freq="h")
    s = load.reindex(full)
    local = index.tz_convert(r["tz"])
    off = holidays.country_holidays(r["cc"], subdiv=r.get("sub"), years=range(2024, index[-1].year + 2))
    week = math.ceil(lag / 168) * 168
    level = s.rolling(168, min_periods=120).mean().shift(lag)
    x = pd.DataFrame(index=index)
    x["hour"], x["dow"], x["doy"] = local.hour, local.day_of_week, local.day_of_year
    x["holiday"] = [d in off for d in local.date]
    w = wx.reindex(full).interpolate(limit=6)
    for c in WX:
        x[c] = w[c].reindex(index)
    t = w.temperature_2m
    x["heat_need"], x["cool_need"] = (18 - x.temperature_2m).clip(lower=0), (x.temperature_2m - 22).clip(lower=0)
    x["temp_24h"], x["temp_72h"] = t.rolling(24).mean().reindex(index), t.rolling(72).mean().reindex(index)
    for name, shift in (("lag_day", lag), ("lag_day2", lag + 24), ("lag_week", week), ("lag_week2", week + 168)):
        x[name] = (s.shift(shift) / level).reindex(index)
    x["level"] = level.reindex(index)
    return x


def fit(x, y):
    # predict the ratio to recent average load, so growth cannot push forecasts past what trees can reach
    m = lgb.LGBMRegressor(n_estimators=500, learning_rate=0.03, num_leaves=31, min_child_samples=40, subsample=0.8,
                          subsample_freq=1, colsample_bytree=0.8, verbose=-1, random_state=0)
    keep = y.notna() & x.level.notna()
    return m.fit(x[keep].drop(columns="level"), np.log(y[keep] / x.level[keep]))


def predict(m, x):
    return x.level.values * np.exp(m.predict(x.drop(columns="level")))


def widen(lo, hi, m):
    """Stretch a band about its middle by a factor m."""
    mid, half = (lo + hi) / 2, (hi - lo) / 2
    return mid - half * m, mid + half * m


def calibrate(err, target=0.8):
    """Band edges (as relative errors) that really hold `target` of the time on data they were not fitted to.
    Time ordered errors are split in thirds: quantiles come from the first, a widening factor from the second,
    and the reported coverage from the last, so the score is not marked on its own homework.
    Returns (low, high, coverage %, widening) where low and high are for the final band, built on all the errors."""
    err = pd.Series(err)
    n = len(err)
    a, b, c = err.iloc[: n // 3], err.iloc[n // 3: 2 * (n // 3)], err.iloc[2 * (n // 3):]
    lo, hi = np.quantile(a, [0.1, 0.9])
    m = 1.0
    while m < 4 and b.between(*widen(lo, hi, m)).mean() < target:
        m *= 1.05
    cover = float(c.between(*widen(lo, hi, m)).mean() * 100)
    lo_f, hi_f = widen(*np.quantile(err, [0.1, 0.9]), m)
    return min(lo_f, 0.0), max(hi_f, 0.0), cover, m  # a band never sits wholly on one side of the forecast


def mape(y, p):
    return float((np.abs(p - y) / y).mean() * 100)


def build(r, now):
    raw, op = globals()[f"load_{r['src']}"](r)
    load = hourly(raw)
    load = load[load.index < now]  # the current hour is still partial
    last = load.index[-1]
    assert now - last < pd.Timedelta(days=30), f"feed stale, last value {last}"
    wx = weather(r, now)
    idx = pd.date_range(now, periods=HORIZON + 1, freq="h")
    hours_ahead = ((idx - last) / pd.Timedelta(hours=1)).to_numpy()
    lag_of_hour = np.ceil(hours_ahead / 24).astype(int) * 24  # nearer hours can use fresher load readings
    lags = sorted(set(lag_of_hour.tolist()))

    # walk-forward backtest, one model per lead time, refit before each 30-day block as an operator would
    y = load
    tests = {}
    for lg in lags:
        x = features(load, wx, r, lg, load.index)
        parts = []
        for k in range(TEST_DAYS // 30, 0, -1):
            a, b = last - pd.Timedelta(days=30 * k), last - pd.Timedelta(days=30 * (k - 1))
            block = (x.index > a) & (x.index <= b) & x.level.notna().values & x.temperature_2m.notna().values
            parts.append(pd.Series(predict(fit(x[x.index <= a], y[x.index <= a]), x[block]), index=x.index[block]))
        tests[lg] = pd.concat(parts)
    # headline score is the first lead time that is a true day-ahead forecast
    lag = next((lg for lg in lags if lg >= 48), lags[-1])
    p = tests[lag]
    x = features(load, wx, r, lag, load.index)
    actual = y[p.index]
    week = math.ceil(lag / 168) * 168
    naive = load.reindex(pd.date_range(load.index[0], last, freq="h")).shift(week).reindex(actual.index)
    has = naive.notna()
    bt = dict(days=TEST_DAYS, hours=len(p), mape=round(mape(actual, p), 2), naive=round(mape(actual[has], naive[has]), 2),
              lead=lag, mae=round(float((p - actual).abs().mean())))
    if op is not None:
        o = op.reindex(actual.index).dropna()
        bt["op"] = round(mape(actual[o.index], o), 2)
        bt["modelOnOpHours"] = round(mape(actual[o.index], p[o.index]), 2)
    # band from the first half of the test window, coverage measured on the second half
    err = (actual - p) / p
    _, _, cover, stretch = calibrate(err)
    bt["coverage"] = round(cover, 1)

    # final models use everything, then forecast forward; the band widens with lead time
    final = fit(x, y)
    f, flo, fhi = np.zeros(len(idx)), np.zeros(len(idx)), np.zeros(len(idx))
    temps = features(load, wx, r, lag, idx).temperature_2m
    assert temps.notna().all(), "weather forecast is missing hours"
    for lg in lags:
        hrs = lag_of_hour == lg
        fx = features(load, wx, r, lg, idx[hrs])
        assert fx.level.notna().all(), "cannot build forecast inputs"
        m = final if lg == lag else fit(features(load, wx, r, lg, load.index), y)
        e = ((y[tests[lg].index] - tests[lg]) / tests[lg]).to_numpy()
        lo, hi = widen(*np.quantile(e, [0.1, 0.9]), stretch)
        lo, hi = min(lo, 0.0), max(hi, 0.0)
        f[hrs] = predict(m, fx)
        flo[hrs], fhi[hrs] = f[hrs] * (1 + lo), f[hrs] * (1 + hi)
    gain = final.booster_.feature_importance("gain")
    names = final.booster_.feature_name()
    group = lambda n: "Recent load" if n.startswith("lag") else "Calendar" if n in ("hour", "dow", "doy", "holiday") else "Weather"
    drivers = {g: round(float(sum(v for n, v in zip(names, gain) if group(n) == g) / gain.sum() * 100), 1) for g in ("Recent load", "Calendar", "Weather")}

    year = load[load.index > last - pd.Timedelta(days=365)]
    hist = load[load.index > now - pd.Timedelta(days=7)].reindex(pd.date_range(now - pd.Timedelta(days=7), now, freq="h"))
    scatter = pd.concat([year.rename("mw"), wx.temperature_2m.rename("t")], axis=1, join="inner").dropna().sample(500, random_state=0)
    recent = pd.date_range(last - pd.Timedelta(days=14), last, freq="h")
    ints = lambda s: [None if pd.isna(v) else round(float(v)) for v in s]
    out = dict(
        id=r["id"], name=r["name"], op=r["op"], source=SOURCE[r["src"]], tz=r["tz"], lat=r["pts"][0][0], lon=r["pts"][0][1],
        lastObs=int(last.timestamp()), peak=round(float(year.quantile(0.99))), backtest=bt, drivers=drivers,
        histStart=int(hist.index[0].timestamp()), hist=ints(hist),
        p50=ints(f), p10=ints(flo), p90=ints(fhi), temp=[round(float(v), 1) for v in temps],
        btStart=int(recent[0].timestamp()), btActual=ints(load.reindex(recent)), btModel=ints(p.reindex(recent)),
        scatter=[[round(float(a), 1), round(float(b))] for a, b in zip(scatter.t, scatter.mw)],
    )
    if op is not None:
        out["opForecast"] = ints(op.reindex(idx))
        out["btOp"] = ints(op.reindex(recent))
    return out


def main():
    now = now_hour()
    out, failed = [], []
    for r in REGIONS:
        try:
            d = build(r, now)
            out.append(d)
            b = d["backtest"]
            print(f"{d['id']:5} {d['name']:18} lead {b['lead']:>3}h  MAPE {b['mape']:5.2f}  naive {b['naive']:5.2f}  operator {b.get('op', '-'):>5}  band {b['coverage']:5.1f}%", flush=True)
        except Exception as e:  # one feed outage should not lose the other regions
            failed.append(r["id"])
            print(f"{r['id']:5} FAILED {type(e).__name__}: {e}", flush=True)
    assert out, "every region failed"
    payload = dict(generated=int(pd.Timestamp.now(tz="UTC").timestamp()), start=int(now.timestamp()), horizon=HORIZON, regions=out)
    (ROOT / "data.js").write_text("window.GRID_DATA=" + json.dumps(payload, separators=(",", ":")) + ";\n")
    print(f"wrote data.js: {len(out)} regions, failed: {failed or 'none'}")


if __name__ == "__main__":
    main()
