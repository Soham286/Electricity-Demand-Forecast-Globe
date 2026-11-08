"""Checks on the generated forecasts (data.js, world.js): everything the page reads is there and sensible."""
import json
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[1] / "web"
REQUIRED = ["id", "name", "kind", "lat", "lon", "tz", "peak", "tests", "p50", "p10", "p90", "temp", "hist", "histStart", "peakStep", "btStart", "btActual",
            "btModel", "scatter", "ringT", "ringV", "drivers", "festivals", "coverage", "coverageOf", "mae", "maeOf", "about", "mainNote", "scoreNote",
            "scatterNote", "btTitle", "btNote"]


def load(name):
    f = WEB / name
    if not f.exists():
        pytest.skip(f"{name} has not been generated")
    return json.loads(f.read_text(encoding="utf-8").split("=", 1)[1].rstrip().rstrip(";"))


def regions():
    out = load("data.js")["regions"]
    return out + (load("world.js")["regions"] if (WEB / "world.js").exists() else [])


def test_every_area_has_what_the_page_reads():
    for r in regions():
        missing = [k for k in REQUIRED if k not in r]
        assert not missing, f"{r['id']} lacks {missing}"


def test_forecasts_are_complete_and_in_order():
    for r in regions():
        n = len(r["p50"])
        assert n == 49, r["id"]
        assert all(len(r[k]) == n for k in ("p10", "p90", "temp", "ringT", "ringV")), r["id"]
        assert all(v is not None for v in r["p50"] + r["p10"] + r["p90"] + r["temp"]), r["id"]
        assert all(lo <= m <= hi for lo, m, hi in zip(r["p10"], r["p50"], r["p90"])), f"{r['id']}: low, expected, high are out of order"
        assert r["peak"] > 0 and len(r["hist"]) == len(r["peakStep"]), r["id"]


def test_ids_are_unique_and_countries_are_known():
    rs = regions()
    ids = [r["id"] for r in rs]
    assert len(ids) == len(set(ids))
    for r in rs:
        if r["kind"] == "country":
            assert r.get("country") and r.get("group") and r.get("cc"), r["id"]


def test_accuracy_numbers_are_believable():
    for r in regions():
        for t in r["tests"]:
            assert 0 < t["model"] < 50 and 0 < t["base"] < 100, (r["id"], t)
        assert 0 <= r["coverage"] <= 100, r["id"]


def test_expected_ranges_are_not_broken_for_the_big_series():
    # The page shows the real coverage next to its 80% goal, so this is only a guard against a broken band
    # (zero width, wrong side of the forecast). Coverage drifts a few points with each day's data; a hard bar
    # near the goal would block the daily publish for noise.
    big = [r for r in regions() if r["id"] in ("IN", "w:USALL", "w:BRALL", "w:AUALL", "w:FR", "w:DE")]
    assert big
    for r in big:
        assert r["coverage"] >= 40, (r["id"], r["coverage"])


def test_whole_country_rows_are_marked():
    rs = {r["id"]: r for r in regions()}
    for tot in ("w:USALL", "w:BRALL", "w:AUALL"):
        if tot in rs:
            assert rs[tot]["total"] is True
