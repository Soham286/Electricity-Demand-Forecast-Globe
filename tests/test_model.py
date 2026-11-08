import numpy as np
import pandas as pd

from forecast_globe.config import REGIONS, WX
from forecast_globe.feeds import hourly
from forecast_globe.model import calibrate, features, widen


def sample():
    idx = pd.date_range("2025-01-01", "2025-03-01", freq="h", tz="UTC")
    load = pd.Series(1000 + 100 * np.sin(np.arange(len(idx)) / 24), index=idx)
    wx = pd.DataFrame({c: 10.0 for c in WX}, index=idx)
    return load, wx


def test_no_future_load_leaks_into_features():
    load, wx = sample()
    target = pd.date_range("2025-02-20", periods=49, freq="h", tz="UTC")
    lag = 72
    base = features(load, wx, REGIONS[0], lag, target)
    # anything newer than t - lag must not matter, even for the first target hour
    changed = load.copy()
    changed[changed.index > target[0] - pd.Timedelta(hours=lag)] *= 5
    first = features(changed, wx, REGIONS[0], lag, target[:1])
    pd.testing.assert_frame_equal(base.iloc[:1], first, check_freq=False)


def test_hourly_averages_subhourly_and_drops_glitches():
    idx = pd.date_range("2025-01-01", periods=96, freq="15min", tz="UTC")
    s = pd.Series(100.0, index=idx)
    s.iloc[4:8] = 0.0  # a dead-meter hour
    out = hourly(s)
    assert len(out) == 23 and (out == 100).all()


def test_calibrated_band_covers_the_goal_on_steady_errors_and_never_misses_the_forecast():
    rng = np.random.default_rng(1)
    err = pd.Series(rng.normal(0.01, 0.03, 1800))
    lo, hi, cover, m = calibrate(err)
    assert lo <= 0 <= hi and cover >= 72
    assert widen(-0.1, 0.1, 2) == (-0.2, 0.2)


def test_the_demand_model_can_be_built_and_fitted():
    # lightgbm's scikit-learn interface needs scikit-learn installed; a missing requirement shows up here in seconds
    from forecast_globe.model import fit, predict
    rng = np.random.default_rng(2)
    x = pd.DataFrame({"hour": rng.integers(0, 24, 200), "temperature_2m": rng.normal(20, 5, 200), "level": np.full(200, 100.0)})
    y = pd.Series(100 * (1 + 0.01 * x["hour"]))
    model = fit(x, y)
    assert np.isfinite(predict(model, x)).all()
