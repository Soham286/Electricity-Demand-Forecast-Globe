import json

import pytest

import world


def test_short_names_drop_the_country():
    assert world.short_name("Queensland (Australia)") == "Queensland"
    assert world.short_name("Brazil Southeast") == "Southeast"
    assert world.short_name("Southwest Power Pool (US)") == "Southwest Power Pool"
    assert world.short_name("France") == "France"


def test_a_whole_country_must_be_built_from_known_areas():
    with pytest.raises(AssertionError):
        world.whole("X", "X", "US", "UTC", "North America", {"NOT AN AREA"}, "all", "about")


def test_saving_tags_each_area_with_its_country_and_keeps_ranges_ordered(tmp_path, monkeypatch):
    monkeypatch.setattr(world, "ROOT", tmp_path)
    one = lambda i, n, lo, hi: dict(id=i, name=n, p10=[lo, lo], p50=[100, 100], p90=[hi, hi])
    out = [one("w:BRSE", "Brazil Southeast", 120, 130), one("w:FR", "France", 90, 95), one("w:TKY", "Tokyo", 80, 120)]
    world.save(out, world.P.now_hour())
    saved = json.loads((tmp_path / "world.js").read_text().split("=", 1)[1].rstrip().rstrip(";"))["regions"]
    by = {x["id"]: x for x in saved}
    assert by["w:BRSE"]["country"] == "Brazil" and by["w:BRSE"]["short"] == "Southeast"
    assert by["w:FR"]["country"] == "France"
    assert by["w:TKY"]["country"] == "Japan" and by["w:TKY"]["name"] == "Tokyo area, Japan"
    # a band that missed the middle is pulled back so the page never draws low above expected
    assert by["w:BRSE"]["p10"] == [100, 100] and by["w:FR"]["p90"] == [100, 100]
