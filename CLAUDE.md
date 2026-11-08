# CLAUDE.md

Project guidance for Claude Code. Read the README for what the project does; this file holds what you need to work on it safely.

## What this is

Electricity Demand Forecast Globe: hour by hour demand forecasts (48 hours ahead) for India (5 grids, 33 states) and 41 other countries, shown on a three.js globe. Python builds `data.js` and `world.js`; `index.html` is a static page that reads them. Everything uses free public data and needs no API keys.

## Commands

```bash
pip install -e ".[dev]"
python -m forecast_globe.india           # India forecasts, writes web/data.js (about 5 min, then cached)
python -m forecast_globe.world           # all other countries, writes web/world.js (about an hour first time)
python -m forecast_globe.world FR USALL  # rebuild only these areas, keeping the rest of world.js
pytest                                   # all tests; must pass before any push
python -m http.server 8765 --directory web
```

## Layout

- `src/forecast_globe/config.py`: settings and the list of areas with a free hourly feed. `feeds.py`: cached downloader with retries and one reader per source. `model.py`: features, hourly model (`build`), range calibration (`calibrate`, `widen`).
- `src/forecast_globe/india.py`: India pipeline (daily level model times hourly shape model, festival effects, supply by fuel, grids, states). `world.py`: other countries and whole-country totals (`USALL`, `BRALL`, `AUALL`), tags each area with its country.
- `web/`: the static site. `index.html` is the whole front end (no framework, no build step), `land.js` the world outlines. `data.js` and `world.js` are generated: never edit them by hand, rerun the pipelines. They are committed so the site works before the first build.
- `data/`: hourly all-India record. `tests/`: leakage, logic, generated-data and page checks. `.github/workflows/update.yml`: daily rebuild and publish to GitHub Pages. `pyproject.toml`: dependencies, commands, pytest settings.

## Rules

- **No leakage.** Features may only use data that would exist at forecast time. The leakage tests enforce this; do not weaken them.
- **Honest numbers.** Accuracy and range coverage are measured on data the model did not see. Do not tune on the test window and report the result as clean. If a rule was chosen after seeing results, say so (the README already does for two rules).
- **Free and keyless.** Do not add paid services or required API keys. `EIA_API_KEY` is optional and only avoids rate limits.
- **Page copy is plain language** for readers with no data science background: explain terms, put technical charts under "For students". Keep the visual system: Geist, tokens in `:root`, spacing from the 2/4/8/12/16/24/32/40/48/64 steps, one easing curve, respect `prefers-reduced-motion`.
- **Do not commit** `cache/`, `.claude/` or `_site/` (see `.gitignore`). Do not commit or push without being asked.
- After changing page code, run `pytest` (it parses the page script) and recheck desktop and phone widths.

## Gotchas learned the hard way

- India's hours fall on :30 UTC, weather is on :00. `weather()` resamples; without it every hourly weather input is empty.
- `forecast_globe.world <IDs>` merges into the existing `world.js`; a full run rewrites it. World area ids are prefixed `w:` in the data (`w:FR`).
- Whole-country totals run the full model on summed regional demand (`load_sum`), they are not averages of regional results.
- In `model.build` the widening factor is `stretch`; do not reuse `m`, which is the model in the loop (this caused a crash once).
- LightGBM's scikit-learn interface needs `scikit-learn` in `pyproject.toml`; a test covers it.
- A downloaded JSON that is cut off must not be cached; `get()` validates it. On Windows a locked cache file may need a second delete attempt.
- The in-app browser throttles animation to about 1 frame per second, so it cannot judge smoothness or settled camera screenshots.
- The workflow runs India and 8 world slices in parallel (`forecast_globe.world --shard K/8`), then merges them (`--merge`). A first build is slow (downloads, then training); daily runs reuse the caches, which are saved even when a step fails. Logs: `gh run view <id> --repo Soham286/Electricity-Demand-Forecast-Globe --log-failed` (the `gh` tool is signed in on this machine).
