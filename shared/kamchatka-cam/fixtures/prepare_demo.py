"""Stage the four supplied control images locally. Scenes are hand-labelled demo fixtures."""

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path
import shutil


def build_manifest():
    at = datetime.fromisoformat("2026-11-21T03:15:24+12:00")

    def scene(falling, intensity, visibility, ground, drifts, road, evidence):
        return dict(schema_version="1", model="control-label", prompt_version="demo-1",
                    format_valid=True, view_quality="good", falling_snow=falling, intensity=intensity,
                    visibility=visibility, snow_on_ground=ground, large_snowdrifts=drifts,
                    road_coverage=road, evidence=evidence)

    cameras = [
        dict(id="zavoyko", name="Завойко · Елизово", area="Елизово", road=True),
        dict(id="moroznaya", name="Гора Морозная", area="Елизово", road=False),
        dict(id="heavy", name="Контрольный кадр · Сильный снегопад", area="Демо", road=True),
        dict(id="path", name="Контрольный кадр · Тротуар", area="Демо", road=False),
    ]
    labels = [
        scene("no", "none", "good", "no", "no", "clear", "Разметка и открытая проезжая часть хорошо видны."),
        scene("no", "none", "good", "yes", "uncertain", "not_visible", "Снег лежит на площадке. Признаки активного снегопада не видны."),
        scene("yes", "heavy", "poor", "uncertain", "uncertain", "not_visible", "Плотные полосы снега в воздухе ограничивают видимость. Проезжая часть не различима."),
        scene("yes", "light", "good", "yes", "uncertain", "not_visible", "Падает снег. Тротуар заснежен, проезжая часть вне кадра."),
    ]
    frames = [
        dict(camera_id=camera["id"], file=f"{index + 1}.jpg", at=(at + timedelta(minutes=index)).isoformat(), scene=labels[index])
        for index, camera in enumerate(cameras)
    ]
    frames.append({**frames[2], "at": (at + timedelta(minutes=2, seconds=20)).isoformat(), "synthetic_repeat": True})
    forecast = dict(point="Елизово", at=at.isoformat(), periods=[
        dict(at=(at + timedelta(hours=8)).isoformat(), horizon="hourly", probability=.72, temperature_c=-2,
             snow_mm=1.2, wind_mps=8, wind_deg=105, gust_mps=12, snow=True),
    ])
    return dict(cameras=cameras, frames=frames, forecast=[forecast])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("images", type=Path, nargs=4)
    parser.add_argument("--output", type=Path, default=Path("runtime/demo"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    for index, image in enumerate(args.images, 1):
        shutil.copyfile(image, args.output / f"{index}.jpg")
    (args.output / "manifest.json").write_text(json.dumps(build_manifest(), ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
