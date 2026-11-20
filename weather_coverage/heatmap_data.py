"""Normalize atlas responses and define the fixed visual encoding."""
from collections import Counter, defaultdict
from contextlib import closing
import csv
from datetime import datetime
import json
import shutil
from zoneinfo import ZoneInfo

from .experiment import coordinate_key, iso, numeric
from .heatmap_collect import OUT, ledger, point_plan, retained_responses

LOCAL = ZoneInfo("Asia/Kamchatka")
# Fixed across time, maps and video. The display percentage is computed in parsing.
FIELDS = {
    "temp": ("Температура", "°C", (-13, 5), ["#362869","#536cc4","#8fd9df","#f6d483","#dd7145"]),
    "feels_like": ("Ощущаемая температура", "°C", (-20, 4), ["#362869","#536cc4","#8fd9df","#f6d483","#dd7145"]),
    "wind_speed": ("Скорость ветра", "м/с", (0, 12), ["#215766","#51b9a6","#d9e477","#f79b58","#c65163"]),
    "wind_gust": ("Порывы ветра", "м/с", (0, 20), ["#215766","#51b9a6","#d9e477","#f79b58","#c65163"]),
    "wind_deg": ("Направление ветра", "°", (0, 360), ["#ecb45f","#dc6f90","#7e78cd","#49aba8","#ecb45f"]),
    "snow": ("Снег за 3 часа", "мм / 3 ч", (0, 7), ["#265365","#58b9ca","#a5dfef","#ccb8fb","#a47fdf"]),
    "rain": ("Дождь за 3 часа", "мм / 3 ч", (0, 7), ["#1d5264","#2599aa","#8de0bc","#ebdc83","#ee7955"]),
    "visibility": ("Видимость", "км", (0, 10), ["#e36e62","#f3b882","#dce6b0","#72c9c2","#3e8bab"]),
    "humidity": ("Влажность", "%", (45, 100), ["#e78e61","#e2be77","#91d2b6","#56aabf","#797de2"]),
    "clouds": ("Облачность", "%", (0, 100), ["#304a77","#5488ae","#77b9cf","#b2d0e0","#ecedf9"]),
    "pop": ("Вероятность осадков", "%", (0, 100), ["#1f4a69","#3798b2","#80d4c3","#efd980","#e88259"]),
    "precip": ("Суммарная интенсивность", "мм/ч", (0, 3), ["#275470","#52bfd1","#b0e7ef","#e9b0fd","#b487e7"]),
}
EXPORT = [
    "point_id","lat","lon","source_product","round","request_time_utc","forecast_dt_utc",
    "local_time_kamchatka","temp","feels_like","wind_speed","wind_gust","wind_deg","snow_3h",
    "rain_3h","current_snow_mm_h","current_rain_mm_h","visibility","humidity","clouds","pop",
    "precip_intensity_mm_h","weather_code","api_lat","api_lon","api_city_id","raw_file",
]
SOURCE_FIELDS = {
    "snow":"snow_3h","rain":"rain_3h","precip":"precip_intensity_mm_h",
}


def local_time(epoch):
    return datetime.fromtimestamp(epoch, LOCAL)


def export_csv(filename, columns, rows):
    with (OUT / filename).open("w",encoding="utf-8",newline="") as file:
        writer=csv.DictWriter(file,columns,extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def normalize(payload, point, product, round_id, request_time, raw_file):
    is_forecast = product=="forecast"
    site = payload.get("city",{}) if is_forecast else payload
    for item in payload.get("list",[]) if is_forecast else [payload]:
        epoch = item.get("dt")
        if not isinstance(epoch,int):
            continue
        main = item.get("main",{})
        wind = item.get("wind",{})
        snow = numeric(item.get("snow",{}).get("3h" if is_forecast else "1h"))
        rain = numeric(item.get("rain",{}).get("3h" if is_forecast else "1h"))
        yield dict(point_id=point["id"],lat=point["latitude"],lon=point["longitude"],
                   source_product="Forecast" if is_forecast else "Current",round=round_id,
                   request_time_utc=iso(request_time),forecast_dt_utc=iso(epoch),epoch=epoch,
                   local_time_kamchatka=local_time(epoch).isoformat(), temp=numeric(main.get("temp")),
                   feels_like=numeric(main.get("feels_like")),wind_speed=numeric(wind.get("speed")),
                   wind_gust=numeric(wind.get("gust")),wind_deg=numeric(wind.get("deg")),
                   snow_3h=snow if is_forecast else None,rain_3h=rain if is_forecast else None,
                   current_snow_mm_h=None if is_forecast else snow,
                   current_rain_mm_h=None if is_forecast else rain,
                   visibility=numeric(item.get("visibility")),humidity=numeric(main.get("humidity")),
                   clouds=numeric(item.get("clouds",{}).get("all")),pop=numeric(item.get("pop")),
                   precip_intensity_mm_h=(snow+rain)/3 if is_forecast and snow is not None and rain is not None
                   else None, weather_code=(item.get("weather") or [{}])[0].get("id"),
                   api_lat=numeric(site.get("coord",{}).get("lat")),
                   api_lon=numeric(site.get("coord",{}).get("lon")),api_city_id=numeric(site.get("id")),
                   raw_file=raw_file)


def shown_value(row, field):
    value=row[SOURCE_FIELDS.get(field,field)]
    if value is None:
        return None
    if field=="visibility":
        return value/1000
    if field=="pop":
        return value*100
    return value


def loaded():
    plan=point_plan()
    points={coordinate_key(point):point for point in plan}
    with closing(ledger()) as db:
        receipts=retained_responses(db)
        attempts=dict(db.execute("SELECT status, COUNT(*) FROM attempts GROUP BY status"))
    records=[]
    archive=[]
    seen=set()
    for round_id, product, coord, epoch, path in sorted(receipts,key=lambda item:-item[3]):
        if coord not in points or (product,coord) in seen:
            continue
        seen.add((product,coord))
        dest=OUT/"raw"/product/round_id/path.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        if path.resolve()!=dest.resolve() and not dest.exists():
            shutil.copyfile(path,dest)
        raw_file=str(dest.relative_to(OUT))
        archive.append(dict(round=round_id,product=product,coordinate=coord,request_epoch=epoch,
                            request_time_utc=iso(epoch),raw_file=raw_file))
        records.extend(normalize(json.loads(dest.read_bytes()),points[coord],product,round_id,epoch,raw_file))
    forecast=defaultdict(dict)
    for record in records:
        if record["source_product"]=="Forecast":
            forecast[record["point_id"]][record["epoch"]]=record
    if not forecast:
        raise ValueError("Нет сохранённых ответов Forecast.")
    # Every frame uses the same requested points and common exact valid time.
    valid_times=sorted(set.intersection(*(set(rows) for rows in forecast.values())))
    if not valid_times:
        raise ValueError("У всех сохранённых точек нет общего срока действия.")
    frames=[
        {**forecast[p["id"]][date],"frame_index":index}
        for index,date in enumerate(valid_times) for p in plan if p["id"] in forecast
    ]
    export_csv("raw_index.csv",["round","product","coordinate","request_epoch","request_time_utc","raw_file"],archive)
    export_csv("observations_full.csv",EXPORT,sorted(records,key=lambda r:(r["epoch"],r["point_id"])))
    export_csv("forecast_frames.csv",["frame_index",*EXPORT],frames)
    export_csv("point_metadata.csv",
               ["point_id","lat","lon","atlas_kind","district","terrain","inside_city","on_land",
                "cell_500m","elevation_m_dsm","elevation_source","osm","road","forecast_received",
                "current_received"],
               [{**p,"point_id":p["id"],"lat":p["latitude"],"lon":p["longitude"],
                 "cell_500m":p["cell_ids"]["500"],"forecast_received":("forecast",coord) in seen,
                 "current_received":("weather",coord) in seen} for coord,p in points.items()])
    plan=[{**point,"received":point["id"] in forecast} for point in plan]
    statistics={
        "attempts":attempts,"planned":len(plan),"received":len(forecast),
        "by_kind":dict(Counter(p["atlas_kind"] for p in plan if p["received"])),
        "planned_by_kind":dict(Counter(p["atlas_kind"] for p in plan)),
        "response_coord_count":len({(rows[valid_times[0]]["api_lat"],rows[valid_times[0]]["api_lon"]) for rows in forecast.values()}),
        "full_profile_count":len({tuple(shown_value(rows[date],f) for date in valid_times for f in FIELDS)
                                  for rows in forecast.values()}),
        "first_request":min(r["request_time_utc"] for r in frames),
        "last_request":max(r["request_time_utc"] for r in frames),
        "local_start":local_time(valid_times[0]).isoformat(),
        "local_end":local_time(valid_times[-1]).isoformat(),
        "frame_count":len(valid_times),
    }
    return plan, valid_times, frames, statistics
