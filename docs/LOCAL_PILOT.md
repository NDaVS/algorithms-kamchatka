# Локальный расчётный пилот

Пилот планирует проходы плугов на ориентированном графе части Петропавловска-Камчатского. На одном снимке заданий он сравнивает приоритетный жадный алгоритм B0 и O1 на SciPy/HiGHS, проверяет маршруты и экспортирует JSON, GeoJSON и CSV. Учебный сценарий команд доступен отдельно через `starter.cli`.

## Запуск на Linux или macOS

Выполняйте команды из корня репозитория. Нужен Python 3.13. Дорожные выгрузки включены в репозиторий. После установки зависимостей расчёт выполняется офлайн.

```bash
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-core.txt
python -m unittest discover -s integration_tests -v
python -m snowops demo --case base_snowfall --output out/pkc-first --seed 42 --time-limit-s 30
python -m snowops validate out/pkc-first/base_snowfall/scenario.json out/pkc-first/base_snowfall/O1.plan.json
```

Для полного набора сценариев запустите `demo` без `--case`. В `metrics.csv` сохраняются результаты сравнения. Рядом лежат время и версии исполнения, аудит графа, JSON сценариев, планы и слои для карты. Код выхода `validate` равен 1, если найдено хотя бы одно нарушение.

Для собственного расчёта скопируйте сгенерированный `scenario.json` и используйте:

```bash
python -m snowops solve <сценарий.json> --output out/custom --method O1
python -m snowops.study --only scale --output out/pkc-scale --limit 18
```

У `snowops solve` свой OSM-файл указывается опцией `--osm` до подкоманды. Для масштабного исследования доступны `--only sensitivity`, `--only depots` и `--only operational-depot`. Для пользовательских рангов объектов применяется `--policy <файл.json>`.

Ресурсы техники и длительности работ заданы сценарно. Источником дорожной геометрии служит OpenStreetMap. [Контракт API](API_CONTRACT.md) · [Происхождение данных](DATA_PROVENANCE.md) · [Лицензии](THIRD_PARTY_LICENSES.md)
