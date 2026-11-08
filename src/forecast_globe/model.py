"""The hourly demand model: features, fitting, walk forward backtest, calibrated ranges, and one area's forecast."""
import math

import holidays
import lightgbm as lgb
import numpy as np
import pandas as pd

from forecast_globe.config import HORIZON, SOURCE, TEST_DAYS, WX
from forecast_globe.feeds import hourly, load_area, weather


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
    raw, op = load_area(r)
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
