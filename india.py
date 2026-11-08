"""India's 48-hour demand forecast. No free live hourly feed exists, so two models are multiplied:
daily level (fresh daily totals + weather) x hourly shape (2024-25 hourly data + weather)."""
import io, json, math
from pathlib import Path

import holidays
import lightgbm as lgb
import numpy as np
import pandas as pd

from pipeline import WX, calibrate, get, mape, widen, wx_frame

ROOT = Path(__file__).parent
HOURLY = ROOT / "data" / "india_hourly_2024_2025.xlsx"  # https://data.mendeley.com/datasets/y58jknpgs8
DAILY_URL = "https://robbieandrew.github.io/india/data/POSOCO_data.csv"
TZ = "Asia/Kolkata"
START = "2024-01-01"
HORIZON, BLOCKS, TEST_LEAD = 48, 3, 3
# largest demand centres across the five regional grids, for the national hourly shape
POINTS = [(28.61, 77.21), (26.85, 80.95), (26.91, 75.79), (19.08, 72.88), (23.02, 72.57), (13.08, 80.27), (12.97, 77.59), (17.39, 78.49), (22.57, 88.36), (26.14, 91.74)]
GRIDS = {
    "NR": dict(name="Northern", lat=27.5, lon=77.0, states="Delhi, UP, Punjab, Haryana, Rajasthan and the Himalayan states", pts=[(28.61, 77.21), (26.85, 80.95), (26.91, 75.79), (30.73, 76.78)]),
    "WR": dict(name="Western", lat=21.0, lon=75.0, states="Maharashtra, Gujarat, MP, Chhattisgarh and Goa", pts=[(19.08, 72.88), (23.02, 72.57), (21.15, 79.09), (22.72, 75.86)]),
    "SR": dict(name="Southern", lat=13.5, lon=78.0, states="Tamil Nadu, Karnataka, Andhra Pradesh, Telangana and Kerala", pts=[(13.08, 80.27), (12.97, 77.59), (17.39, 78.49), (9.93, 76.27)]),
    "ER": dict(name="Eastern", lat=23.5, lon=86.0, states="West Bengal, Bihar, Odisha, Jharkhand and Sikkim", pts=[(22.57, 88.36), (25.59, 85.14), (20.30, 85.82), (23.34, 85.31)]),
    "NER": dict(name="North-Eastern", lat=25.8, lon=92.5, states="Assam and the seven neighbouring hill states", pts=[(26.14, 91.74), (25.58, 91.89), (23.83, 91.28), (24.82, 93.95)]),
}
# column in the daily file: holiday-calendar code, regional grid, name, main load centre for weather
STATES = {
    "Punjab": ("PB", "NR", "Punjab", (30.90, 75.85)), "Haryana": ("HR", "NR", "Haryana", (28.46, 77.03)),
    "Rajasthan": ("RJ", "NR", "Rajasthan", (26.91, 75.79)), "Delhi": ("DL", "NR", "Delhi", (28.61, 77.21)),
    "UP": ("UP", "NR", "Uttar Pradesh", (26.85, 80.95)), "Uttarakhand": ("UK", "NR", "Uttarakhand", (30.32, 78.03)),
    "HP": ("HP", "NR", "Himachal Pradesh", (31.10, 77.17)), "J&K(UT) & Ladakh(UT)": ("JK", "NR", "J&K and Ladakh", (34.08, 74.80)),
    "Chandigarh": ("CH", "NR", "Chandigarh", (30.73, 76.78)), "Chhattisgarh": ("CG", "WR", "Chhattisgarh", (21.25, 81.63)),
    "Gujarat": ("GJ", "WR", "Gujarat", (23.02, 72.57)), "MP": ("MP", "WR", "Madhya Pradesh", (23.26, 77.41)),
    "Maharashtra": ("MH", "WR", "Maharashtra", (19.08, 72.88)), "Goa": ("GA", "WR", "Goa", (15.49, 73.83)),
    "DNHDDPDCL": ("DH", "WR", "Dadra, Nagar Haveli, Daman and Diu", (20.27, 73.01)), "Andhra Pradesh": ("AP", "SR", "Andhra Pradesh", (16.51, 80.65)),
    "Karnataka": ("KA", "SR", "Karnataka", (12.97, 77.59)), "Kerala": ("KL", "SR", "Kerala", (9.93, 76.27)),
    "Tamil Nadu": ("TN", "SR", "Tamil Nadu", (13.08, 80.27)), "Puducherry": ("PY", "SR", "Puducherry", (11.94, 79.81)),
    "Telangana": ("TS", "SR", "Telangana", (17.39, 78.49)), "Bihar": ("BR", "ER", "Bihar", (25.59, 85.14)),
    "Jharkhand": ("JH", "ER", "Jharkhand", (23.34, 85.31)), "Odisha": ("OD", "ER", "Odisha", (20.30, 85.82)),
    "West Bengal": ("WB", "ER", "West Bengal", (22.57, 88.36)), "Sikkim": ("SK", "ER", "Sikkim", (27.33, 88.61)),
    "Assam": ("AS", "NER", "Assam", (26.14, 91.74)), "Arunachal Pradesh": ("AR", "NER", "Arunachal Pradesh", (27.08, 93.61)),
    "Manipur": ("MN", "NER", "Manipur", (24.82, 93.95)), "Meghalaya": ("ML", "NER", "Meghalaya", (25.58, 91.89)),
    "Mizoram": ("MZ", "NER", "Mizoram", (23.73, 92.72)), "Nagaland": ("NL", "NER", "Nagaland", (25.67, 94.11)),
    "Tripura": ("TR", "NER", "Tripura", (23.83, 91.28)),
}
OFF = holidays.country_holidays("IN", years=range(2013, 2029))
WINDOW = range(-2, 6)  # Diwali's dip starts before the day and lasts days after it


def load_hourly():
    df = pd.read_excel(HOURLY, sheet_name="Report")
    t = pd.to_datetime(df.Timestamp, format="%d-%m-%Y %H:%M:%S", errors="coerce")  # footer rows drop out here
    s = pd.Series(pd.to_numeric(df["Demand (MW)"], errors="coerce").values, index=t)
    s = s[s.index.notna()].dropna().sort_index()
    return s.tz_localize(TZ).tz_convert("UTC")


def festival_table(e, cut, off=OFF):
    """Typical % demand shift by festival and day offset, learned only from before `cut`."""
    log = np.log(e[e.index < cut])
    full = log.reindex(pd.date_range(log.index[0], log.index[-1], freq="D"))
    # normal = median of the same weekday over the four weeks either side
    near = pd.concat([full.shift(7 * k) for k in (-4, -3, -2, -1, 1, 2, 3, 4)], axis=1).median(axis=1)
    shift = full - near
    seen = {}
    for d, names in off.items():
        for n in names.split("; "):
            seen.setdefault(n.replace(" (estimated)", ""), []).append(pd.Timestamp(d))
    table = {}
    for name, dates in seen.items():
        for k in WINDOW:
            vals = shift.reindex([d + pd.Timedelta(days=k) for d in dates]).dropna().tail(8).to_numpy()
            # keep only dips that recur: most years agree and the size clears normal noise;
            # "rises" around festivals were calendar coincidences and made Dussehra worse
            if len(vals) >= 4 and np.median(vals) <= -0.02 and (vals < 0).mean() >= 0.7:
                table[name, k] = float(np.median(vals))
    return table


def festival_effect(table, days, off=OFF):
    """Log demand shift for each day; where festival windows overlap the larger shift wins."""
    out = pd.Series(0.0, index=days)
    for d in days:
        for k in WINDOW:
            for n in off.get((d - pd.Timedelta(days=k)).date(), "").split("; "):
                v = table.get((n.replace(" (estimated)", ""), k), 0.0)
                if abs(v) > abs(out[d]):
                    out[d] = v
    return out


def daily_forecast(e_all, e, wd, lead, cut, days, off=OFF):
    """Train on days before `cut`, with festival shifts taken out so they do not pollute level or lags, then put back."""
    table = festival_table(e_all, cut, off)
    clean = e * np.exp(-festival_effect(table, e.index, off))
    x = daily_features(clean, wd, lead, clean.index, off)
    m = fit_daily(x[x.index < cut], clean[clean.index < cut])
    known = daily_features(clean, wd, lead, days, off)
    return predict_daily(m, known) * np.exp(festival_effect(table, days, off)), m


def load_daily(code):
    energy, peak = f"{code}: EnergyMet", f"{code}: MaximumDemand"
    df = pd.read_csv(io.BytesIO(get("posoco.csv", DAILY_URL, live=True)), usecols=lambda c: c in ("yyyymmdd", energy, peak))
    df.index = pd.to_datetime(df.yyyymmdd.astype(str))
    # EnergyMet is GWh per day; as average MW it matches the hourly file's daily mean (median ratio 1.00)
    energy_mw = (df[energy] * 1000 / 24).where(df[energy] > 0)  # a few zero days are missing reports, not real demand
    return energy_mw, df.get(peak)  # states publish no peak


def weather(points, tag):
    lat, lon = ",".join(str(p[0]) for p in points), ",".join(str(p[1]) for p in points)
    now = pd.Timestamp.now(tz="UTC").floor("h")
    month = now.replace(day=1, hour=0)
    base = dict(latitude=lat, longitude=lon, timezone="UTC")
    prev = ",".join(v + "_previous_day1" for v in WX)
    frames = []
    for name, a, b, live in ((f"wx_{tag}_{month:%Y%m}.json", pd.Timestamp(START), month - pd.Timedelta(days=1), False), (f"wx_{tag}_recent.json", month, now, True)):
        raw = get(name, "https://previous-runs-api.open-meteo.com/v1/forecast", {**base, "start_date": f"{a:%Y-%m-%d}", "end_date": f"{b:%Y-%m-%d}", "hourly": prev}, live=live)
        frames.append(wx_frame(raw, "_previous_day1"))
    issued = pd.concat(frames).dropna()
    # archived forecasts begin mid-January 2024, so reanalysis fills only those first weeks
    early = wx_frame(get(f"wx_{tag}_early.json", "https://archive-api.open-meteo.com/v1/archive", {**base, "start_date": START, "end_date": "2024-02-15", "hourly": ",".join(WX)}), "")
    fc = wx_frame(get(f"wx_{tag}_fc.json", "https://api.open-meteo.com/v1/forecast", {**base, "hourly": ",".join(WX), "past_days": 3, "forecast_days": 5}, live=True), "")
    full = issued.combine_first(fc).combine_first(early).sort_index()
    # Indian hours fall on :30 UTC, weather on :00; without this every hourly weather input would be empty
    half = full.resample("30min").interpolate()
    return half[half.index.minute == 30]


def daily_weather(wx):
    local = wx.tz_convert(TZ).tz_localize(None)
    d = local.resample("D").mean()
    d["t_max"] = local.temperature_2m.resample("D").max()
    d["t_3d"] = d.temperature_2m.rolling(3).mean()
    d["cool_need"], d["heat_need"] = (d.temperature_2m - 24).clip(lower=0), (18 - d.temperature_2m).clip(lower=0)
    return d


def daily_features(e, wd, lead, days, off=OFF):
    """Every demand input is at least `lead` days old."""
    full = pd.date_range(e.index[0], max(e.index[-1], days[-1]), freq="D")
    s = e.reindex(full)
    level = s.rolling(7, min_periods=5).mean().shift(lead)
    x = pd.DataFrame(index=days)
    x["dow"], x["doy"] = days.day_of_week, days.day_of_year
    x["holiday"] = [d in off for d in days.date]
    x["next_off"] = [(d + pd.Timedelta(days=1)).date() in off for d in days]
    x["prev_off"] = [(d - pd.Timedelta(days=1)).date() in off for d in days]
    x = x.join(wd.reindex(days))
    for name, k in (("lag1", lead), ("lag2", lead + 1), ("lag_week", 7 * math.ceil(lead / 7))):
        x[name] = (s.shift(k) / level).reindex(days)
    x["level"] = level.reindex(days)
    return x


def shape_features(wx, wd, idx):
    local = idx.tz_convert(TZ)
    days = local.tz_localize(None).normalize()
    x = pd.DataFrame(index=idx)
    x["hour"], x["dow"], x["doy"] = local.hour, local.day_of_week, local.day_of_year
    x["holiday"] = [d in OFF for d in local.date]
    x = x.join(wx.reindex(idx))
    x["day_temp"] = wd.temperature_2m.reindex(days).values
    x["temp_24h"] = wx.temperature_2m.rolling(24).mean().reindex(idx)
    x["cool_need"] = (x.temperature_2m - 24).clip(lower=0)
    return x


def fit_daily(x, y):
    keep = y.notna() & x.level.notna()
    m = lgb.LGBMRegressor(n_estimators=300, learning_rate=0.03, num_leaves=7, min_child_samples=10, subsample=0.8, subsample_freq=1, colsample_bytree=0.8, verbose=-1, random_state=0)
    return m.fit(x[keep].drop(columns="level"), np.log(y[keep] / x.level[keep]))


def predict_daily(m, x):
    return pd.Series(x.level.values * np.exp(m.predict(x.drop(columns="level"))), index=x.index)


def fit_shape(h, wx, wd):
    days = h.index.tz_convert(TZ).tz_localize(None).normalize()
    mean, count = h.groupby(days).transform("mean"), h.groupby(days).transform("count")
    keep = count.values == 24
    m = lgb.LGBMRegressor(n_estimators=500, learning_rate=0.03, num_leaves=31, min_child_samples=40, subsample=0.8, subsample_freq=1, colsample_bytree=0.8, verbose=-1, random_state=0)
    return m.fit(shape_features(wx, wd, h.index[keep]), np.log(h[keep] / mean[keep]))


def shape_of(m, wx, wd, days):
    """Hourly multipliers for whole days, forced to average 1 so the shape cannot change the daily level."""
    idx = pd.date_range(days[0], days[-1] + pd.Timedelta(hours=23), freq="h", tz=TZ).tz_convert("UTC")
    idx = idx[idx.tz_convert(TZ).tz_localize(None).normalize().isin(days)]
    r = pd.Series(np.exp(m.predict(shape_features(wx, wd, idx))), index=idx)
    return r / r.groupby(idx.tz_convert(TZ).tz_localize(None).normalize()).transform("mean")


def combine(level, shape):
    return shape * level.reindex(shape.index.tz_convert(TZ).tz_localize(None).normalize()).values


def blocks(end):
    return [(end - pd.Timedelta(days=30 * k), end - pd.Timedelta(days=30 * (k - 1))) for k in range(BLOCKS, 0, -1)]


def group_gain(m, group):
    names, gain = m.booster_.feature_name(), m.booster_.feature_importance("gain")
    out = {}
    for n, v in zip(names, gain):
        out[group(n)] = out.get(group(n), 0) + v
    return {k: round(float(v / gain.sum() * 100), 1) for k, v in out.items()}


def peak_ratio(peak, e, cut):
    """A region's usual peak over its daily average, from the 60 days before `cut`, since sharpness shifts by season."""
    days = e.index[(e.index < cut) & (e.index >= cut - pd.Timedelta(days=60))]
    return float((peak / e).reindex(days).median())


def regional_shape(shape, day_peak, ratio):
    """National hourly timing, rescaled each day so the day's peak lands on the region's own ratio."""
    days = local_day(shape.index)
    return 1 + (ratio - 1) * (shape - 1) / (day_peak.reindex(days).values - 1)


SUPPLY_FIELDS = ["Coal", "Lignite", "Gas", "Nuclear", "Hydro", "RES", "SolarGen", "WindGen", "Total", "EnergyMet", "EnergyShortage"]


def load_supply(code):
    """Daily generation by fuel, consumption and unmet demand, all in GWh (million units). Grids and India only."""
    want = {f"{code}: {k}" for k in SUPPLY_FIELDS}
    df = pd.read_csv(io.BytesIO(get("posoco.csv", DAILY_URL, live=True)), usecols=lambda c: c == "yyyymmdd" or c in want)
    df.index = pd.to_datetime(df.yyyymmdd.astype(str))
    return df.drop(columns="yyyymmdd").rename(columns=lambda c: c.split(": ", 1)[1]).fillna(0)


def supply_payload(df, gross_ratio, last_day, days=90):
    """Last `days` of supply for the page, plus 30-day shares and a rough import/export position."""
    d = df[(df.index > last_day - pd.Timedelta(days=days)) & (df.index <= last_day)]
    fuels = {
        "Nuclear": d.Nuclear, "Coal, lignite and gas": d.Coal + d.Lignite + d.Gas, "Hydro": d.Hydro,
        "Wind": d.WindGen, "Solar": d.SolarGen, "Other renewables": (d.RES - d.SolarGen - d.WindGen).clip(lower=0),
    }
    parts = sum(fuels.values())
    assert ((parts - d.Total).abs() <= 0.02 * d.Total + 1).mean() > 0.95, "fuels do not add up to total generation"
    last30 = d.index > last_day - pd.Timedelta(days=30)
    total30 = d.Total[last30].sum()
    # generation is measured before plants' own use and losses, consumption after; scaling by the national
    # ratio makes them comparable, so the import/export figure is approximate
    net = float(((d.Total / gross_ratio.reindex(d.index)) - d.EnergyMet)[last30].sum() / d.EnergyMet[last30].sum() * 100)
    return dict(
        start=int(d.index[0].tz_localize(TZ).timestamp()), fuels={k: [round(float(v), 1) for v in s] for k, s in fuels.items()},
        used=[round(float(v), 1) for v in d.EnergyMet], short=[round(float(v), 2) for v in d.EnergyShortage],
        share={k: round(float(s[last30].sum() / total30 * 100), 1) for k, s in fuels.items()},
        lastGen=round(float(d.Total.iloc[-1]), 1), net=round(net, 1), grossGap=round(float((d.Total[last30].sum() / d.EnergyMet[last30].sum() - 1) * 100), 1),
    )


def festival_labels(table, days, off=OFF):
    ahead, out = festival_effect(table, days, off), []
    for d, v in ahead[ahead != 0].items():
        hit = next((n, k) for k in sorted(WINDOW, key=abs) for n in off.get((d - pd.Timedelta(days=k)).date(), "").split("; ") if table.get((n.replace(" (estimated)", ""), k)) == v)
        out.append(dict(day=f"{d:%a %d %b}", name=hit[0].split(" (")[0], offset=hit[1], pct=round(float(np.exp(v) - 1) * 100, 1)))
    return out


def ints(s):
    return [None if pd.isna(v) else round(float(v)) for v in s]


def local_day(idx):
    return idx.tz_convert(TZ).tz_localize(None).normalize()


def main():
    now = pd.Timestamp.now(tz=TZ).floor("h").tz_convert("UTC")
    h = load_hourly()
    nat_all, nat_peak_all = load_daily("India")
    nat, nat_peak = nat_all[nat_all.index >= START], nat_peak_all[nat_peak_all.index >= START]
    wx = weather(POINTS, "in")
    wd = daily_weather(wx)
    last_day = nat.index[-1]
    assert wx.reindex(h.index).temperature_2m.notna().mean() > 0.99, "weather does not line up with demand hours"
    grids = {}
    for code, g in GRIDS.items():
        e_all, peak_all = load_daily(code)
        wr = weather(g["pts"], code)
        grids[code] = dict(g, e_all=e_all, e=e_all[e_all.index >= START], peak=peak_all[peak_all.index >= START], wx=wr, wd=daily_weather(wr))

    def forecast_sum(lead, cut, days):
        return sum(daily_forecast(g["e_all"], g["e"], g["wd"], lead, cut, days)[0] for g in grids.values())

    # national hour-by-hour test, the only hourly truth available; the level is the sum of the regions
    parts, shape_parts = [], []
    day_mean = h.groupby(local_day(h.index)).mean()
    for a, b in blocks(h.index[-1]):
        cut = a.tz_convert(TZ).tz_localize(None).normalize()
        days = pd.date_range(cut, b.tz_convert(TZ).tz_localize(None).normalize(), freq="D")
        s = shape_of(fit_shape(h[h.index < cut.tz_localize(TZ).tz_convert("UTC")], wx, wd), wx, wd, days)
        parts.append(combine(forecast_sum(TEST_LEAD, cut, days), s))
        shape_parts.append(combine(day_mean, s))
    p = pd.concat(parts)
    p = p[~p.index.duplicated(keep="last")]
    actual = h.reindex(p.index).dropna()
    p = p[actual.index]
    shape_only = pd.concat(shape_parts)
    shape_only = shape_only[~shape_only.index.duplicated(keep="last")][actual.index]
    naive = h.reindex(pd.date_range(h.index[0], h.index[-1], freq="h")).shift(168).reindex(actual.index)
    err = (actual - p) / p
    lo, hi, coverage, _ = calibrate(err)
    shape_lo, shape_hi = np.quantile((actual - shape_only) / shape_only, [0.1, 0.9])

    # latest 90 days, checked against each region's reported daily totals and peaks
    for g in grids.values():
        g["recent"] = pd.concat([daily_forecast(g["e_all"], g["e"], g["wd"], TEST_LEAD, a + pd.Timedelta(days=1), g["e"].index[(g["e"].index > a) & (g["e"].index <= b)])[0] for a, b in blocks(last_day)])
    recent = sum(g["recent"] for g in grids.values())
    shape_model = fit_shape(h, wx, wd)  # hourly data stops June 2025, so this whole window is unseen

    idx = pd.date_range(now, periods=HORIZON + 1, freq="h")
    past = pd.date_range(now - pd.Timedelta(days=7), now - pd.Timedelta(hours=1), freq="h")
    local_days = pd.DatetimeIndex(local_day(past.append(idx)).unique())
    all_days = pd.date_range(START, local_days[-1], freq="D")
    all_shape = shape_of(shape_model, wx, wd, all_days)
    day_peak = all_shape.groupby(local_day(all_shape.index)).max()
    shape = all_shape[local_day(all_shape.index).isin(local_days)]

    when = f"{recent.index[0]:%d %b} to {recent.index[-1]:%d %b %Y}"
    forward_days = pd.DatetimeIndex(local_day(idx).unique())
    past_days, hourly_note = local_day(past), f"{h.index[-1].tz_convert(TZ):%d %b %Y}"
    common = dict(op="Grid-India", source="Grid-India via Mendeley and Robbie Andrew", tz=TZ, lead=TEST_LEAD, lastDay=f"{last_day:%d %b %Y}", lastHour=hourly_note, histStart=int(past[0].timestamp()))
    shape_gain = group_gain(shape_model, lambda n: "Calendar" if n in ("hour", "dow", "doy", "holiday") else "Weather")
    level_group = lambda n: "Recent demand" if n.startswith("lag") else "Calendar" if n in ("dow", "doy", "holiday", "next_off", "prev_off") else "Weather"

    def stepped(values):
        steps = pd.Series(values.reindex(past_days).values, index=past)
        steps[past.tz_convert(TZ).hour == 23] = np.nan  # a gap between days, so each day reads as its own bar
        return ints(steps)

    out, level_sum, gains = [], 0, []
    for code, g in grids.items():
        e, peak = g["e"], g["peak"]
        level = e.reindex(local_days)
        gain = None
        for d in local_days[local_days > last_day]:
            pred, m = daily_forecast(g["e_all"], e, g["wd"], (d - last_day).days, last_day + pd.Timedelta(days=1), pd.DatetimeIndex([d]))
            level[d] = pred.iloc[0]
            gain = gain or group_gain(m, level_group)
        assert level.notna().all(), f"{code}: missing a daily level"
        level_sum = level_sum + level.where(local_days > last_day, 0)
        gains.append(gain)
        rec = g["recent"]
        peak_p = rec * peak_ratio(peak, e, rec.index[0])
        # no regional hourly truth, so the band combines this region's daily error with the national shape error
        daily_err = (e[rec.index] - rec) / rec
        n3 = len(daily_err) // 3
        _, _, _, m = calibrate(daily_err)
        band = lambda lo_, hi_: (-math.hypot(lo_, shape_lo), math.hypot(hi_, shape_hi))
        c_lo, c_hi = band(*widen(*np.quantile(daily_err[:n3], [0.1, 0.9]), m))
        peak_err = ((peak[rec.index] - peak_p) / peak_p)[2 * n3:]
        cover = float(peak_err.between(c_lo, c_hi).mean() * 100)
        f_lo, f_hi = band(*widen(*np.quantile(daily_err, [0.1, 0.9]), m))
        g["ratio"] = peak_ratio(peak, e, last_day + pd.Timedelta(days=1))
        est = combine(level, regional_shape(shape, day_peak, g["ratio"]))
        f = est.reindex(idx)
        temps = g["wx"].temperature_2m.reindex(idx)
        assert f.notna().all() and temps.notna().all(), f"{code}: forecast has missing hours"
        pairs = pd.concat([e.rename("mw"), g["wd"].temperature_2m.rename("t")], axis=1, join="inner").dropna().tail(365)
        bt_idx = pd.date_range(max(rec.index[0], last_day - pd.Timedelta(days=13)), last_day + pd.Timedelta(hours=23), freq="h", tz=TZ).tz_convert("UTC")
        bt_days = local_day(bt_idx)
        out.append(dict(
            common, id=code, kind="grid", name=g["name"], lat=g["lat"], lon=g["lon"],
            about=f"Grid-India {g['name']} grid: {g['states']}",
            lastObs=int(last_day.tz_localize(TZ).timestamp()) + 86400, peak=round(float(peak[peak.index > last_day - pd.Timedelta(days=365)].max())),
            tests=[dict(title="Daily energy", when=when, model=round(mape(e[rec.index], rec), 2), base=round(mape(e[rec.index], e.shift(7)[rec.index]), 2)),
                   dict(title="Daily peak", when=when, model=round(mape(peak[rec.index], peak_p), 2), base=round(mape(peak[rec.index], peak.shift(7)[rec.index]), 2))],
            coverage=round(cover, 1), coverageOf="reported daily peaks in the last 30 days", mae=round(float((peak_p - peak[rec.index]).abs().mean())), maeOf="daily peak",
            festivals=festival_labels(festival_table(g["e_all"], last_day + pd.Timedelta(days=1)), forward_days),
            drivers={"Daily level": gain, "Hourly shape (national)": shape_gain},
            hist=ints(est.reindex(past)), peakStep=stepped(peak), p50=ints(f), p10=ints(f * (1 + f_lo)), p90=ints(f * (1 + f_hi)), temp=[round(float(v), 1) for v in temps],
            btStart=int(bt_idx[0].timestamp()), btActual=ints(pd.Series(e.reindex(bt_days).values)), btModel=ints(pd.Series(rec.reindex(bt_days).values)),
            scatter=[[round(float(a), 1), round(float(b))] for a, b in zip(pairs.t, pairs.mw)],
            ringT=[round(float(v), 1) for v in g["wd"].temperature_2m.reindex(local_day(idx)).values], ringV=ints(pd.Series(level.reindex(local_day(idx)).values)),
            mainNote="No regional hourly data exists for free, so hours follow the national pattern, rescaled to this grid's usual ratio of peak to average demand. Its accuracy can only be checked on daily totals and peaks, and at busy hours the regions add up to a little more than the All India figure, since every grid is modelled peaking together. The steps are real reported peaks. The axis does not start at zero. Click the forecast to move the timeline.",
            scoreNote=f"Average error, each test made {TEST_LEAD} days ahead with models trained only on earlier data. Hourly accuracy cannot be measured for a single region. Lower is better.",
            scatterNote="Daily average demand against average temperature, past year. The ring is the selected day.",
            btTitle="Backtest, last 14 days of daily averages", btNote="Model output against reported demand, one level per day.",
        ))

    def state_entry(col, sub, grid, name, point):
        off = holidays.country_holidays("IN", subdiv=sub, years=range(2013, 2029))
        e_all = load_daily(col)[0]
        e = e_all[e_all.index >= START]
        wr = weather([point], f"st_{sub}")
        wds = daily_weather(wr)
        rec = pd.concat([daily_forecast(e_all, e, wds, TEST_LEAD, a + pd.Timedelta(days=1), e.index[(e.index > a) & (e.index <= b)], off)[0] for a, b in blocks(last_day)])
        daily_err = (e[rec.index] - rec) / rec
        _, _, cover, m = calibrate(daily_err)
        q_lo, q_hi = widen(*np.quantile(daily_err, [0.1, 0.9]), m)
        f_lo, f_hi = -math.hypot(q_lo, shape_lo), math.hypot(q_hi, shape_hi)
        level, gain = e.reindex(local_days), None
        for d in local_days[local_days > last_day]:
            pred, m = daily_forecast(e_all, e, wds, (d - last_day).days, last_day + pd.Timedelta(days=1), pd.DatetimeIndex([d]), off)
            level[d] = pred.iloc[0]
            gain = gain or group_gain(m, level_group)
        assert level.notna().all(), f"{name}: missing a daily level"
        g = grids[grid]
        est = combine(level, regional_shape(shape, day_peak, g["ratio"]))
        f, temps = est.reindex(idx), wr.temperature_2m.reindex(idx)
        assert f.notna().all() and temps.notna().all(), f"{name}: forecast has missing hours"
        pairs = pd.concat([e.rename("mw"), wds.temperature_2m.rename("t")], axis=1, join="inner").dropna().tail(365)
        bt_idx = pd.date_range(max(rec.index[0], last_day - pd.Timedelta(days=13)), last_day + pd.Timedelta(hours=23), freq="h", tz=TZ).tz_convert("UTC")
        bt_days = local_day(bt_idx)
        return dict(
            common, id=sub, kind="state", grid=grid, name=name, lat=point[0], lon=point[1], about=f"State in the {g['name']} grid",
            lastObs=int(last_day.tz_localize(TZ).timestamp()) + 86400,
            peak=round(float(e[e.index > last_day - pd.Timedelta(days=365)].max() * g["ratio"])), peakLabel="of estimated yearly peak",
            tests=[dict(title="Daily energy", when=when, model=round(mape(e[rec.index], rec), 2), base=round(mape(e[rec.index], e.shift(7)[rec.index]), 2))],
            coverage=round(cover, 1), coverageOf="daily totals in the last 30 days", mae=round(float((rec - e[rec.index]).abs().mean())), maeOf="daily average",
            festivals=festival_labels(festival_table(e_all, last_day + pd.Timedelta(days=1), off), forward_days, off),
            drivers={"Daily level": gain, "Hourly shape (national)": shape_gain},
            hist=ints(est.reindex(past)), peakStep=[None] * len(past), p50=ints(f), p10=ints(f * (1 + f_lo)), p90=ints(f * (1 + f_hi)), temp=[round(float(v), 1) for v in temps],
            btStart=int(bt_idx[0].timestamp()), btActual=ints(pd.Series(e.reindex(bt_days).values)), btModel=ints(pd.Series(rec.reindex(bt_days).values)),
            scatter=[[round(float(a), 1), round(float(b))] for a, b in zip(pairs.t, pairs.mw)],
            ringT=[round(float(v), 1) for v in wds.temperature_2m.reindex(local_day(idx)).values], ringV=ints(pd.Series(level.reindex(local_day(idx)).values)),
            mainNote=f"States publish daily totals only, with no hourly or peak data, so hours follow the national pattern rescaled to the {g['name']} grid's peak ratio, and past hours are estimates. Accuracy can only be checked on daily totals. The axis does not start at zero. Click the forecast to move the timeline.",
            scoreNote=f"Average error on daily totals, each made {TEST_LEAD} days ahead with models trained only on earlier data. State festivals from the {name} holiday calendar are included. Lower is better.",
            scatterNote="Daily average demand against temperature at the main load centre, past year. The ring is the selected day.",
            btTitle="Backtest, last 14 days of daily averages", btNote="Model output against reported demand, one level per day.",
        )

    for col, (sub, grid, name, point) in STATES.items():
        try:
            out.append(state_entry(col, sub, grid, name, point))
        except Exception as ex:  # one thin state series should not lose the rest
            print(f"{name} FAILED {type(ex).__name__}: {ex}", flush=True)

    level = nat.reindex(local_days)
    level[local_days > last_day] = level_sum[local_days > last_day]
    assert level.notna().all(), "missing a national daily level"
    est = combine(level, shape)
    f = est.reindex(idx)
    temps = wx.temperature_2m.reindex(idx)
    assert f.notna().all() and temps.notna().all(), "national forecast has missing hours"
    peak_p = recent * day_peak[recent.index]
    both = pd.concat([h.rename("mw"), wx.temperature_2m.rename("t")], axis=1, join="inner").dropna().sample(500, random_state=0)
    bt_idx = pd.date_range(h.index[-1] - pd.Timedelta(days=14), h.index[-1], freq="h")
    avg_gain = {k: round(sum(g[k] for g in gains) / len(gains), 1) for k in gains[0]}
    out.insert(0, dict(
        common, id="IN", kind="nation", name="All India", lat=22.5, lon=79.0, about="Grid-India national grid, the sum of the five regional grids",
        lastObs=int(last_day.tz_localize(TZ).timestamp()) + 86400, peak=round(float(nat_peak[nat_peak.index > last_day - pd.Timedelta(days=365)].max())),
        tests=[dict(title="Every hour", when=f"{p.index[0]:%d %b} to {p.index[-1]:%d %b %Y}", model=round(mape(actual, p), 2), base=round(mape(actual[naive.notna()], naive.dropna()), 2)),
               dict(title="Daily energy", when=when, model=round(mape(nat[recent.index], recent), 2), base=round(mape(nat[recent.index], nat.shift(7)[recent.index]), 2)),
               dict(title="Daily peak", when=when, model=round(mape(nat_peak[recent.index], peak_p), 2), base=round(mape(nat_peak[recent.index], nat_peak.shift(7)[recent.index]), 2))],
        coverage=round(coverage, 1), coverageOf="actual hours in the hourly test", mae=round(float((p - actual).abs().mean())), maeOf="hourly",
        festivals=festival_labels(festival_table(nat_all, last_day + pd.Timedelta(days=1)), forward_days),
        drivers={"Daily level (average of regions)": avg_gain, "Hourly shape": shape_gain},
        hist=ints(est.reindex(past)), peakStep=stepped(nat_peak), p50=ints(f), p10=ints(f * (1 + lo)), p90=ints(f * (1 + hi)), temp=[round(float(v), 1) for v in temps],
        btStart=int(bt_idx[0].timestamp()), btActual=ints(h.reindex(bt_idx)), btModel=ints(p.reindex(bt_idx)),
        scatter=[[round(float(a), 1), round(float(b))] for a, b in zip(both.t, both.mw)], ringT=[round(float(v), 1) for v in temps], ringV=ints(f),
        mainNote="No free live hourly feed exists for India, so past hours are estimates built from each day's reported total. The steps are real reported peaks. The axis does not start at zero. Click the forecast to move the timeline.",
        scoreNote=f"Average error, each test made {TEST_LEAD} days ahead with models trained only on earlier data. Hourly accuracy can only be measured up to {hourly_note}. Lower is better.",
        scatterNote=f"Hourly samples, Jan 2024 to {hourly_note}. The ring is the selected hour.",
        btTitle="Backtest, final 14 days of hourly data", btNote=f"Model output against what the grid then used, to {hourly_note}.",
    ))
    india_supply = load_supply("India")
    gross_ratio = india_supply.Total / india_supply.EnergyMet
    for r in out:
        if r["kind"] == "state":
            r["supplyFrom"] = r["grid"]  # states publish no generation
        else:
            r["supply"] = supply_payload(india_supply if r["id"] == "IN" else load_supply(r["id"]), gross_ratio, last_day)
    for r in out:  # low never above expected, high never below
        r["p10"] = [min(a, b) for a, b in zip(r["p10"], r["p50"])]
        r["p90"] = [max(a, b) for a, b in zip(r["p90"], r["p50"])]
    payload = dict(generated=int(pd.Timestamp.now(tz="UTC").timestamp()), start=int(now.timestamp()), horizon=HORIZON, regions=out)
    (ROOT / "data.js").write_text("window.GRID_DATA=" + json.dumps(payload, separators=(",", ":")) + ";\n")
    print(f"{sum(r['kind'] == 'state' for r in out)} of {len(STATES)} states built")
    for r in out:
        if "supply" in r:
            s = r["supply"]
            print(f"{r['id']:4} supply: last day {s['lastGen']} GWh, 30-day shares {s['share']}, net {s['net']:+.1f}% vs use")
    for r in out:
        t = " | ".join(f"{x['title']} {x['model']:.2f} vs {x['base']:.2f}" for x in r["tests"])
        print(f"{r['id']:4} {t}  band {r['coverage']}% of {r['coverageOf']}")
        print(f"     now {r['p50'][0]} MW, 48h peak {max(r['p50'])} MW, yearly peak {r['peak']} MW, festivals {[(x['day'], x['pct']) for x in r['festivals']]}")


if __name__ == "__main__":
    main()
