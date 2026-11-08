import numpy as np
import pandas as pd

from india import OFF, TZ, daily_features, daily_weather, festival_effect, festival_table, peak_ratio, regional_shape
from pipeline import WX


def test_daily_features_ignore_demand_newer_than_the_lead():
    days = pd.date_range("2025-01-01", "2025-03-01", freq="D")
    e = pd.Series(200000 + 5000 * np.sin(np.arange(len(days))), index=days)
    idx = pd.date_range("2025-01-01", "2025-03-05", freq="h", tz="UTC")
    wd = daily_weather(pd.DataFrame({c: 20.0 for c in WX}, index=idx))
    target = pd.DatetimeIndex([pd.Timestamp("2025-02-20")])
    lead = 3
    changed = e.copy()
    changed[changed.index > target[0] - pd.Timedelta(days=lead)] *= 5
    pd.testing.assert_frame_equal(daily_features(e, wd, lead, target), daily_features(changed, wd, lead, target))


def test_festival_table_learns_dips_only_from_before_the_cut():
    days = pd.date_range("2013-01-01", "2026-11-06", freq="D")
    e = pd.Series(200000.0, index=days)
    diwali = [pd.Timestamp(d) for d, n in OFF.items() if "Diwali" in n and pd.Timestamp(d) in days]
    for d in diwali:
        e[d] *= 0.9
    cut = pd.Timestamp("2024-01-01")
    table = festival_table(e, cut)
    assert abs(table[("Diwali (Deepavali)", 0)] - np.log(0.9)) < 0.01
    # a huge dip after the cut must not change what was learned
    later = e.copy()
    later[pd.Timestamp("2024-10-31")] *= 0.1
    assert festival_table(later, cut) == table
    effect = festival_effect(table, pd.DatetimeIndex([pd.Timestamp("2026-11-08"), pd.Timestamp("2026-07-01")]))
    assert effect.iloc[0] < -0.09 and effect.iloc[1] == 0


def test_regional_shape_keeps_timing_and_hits_the_regions_peak_ratio():
    days = pd.date_range("2025-01-01", "2025-03-31", freq="D")
    e = pd.Series(100.0, index=days)
    assert abs(peak_ratio(e * 1.2, e, pd.Timestamp("2025-04-01")) - 1.2) < 1e-9
    idx = pd.date_range("2025-03-01 00:00", periods=24, freq="h", tz=TZ).tz_convert("UTC")
    shape = pd.Series(1 + 0.1 * np.sin(np.arange(24) / 24 * 2 * np.pi), index=idx)
    out = regional_shape(shape, pd.Series(shape.max(), index=[pd.Timestamp("2025-03-01")]), 1.2)
    assert abs(out.max() - 1.2) < 1e-9 and out.idxmax() == shape.idxmax() and abs(out.mean() - 1) < 1e-9
