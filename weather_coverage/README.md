# Погодные инструменты Петропавловска-Камчатского

Координаты берутся из OpenStreetMap. Карта отображает сетку, районы и пространственные различия между точками. Атлас дополнительно формирует тепловые слои, почасовой ползунок и видео. Снимки OSM для пересборки географии включены в репозиторий. [Источники выгрузок](osm/sources.json) · [Лицензия OSM](https://www.openstreetmap.org/copyright)

## Локальное демо

Команды выполняются из корня репозитория на Linux или macOS с Python 3.13:

```bash
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-core.txt -r weather_coverage/requirements.txt
python -m weather_coverage coordinates
python -m weather_coverage demo
python -m weather_coverage map --source demo
```

Откройте `weather_coverage/weather_coverage.html`. География и погодные строки создаются локально. Базовая карта Folium загружает Leaflet и подложку через интернет. Синтетические значения помечены в карте и сохраняются отдельно от измерений. Для просмотра [ноутбука](spatial_analysis.ipynb) используйте Jupyter после генерации демо. По умолчанию он читает демо-данные.

## Фактический пилот OpenWeather

Установите `OPENWEATHER_API_KEY` в окружении процесса, например через менеджер секретов. CLI использует бесплатные Current и Forecast. Предусмотрен общий лимит 100 попыток, постоянный локальный кэш и остановка при отказе авторизации или ограничении частоты.

```bash
python -m weather_coverage collect --round pilot-01
python -m weather_coverage map --source openweather
```

Сбор сохраняет сырые JSON и журнал в `weather_coverage/.state/`, а нормализованные строки — в `weather_coverage/observations.csv`. Для ноутбука установите `SOURCE = "openweather"`. [Возможности API](API_CAPABILITIES.md)

## Полный атлас

Выполните генерацию координат из первого блока. Для сбора и вывода атласа:

```bash
python -m weather_coverage.heatmap_collect --budget 300
python -m weather_coverage.heatmap_atlas --skip-media
```

Сбор выполняет до 298 запросов бесплатного Forecast по уникальным координатам городского плана. Текущий раунд хранится в `weather_coverage/heatmaps/`. Повтор команды использует его кэш. Перед сбором для другого погодного периода перенесите старый каталог в архив, например в `out/heatmaps-first`. Для HTML, PNG и MP4 установите FFmpeg с libx264 и выполните сборку без `--skip-media`. HTML атласа, CSV и медиа создаются в `weather_coverage/heatmaps/`. Сгенерированный HTML атласа работает без сети.

Для исследовательского архива прошлых двух дней можно после сборки Forecast явно вызвать Open-Meteo и дополнить карту:

```bash
python -m weather_coverage.heatmap_history
python -m weather_coverage.heatmap_atlas --skip-media
```

У Open-Meteo и OpenWeather разные источники и интервалы осадков. Атлас показывает источник и единицы для каждого срока. Архивные кадры и видео генерируются флагом `--history-media`. [Open-Meteo Historical Forecast](https://open-meteo.com/en/docs/historical-forecast-api)
