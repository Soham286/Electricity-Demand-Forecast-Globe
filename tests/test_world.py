import json

import pytest

from forecast_globe import world


def test_short_names_drop_the_country():
    assert world.short_name("Queensland (Australia)") == "Queensland"
    assert world.short_name("Brazil Southeast") == "Southeast"
    assert world.short_name("Southwest Power Pool (US)") == "Southwest Power Pool"
    assert world.short_name("France") == "France"


def test_a_whole_country_must_be_built_from_known_areas():
    with pytest.raises(AssertionError):
        world.whole("X", "X", "US", "UTC", "North America", {"NOT AN AREA"}, "all", "about")


def test_saving_tags_each_area_with_its_country_and_keeps_ranges_ordered(tmp_path, monkeypatch):
    monkeypatch.setattr(world, "OUT", tmp_path)
    one = lambda i, n, lo, hi: dict(id=i, name=n, p10=[lo, lo], p50=[100, 100], p90=[hi, hi])
    out = [one("w:BRSE", "Brazil Southeast", 120, 130), one("w:FR", "France", 90, 95), one("w:TKY", "Tokyo", 80, 120)]
    world.save(out, world.feeds.now_hour())
    saved = json.loads((tmp_path / "world.js").read_text().split("=", 1)[1].rstrip().rstrip(";"))["regions"]
    by = {x["id"]: x for x in saved}
    assert by["w:BRSE"]["country"] == "Brazil" and by["w:BRSE"]["short"] == "Southeast"
    assert by["w:FR"]["country"] == "France"
    assert by["w:TKY"]["country"] == "Japan" and by["w:TKY"]["name"] == "Tokyo area, Japan"
    # a band that missed the middle is pulled back so the page never draws low above expected
    assert by["w:BRSE"]["p10"] == [100, 100] and by["w:FR"]["p90"] == [100, 100]


def test_shards_cover_every_area_exactly_once():
    n = 6
    got = sorted(r["id"] for k in range(n) for r in world.shard_of(world.config.REGIONS, k, n))
    assert got == sorted(r["id"] for r in world.config.REGIONS)


def test_merging_parts_writes_world_js_and_refuses_an_area_twice(tmp_path, monkeypatch):
    monkeypatch.setattr(world, "OUT", tmp_path)
    mk = lambda i, n, start: dict(id=i, name=n, start=start, p10=[1], p50=[2], p90=[3])
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_text(json.dumps([mk("w:FR", "France", 100)]))
    b.write_text(json.dumps([mk("w:DE", "Germany", 200)]))
    world.merge([a, b])
    saved = json.loads((tmp_path / "world.js").read_text().split("=", 1)[1].rstrip().rstrip(";"))
    assert [x["id"] for x in saved["regions"]] == ["w:FR", "w:DE"] and saved["start"] == 100
    assert saved["regions"][0]["country"] == "France"
    with pytest.raises(AssertionError):
        world.merge([a, a])
