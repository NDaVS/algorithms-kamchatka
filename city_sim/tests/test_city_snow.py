from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from fastapi.testclient import TestClient
import numpy as np
from shapely.geometry import Point

from city_sim.api import make_app
from city_sim.city_snow import CitySnow, CitySnowRequest, administrative_boundary, remap
from city_sim.snow_experiment import SnowStudy
from city_sim.weather import WeatherArchive


class CitywideWeatherTests(unittest.TestCase):
    def test_historical_catalog_and_city_geometry(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            client = TestClient(make_app(db_path=root / "fixture.sqlite", weather_path=root))
            catalog = client.get("/api/v1/operations/city/catalog").json()
            self.assertEqual(catalog["openweather"], [])
            self.assertEqual({e["event_id"] for e in catalog["storms"]},
                             {"march-2025", "december-2025", "january-2026"})
            self.assertTrue(all(e["source_cells"] == 3 for e in catalog["storms"]))
            self.assertEqual(client.get("/api/v1/operations/city/runs/malformed").status_code, 404)
            self.assertEqual(client.post("/api/v1/operations/city/runs", json={"source": "openweather"}).status_code, 422)
        boundary = administrative_boundary()
        self.assertTrue(boundary.covers(Point(158.644, 53.034)))
        self.assertTrue(boundary.covers(Point(158.677, 52.958)))
        self.assertFalse(boundary.covers(Point(158.4, 53.0)))

    def test_archive_does_not_use_forecasts_collected_after_valid_time(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "forecast_frames.csv").write_text(
                "point_id,lon,lat,source_product,request_time_utc,forecast_dt_utc,wind_speed,wind_deg,snow_3h,api_lon,api_lat\n"
                "g500-1,158.644,53.034,Forecast,2026-11-20T05:50+00:00,2026-11-20T06:00+00:00,8,270,,158.64,53.03\n"
                "g500-1,158.644,53.034,Forecast,2026-11-20T06:01+00:00,2026-11-20T06:00+00:00,50,90,30,158.64,53.03\n"
            )
            runner = CitySnow(SnowStudy(None, WeatherArchive(root)), root)
            select, source = runner.weather(CitySnowRequest(valid_at="2026-11-20T06:00Z"))
            _, rows, meters = select((158.644, 53.034))
            self.assertEqual(meters, 0)
            self.assertEqual(rows[0]["wind_speed_ms"], 8)
            self.assertEqual(rows[0]["snowfall_cm"], 0)
            self.assertTrue(rows[0]["snow_field_omitted"])
            self.assertEqual(source["snapshot_points"], 1)
            self.assertEqual(source["provider_coordinates_reported"], 1)
            self.assertGreater(select((158.72, 53.034))[2], 3000)
            self.assertIsNone(select((159.0, 53.034)))

    def test_resampling_retains_mask_and_grid_order(self):
        sampled = remap([[1, 2], [float("nan"), 3]], 4)
        self.assertEqual(sampled[0, 0], 1)
        self.assertEqual(sampled[0, 3], 2)
        self.assertEqual(sampled[3, 3], 3)
        self.assertTrue(np.isnan(sampled[3, 0]))
