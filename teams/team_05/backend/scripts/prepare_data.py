"""Entry point: prepare all data for the app (OSM -> basemap -> elevation -> graph.json).

    cd backend
    python -m scripts.prepare_data            # or: python scripts/prepare_data.py
    python -m scripts.prepare_data --synthetic   # offline synthetic city only

If OSM download fails (no internet / no osmnx) falls back to the synthetic city (unless --no-fallback).
"""
from __future__ import annotations

import argparse

try:
    from scripts import _common
    from scripts._common import log_exc, say
except ImportError:  # plain file run: python scripts/prepare_data.py
    import _common  # type: ignore
    from _common import log_exc, say  # type: ignore


def _step(n: int, total: int, title: str) -> None:
    say(f"\n[{n}/{total}] {title}")


def _run(name: str, fn) -> int:
    """Run a step; never let an exception escape without a logged traceback."""
    try:
        return fn([])
    except Exception as e:  # noqa: BLE001
        say(f"ОШИБКА в шаге «{name}»: {type(e).__name__}: {e}")
        log_exc(name)
        return 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--synthetic", action="store_true", help="только синтетические офлайн-данные")
    ap.add_argument("--no-fallback", action="store_true", help="не переключаться на синтетику при ошибке")
    ap.add_argument("--skip-elevation", action="store_true", help="не загружать высоты (плоский рельеф)")
    a = ap.parse_args(argv)

    from scripts import build_graph, fetch_elevation, fetch_osm, make_synthetic_data
    say("=== Снегоплан: подготовка данных ===")
    if a.synthetic:
        return _run('synthetic', make_synthetic_data.main)

    _step(1, 4, "Загрузка OpenStreetMap (дороги и слои базовой карты)")
    if _run('fetch_osm', fetch_osm.main) != 0:
        say("Не удалось загрузить OSM.")
        if a.no_fallback:
            return 1
        say("Переключаюсь на офлайн-данные (синтетический город).")
        return _run('synthetic', make_synthetic_data.main)

    _step(2, 4, "Загрузка высот (Open-Meteo)")
    if a.skip_elevation:
        say("Пропущено по флагу --skip-elevation.")
    elif _run('fetch_elevation', fetch_elevation.main) != 0:
        say("Высот нет — продолжаю с плоским рельефом.")

    _step(3, 4, "Сборка графа и задач")
    if _run('build_graph', build_graph.main) != 0:
        say("Не удалось собрать граф.")
        if a.no_fallback:
            return 1
        return _run('synthetic', make_synthetic_data.main)

    _step(4, 4, "Проверка")
    for p in (_common.GRAPH_PATH, _common.BASEMAP_PATH):
        say(f"  {'OK ' if p.exists() else 'НЕТ'} {p}")
    say("\nГотово! Запуск: uvicorn app.main:app --reload --port 8000")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException:  # noqa: BLE001
        log_exc("prepare_data")
        raise SystemExit(1)
