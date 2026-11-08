"""World forecasts: every country or grid with a free hourly demand feed, 48 hours ahead.
Uses the shared models in model.py and feeds.py and writes world.js for index.html. India has its own pipeline (india.py)."""
import json
from pathlib import Path
import sys
import time

import pandas as pd

from forecast_globe import config, feeds, model

OUT = config.WEB  # world.js is written next to the page
COUNTRY = {"US": "United States", "CA": "Canada", "BR": "Brazil", "AU": "Australia", "JP": "Japan"}  # areas of these roll up into one country
CONTINENT = {"US": "North America", "CA": "North America", "BR": "South America", "JP": "Asia", "AU": "Oceania", "ZA": "Africa"}


def eu(code, name, op, cc, tz, *pts):
    return dict(id=code.upper(), name=name, op=op, cc=cc, tz=tz, src="echarts", code=code, pts=list(pts), group="Europe")


# more Energy-Charts countries (ENTSO-E load); a country whose feed is missing or stale is skipped at run time
EXTRA = [
    eu("at", "Austria", "APG", "AT", "Europe/Vienna", (48.21, 16.37), (47.07, 15.44)),
    eu("be", "Belgium", "Elia", "BE", "Europe/Brussels", (50.85, 4.35), (51.22, 4.40)),
    eu("bg", "Bulgaria", "ESO", "BG", "Europe/Sofia", (42.70, 23.32), (42.14, 24.75)),
    eu("ch", "Switzerland", "Swissgrid", "CH", "Europe/Zurich", (47.38, 8.54), (46.20, 6.14)),
    eu("cz", "Czechia", "CEPS", "CZ", "Europe/Prague", (50.08, 14.44), (49.19, 16.61)),
    eu("dk", "Denmark", "Energinet", "DK", "Europe/Copenhagen", (55.68, 12.57), (56.16, 10.20)),
    eu("ee", "Estonia", "Elering", "EE", "Europe/Tallinn", (59.44, 24.75), (58.38, 26.72)),
    eu("fi", "Finland", "Fingrid", "FI", "Europe/Helsinki", (60.17, 24.94), (61.50, 23.76)),
    eu("gr", "Greece", "IPTO", "GR", "Europe/Athens", (37.98, 23.73), (40.64, 22.94)),
    eu("hr", "Croatia", "HOPS", "HR", "Europe/Zagreb", (45.81, 15.98), (43.51, 16.44)),
    eu("hu", "Hungary", "MAVIR", "HU", "Europe/Budapest", (47.50, 19.04), (47.53, 21.63)),
    eu("ie", "Ireland", "EirGrid", "IE", "Europe/Dublin", (53.35, -6.26), (51.90, -8.47)),
    eu("lt", "Lithuania", "Litgrid", "LT", "Europe/Vilnius", (54.69, 25.28), (54.90, 23.90)),
    eu("lu", "Luxembourg", "Creos", "LU", "Europe/Luxembourg", (49.61, 6.13)),
    eu("lv", "Latvia", "AST", "LV", "Europe/Riga", (56.95, 24.11)),
    eu("me", "Montenegro", "CGES", "ME", "Europe/Podgorica", (42.44, 19.26)),
    eu("mk", "North Macedonia", "MEPSO", "MK", "Europe/Skopje", (42.00, 21.43)),
    eu("nl", "Netherlands", "TenneT", "NL", "Europe/Amsterdam", (52.37, 4.90), (51.92, 4.48)),
    eu("no", "Norway", "Statnett", "NO", "Europe/Oslo", (59.91, 10.75), (60.39, 5.32)),
    eu("pt", "Portugal", "REN", "PT", "Europe/Lisbon", (38.72, -9.14), (41.15, -8.61)),
    eu("ro", "Romania", "Transelectrica", "RO", "Europe/Bucharest", (44.43, 26.10), (46.77, 23.59)),
    eu("rs", "Serbia", "EMS", "RS", "Europe/Belgrade", (44.79, 20.45)),
    eu("si", "Slovenia", "ELES", "SI", "Europe/Ljubljana", (46.06, 14.51)),
    eu("sk", "Slovakia", "SEPS", "SK", "Europe/Bratislava", (48.15, 17.11), (48.72, 21.26)),
    eu("ba", "Bosnia and Herzegovina", "NOS BiH", "BA", "Europe/Sarajevo", (43.86, 18.41)),
    eu("cy", "Cyprus", "TSO Cyprus", "CY", "Asia/Nicosia", (35.19, 33.38)),
    eu("xk", "Kosovo", "KOSTT", "XK", "Europe/Belgrade", (42.67, 21.17)),
    dict(eu("ge", "Georgia", "GSE", "GE", "Asia/Tbilisi", (41.72, 44.79)), group="Asia"),
    eu("md", "Moldova", "Moldelectrica", "MD", "Europe/Chisinau", (47.01, 28.86)),
    dict(id="SWPP", name="Southwest Power Pool (US)", op="SPP", cc="US", tz="America/Chicago", src="eia", code="SWPP", pts=[(35.47, -97.52), (39.10, -94.58), (41.26, -95.93)]),
    dict(id="SOCO", name="Southern Company (US)", op="Southern Co.", cc="US", tz="America/New_York", src="eia", code="SOCO", pts=[(33.75, -84.39), (33.52, -86.80)]),
    dict(id="TVA", name="Tennessee Valley (US)", op="TVA", cc="US", tz="America/Chicago", src="eia", code="TVA", pts=[(36.16, -86.78), (35.15, -90.05)]),
    dict(id="FPL", name="Florida (US)", op="FPL", cc="US", tz="America/New_York", src="eia", code="FPL", pts=[(25.76, -80.19), (26.71, -80.05)]),
    dict(id="BPAT", name="Pacific Northwest (US)", op="Bonneville", cc="US", tz="America/Los_Angeles", src="eia", code="BPAT", pts=[(45.52, -122.68), (47.61, -122.33)]),
    dict(id="BRS", name="Brazil South", op="ONS", cc="BR", tz="America/Sao_Paulo", src="ons", code="S", pts=[(-30.03, -51.23), (-25.43, -49.27)]),
    dict(id="BRN", name="Brazil North", op="ONS", cc="BR", tz="America/Manaus", src="ons", code="N", pts=[(-3.12, -60.02), (-1.46, -48.50)]),
    dict(id="QLD", name="Queensland (Australia)", op="AEMO", cc="AU", sub="QLD", tz="Australia/Brisbane", src="aemo", code="QLD1", pts=[(-27.47, 153.03), (-23.38, 150.51)]),
    dict(id="SA", name="South Australia", op="AEMO", cc="AU", sub="SA", tz="Australia/Adelaide", src="aemo", code="SA1", pts=[(-34.93, 138.60)]),
    dict(id="TAS", name="Tasmania", op="AEMO", cc="AU", sub="TAS", tz="Australia/Hobart", src="aemo", code="TAS1", pts=[(-42.88, 147.33)]),
]
config.REGIONS.extend(EXTRA)  # so the US feed loader fetches every balancing authority in one pass
for r in config.REGIONS:
    r.setdefault("group", CONTINENT.get(r["cc"], "Europe"))


def whole(id, name, cc, tz, group, part_ids, short, about):
    """A whole country made by adding its areas together, then modelled like any other area."""
    parts = [r for r in config.REGIONS if r["id"] in part_ids]
    assert len(parts) == len(part_ids), f"{name}: unknown part"
    return dict(id=id, name=name, op="Added up from its regions", cc=cc, tz=tz, src="sum", parts=parts, group=group, total=True, short=short, about=about,
                pts=[p["pts"][0] for p in parts])


config.SOURCE["sum"] = "the regions listed below, added together"
config.REGIONS.extend([
    whole("USALL", "United States", "US", "America/Chicago", "North America", {"CISO", "ERCO", "PJM", "NYIS", "ISNE", "MISO", "SWPP", "SOCO", "TVA", "FPL", "BPAT"}, "All 11 grids",
          "The 11 US grids listed below, added together. It covers much of the country but not every grid."),
    whole("BRALL", "Brazil", "BR", "America/Sao_Paulo", "South America", {"BRSE", "BRNE", "BRS", "BRN"}, "Whole country",
          "All four of Brazil's grid zones, added together."),
    whole("AUALL", "Australia", "AU", "Australia/Sydney", "Oceania", {"NSW", "VIC", "QLD", "SA", "TAS"}, "Main grid",
          "The five states on Australia's main grid, added together. Western Australia and the Northern Territory are separate grids and are not included."),
])
NAME_FIX = {"TKY": "Tokyo area, Japan", "ONT": "Ontario, Canada"}  # one area of a larger country: say so


def short_name(n):
    for suffix in (" (US)", " (Australia)"):
        n = n.removesuffix(suffix)
    return n.removeprefix("Brazil ")


def adapt(d, r, now):
    """Reshape pipeline output into what index.html reads for one area."""
    b, last = d["backtest"], pd.Timestamp(d["lastObs"], unit="s", tz="UTC")
    test = dict(title="Every hour", when=f"{(last - pd.Timedelta(days=b['days'])):%d %b} to {last:%d %b %Y}", model=b["mape"], base=b["naive"])
    if "op" in b:
        test["op"] = b["op"]
    ahead = round((now - last) / pd.Timedelta(hours=1))
    drivers = {"Recent demand": d["drivers"]["Recent load"], "Calendar": d["drivers"]["Calendar"], "Weather": d["drivers"]["Weather"]}
    return dict(
        id="w:" + d["id"], kind="country", group=r["group"], name=d["name"], total=r.get("total", False), short=r.get("short"), lat=d["lat"], lon=d["lon"], tz=d["tz"], tzName="local time",
        op=d["op"], source=d["source"], about=r.get("about", f"{d['op']} · {d['source']}"), start=int(now.timestamp()), lastObs=d["lastObs"], peak=d["peak"],
        tests=[test], coverage=b["coverage"], coverageOf="actual hours in the backtest", mae=b["mae"], maeOf="hourly", lead=b["lead"], festivals=[],
        drivers={"Demand model": drivers}, histStart=d["histStart"], hist=d["hist"], histLabel="Reported hourly", peakStep=[None] * len(d["hist"]),
        p50=d["p50"], p10=d["p10"], p90=d["p90"], temp=d["temp"], btStart=d["btStart"], btActual=d["btActual"], btModel=d["btModel"], scatter=d["scatter"],
        ringT=d["temp"], ringV=d["p50"],
        fresh=[[f"{last:%d %b %H:%M} UTC", f"Latest hourly demand reading, {ahead} hours before this forecast starts"], [d["source"], "Where the demand data comes from"], ["Live", "Weather forecast from Open-Meteo"]],
        mainNote=f"Hourly demand as reported by {d['source']}, then a forecast built from the latest readings, the calendar and the weather. The axis does not start at zero. Click the forecast to move the timeline.",
        scoreNote=f"Average hourly error over the latest {b['days']} days, forecast {b['lead']} hours ahead, with the model refit every 30 days on earlier data only." + (" The grid operator's own forecast is shown where it is published." if "op" in b else "") + " Lower is better.",
        scatterNote="Hourly demand against temperature, past year. The ring is the selected hour.",
        btTitle="Backtest, last 14 days", btNote="Model output against reported demand.",
    )


def save(out, now):
    cc = {"w:" + r["id"]: r["cc"] for r in config.REGIONS}
    for x in out:
        x["cc"] = cc[x["id"]]
        x["name"] = NAME_FIX.get(x["id"].removeprefix("w:"), x["name"])
        x["country"] = COUNTRY.get(x["cc"], x["name"])
        x["short"] = x.get("short") or short_name(x["name"])
        x["p10"] = [min(a, b) for a, b in zip(x["p10"], x["p50"])]  # low never above expected, high never below
        x["p90"] = [max(a, b) for a, b in zip(x["p90"], x["p50"])]
    payload = dict(generated=int(pd.Timestamp.now(tz="UTC").timestamp()), start=int(now.timestamp()), horizon=config.HORIZON, regions=out)
    (OUT / "world.js").write_text("window.WORLD_DATA=" + json.dumps(payload, separators=(",", ":")) + ";\n")


def shard_of(areas, k, n):
    """The k-th of n roughly equal slices of the areas, so several machines can build them side by side."""
    return [r for i, r in enumerate(areas) if i % n == k]


def build_areas(areas, now, each=None):
    """Forecast each area. A missing or stale feed skips that area rather than losing the others."""
    out, failed = [], []
    for r in areas:
        try:
            d = model.build(r, now)
            out.append(adapt(d, r, now))
            b = d["backtest"]
            print(f"{r['id']:5} {r['name']:28} MAPE {b['mape']:5.2f}  last week {b['naive']:5.2f}  operator {b.get('op', '-')!s:>5}  band {b['coverage']:5.1f}%", flush=True)
        except Exception as e:
            failed.append(r["id"])
            print(f"{r['id']:5} {r['name']:28} SKIPPED {type(e).__name__}: {str(e)[:90]}", flush=True)
        if each:
            each(out)
    return out, failed


def merge(paths):
    """Combine the parts built by separate machines into world.js."""
    out = [x for path in paths for x in json.loads(Path(path).read_text())]
    assert out, "no parts to merge"
    ids = [x["id"] for x in out]
    assert len(ids) == len(set(ids)), "an area appears in two parts"
    save(out, pd.Timestamp(min(x["start"] for x in out), unit="s", tz="UTC"))
    print(f"wrote world.js: {len(out)} areas from {len(list(paths))} parts")


def main(argv=None):
    """forecast-world                     every area, writes world.js
    forecast-world FR DE USALL        only these areas, keeping the rest of world.js
    forecast-world --shard 2/6        only the third of six slices, writes world.part-2.json (for parallel machines)
    forecast-world --merge a.json ... combine parts into world.js"""
    args = list(sys.argv[1:] if argv is None else argv)
    now = feeds.now_hour()
    if args[:1] == ["--merge"]:
        return merge(args[1:])
    if args[:1] == ["--shard"]:
        k, n = (int(v) for v in args[1].split("/"))
        out, failed = build_areas(shard_of(config.REGIONS, k, n), now)
        part = OUT / f"world.part-{k}.json"
        part.write_text(json.dumps(out, separators=(",", ":")))
        print(f"wrote {part.name}: {len(out)} areas, skipped: {failed or 'none'}")
        return
    only = set(args)
    keep = []
    if only and (OUT / "world.js").exists():  # rerunning a few areas keeps the rest
        old = json.loads((OUT / "world.js").read_text().split("=", 1)[1].rstrip().rstrip(";"))["regions"]
        keep = [x for x in old if x["id"].removeprefix("w:") not in only]
    areas = [r for r in config.REGIONS if not only or r["id"] in only]
    out, failed = build_areas(areas, now, each=lambda done: save(keep + done, now))
    save(keep + out, now)
    print(f"wrote world.js: {len(keep) + len(out)} areas, skipped: {failed or 'none'}")


if __name__ == "__main__":
    main()
