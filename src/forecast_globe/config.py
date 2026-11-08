"""Settings and the list of areas that have a free hourly demand feed."""
import os
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]  # the project folder
CACHE = ROOT / "cache"                      # downloaded files, not in git
WEB = ROOT / "web"                          # the website; the pipelines write data.js and world.js here
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
